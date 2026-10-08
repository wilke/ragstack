package ops

// The bundle check: the `checked` level (PR-G1.2).
//
// `verified` means a `restore --as` rebuilt a tenant from the bundle and its
// counts agreed — the strongest statement the control plane can make, and an
// expensive one: on a production tenant it allocates a port block and starts a
// second copy of a ~120 GB tenant. `checked` is the level below it, and it
// costs a read of the bundle: every file re-hashed against SHA256SUMS, the
// manifest validated against the contract, every leg record's files present,
// each store snapshot structurally what its store would write, and the qdrant
// counts the census taken under the fence. It is what `decommission` gates on
// (fenced AND (verified OR checked)); `migrate-local` still wants `verified`.
//
// One function does it — CheckBundle — and both callers use it: the backup
// job's own check step (through the job's drivers, before the API comes back
// up, so the census is still under the fence) and `ragstack-ctl backup verify`
// (through the real drivers, offline, with no census). A check that differed
// between the two would be two definitions of the same word.

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

// bundleManifestSchema is contracts/ctl/schemas/bundle_manifest.json, embedded
// so the binary can validate a manifest without the repository beside it.
// go:embed cannot reach outside this package, so it is a COPY, and
// TestTheEmbeddedManifestSchemaIsTheContract fails the build the moment the two
// differ — `cp contracts/ctl/schemas/bundle_manifest.json
// go/internal/ctl/ops/bundle_manifest.schema.json` is the fix.
//
//go:embed bundle_manifest.schema.json
var bundleManifestSchema []byte

// pgDumpMagic is the five bytes every pg_dump custom-format (-Fc) archive
// begins with.
const pgDumpMagic = "PGDMP"

// The legs of a check, in the order they run and are reported.
const (
	CheckLegManifest      = "manifest"
	CheckLegChecksums     = "sha256sums"
	CheckLegParts         = "parts"
	CheckLegQdrant        = "qdrant"
	CheckLegElasticsearch = "elasticsearch"
	CheckLegPostgres      = "postgres"
	CheckLegSQLite        = "sqlite"
)

// BundleCheckLeg is one leg's verdict, as `backup verify` prints it and the
// check step logs it.
type BundleCheckLeg struct {
	Leg    string `json:"leg"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

// BundleCheck is the whole answer.
type BundleCheck struct {
	Bundle string           `json:"bundle"`
	OK     bool             `json:"ok"`
	Fenced bool             `json:"fenced"`
	Files  int              `json:"files"`
	Legs   []BundleCheckLeg `json:"legs"`
}

// BundleCheckDrivers are the three drivers a check reads through. All three
// are reads here; the one write — `checked: true` into the manifest — is the
// caller's, after a check that passed.
type BundleCheckDrivers struct {
	Files   jobs.Files
	Archive jobs.Archive
	SQLite  jobs.SQLite
}

// QdrantCensus answers a collection's live point count. The backup step passes
// one while the tenant is still fenced; `backup verify` passes nil, because a
// count taken after the fence lifted says nothing about the bundle.
type QdrantCensus func(ctx context.Context, collection string) (int64, error)

// checkManifest is the subset of bundle_manifest.json the legs act on — the
// restore's own reading of it, so the two cannot disagree about a member.
type checkManifest = restoreManifest

// CheckBundle runs every leg against the finished bundle in dir, whose id is
// id. It returns the per-leg verdicts and, when any leg failed, the FIRST
// failure as a one-line jobs.ErrRefused naming the file or collection.
//
// A leg that fails does not stop the others (an operator reading `backup
// verify` wants every problem at once), except that nothing can be checked
// without a readable manifest.
func CheckBundle(ctx context.Context, d BundleCheckDrivers, dir, id string, census QdrantCensus) (BundleCheck, error) {
	res := BundleCheck{Bundle: id, Legs: []BundleCheckLeg{}}
	var first error
	record := func(leg, detail string, err error) {
		if err != nil {
			res.Legs = append(res.Legs, BundleCheckLeg{Leg: leg, OK: false, Detail: err.Error()})
			if first == nil {
				first = err
			}
			return
		}
		res.Legs = append(res.Legs, BundleCheckLeg{Leg: leg, OK: true, Detail: detail})
	}

	man, detail, err := checkManifestLeg(ctx, d.Files, dir, id)
	record(CheckLegManifest, detail, err)
	if man == nil {
		return res, first
	}
	res.Fenced = man.Fenced

	n, err := verifyBundleChecksums(ctx, d.Files, dir, id, man.SHA256Sums)
	res.Files = n
	record(CheckLegChecksums, fmt.Sprintf("%d file(s) match SHA256SUMS, nothing in the bundle is uncovered, and "+
		"SHA256SUMS matches the digest the manifest carries", n), err)

	detail, err = checkPartsLeg(ctx, d.Files, dir, id)
	record(CheckLegParts, detail, err)
	detail, err = checkQdrantLeg(ctx, d, dir, id, man, census)
	record(CheckLegQdrant, detail, err)
	detail, err = checkESLeg(ctx, d.Files, dir, id, man)
	record(CheckLegElasticsearch, detail, err)
	detail, err = checkPostgresLeg(ctx, d.Files, dir, id, man)
	record(CheckLegPostgres, detail, err)
	detail, err = checkSQLiteLeg(ctx, d.SQLite, dir, id, man)
	record(CheckLegSQLite, detail, err)

	res.OK = first == nil
	return res, first
}

// refusef is the one shape every leg failure takes: refused, naming the bundle.
func refusef(id, format string, a ...any) error {
	return fmt.Errorf("%w: bundle %s: %s", jobs.ErrRefused, id, fmt.Sprintf(format, a...))
}

// ---------------------------------------------------------------- manifest

func checkManifestLeg(ctx context.Context, files jobs.Files, dir, id string) (*checkManifest, string, error) {
	if strings.HasSuffix(id, partialSuffix) {
		return nil, "", refusef(id, "this is a `%s` directory: the backup that was writing it never finished, so "+
			"there is nothing to check. Take a new one", partialSuffix)
	}
	body, err := files.ReadFile(ctx, filepath.Join(dir, "manifest.json"))
	if err != nil {
		return nil, "", refusef(id, "no readable manifest.json in %s (%v)", dir, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(body, &doc); err != nil {
		return nil, "", refusef(id, "manifest.json is not JSON: %v", err)
	}
	var man checkManifest
	if err := json.Unmarshal(body, &man); err != nil {
		return nil, "", refusef(id, "manifest.json is not a manifest this build can read: %v", err)
	}
	if got, _ := doc["bundle_id"].(string); got != id {
		return &man, "", refusef(id, "the manifest calls this bundle %q; it is in a directory called %q. One of the "+
			"two moved, and a restore reading the manifest would look in the wrong place", got, id)
	}
	var missing []string
	for _, key := range BundleManifestRequired {
		if _, ok := doc[key]; !ok {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		return &man, "", refusef(id, "the manifest is missing %d required member(s): %s", len(missing),
			strings.Join(missing, ", "))
	}
	var schema map[string]any
	if err := json.Unmarshal(bundleManifestSchema, &schema); err != nil {
		return &man, "", fmt.Errorf("the embedded bundle_manifest.json is not JSON: %w", err)
	}
	if problems := validateManifestSchema(schema, doc); len(problems) > 0 {
		return &man, "", refusef(id, "manifest.json does not validate against bundle_manifest.json (%d problem(s)); "+
			"the first: %s", len(problems), problems[0])
	}
	return &man, fmt.Sprintf("manifest.json parses, bundle_id matches the directory, it carries all %d required "+
		"members and validates against bundle_manifest.json", len(BundleManifestRequired)), nil
}

// validateManifestSchema checks doc against schema with the structural subset
// bundle_manifest.json uses, and returns the problems sorted.
func validateManifestSchema(schema map[string]any, doc any) []string {
	v := &schemaChecker{root: schema}
	v.check(schema, doc, "$")
	sort.Strings(v.problems)
	return v.problems
}

// ---------------------------------------------------------------- parts

// partFile is one file a leg record names, and whether it must be non-empty.
type partFile struct {
	rel      string
	nonEmpty bool
}

// checkPartsLeg: every file a `parts/*.json` record names exists, and every one
// a record marks `included` (or `present`) is a non-empty regular file.
//
// The walk is GENERIC rather than per record type, so a leg added later is
// covered without anybody remembering this function: an object whose
// `included`/`present` is true vouches for its `file`, and a `files` list
// names files that must exist. Existence only for the lists — a config
// allowlist file can legitimately be empty — and non-empty for the store legs,
// where an empty snapshot, dump or database copy is a failed copy.
func checkPartsLeg(ctx context.Context, files jobs.Files, dir, id string) (string, error) {
	ents, err := files.ReadDir(ctx, filepath.Join(dir, partsDir))
	if err != nil {
		return "", refusef(id, "%s/ cannot be listed (%v): the leg records are what the manifest was built from",
			partsDir, err)
	}
	var named []partFile
	nParts := 0
	for _, e := range ents {
		if e.IsDir || !strings.HasSuffix(e.Name, ".json") {
			continue
		}
		rel := filepath.Join(partsDir, e.Name)
		body, err := files.ReadFile(ctx, filepath.Join(dir, rel))
		if err != nil {
			return "", refusef(id, "%s cannot be read: %v", rel, err)
		}
		var doc any
		if err := json.Unmarshal(body, &doc); err != nil {
			return "", refusef(id, "%s is not JSON: %v", rel, err)
		}
		nParts++
		walkPartFiles(doc, false, &named)
	}
	seen := map[string]bool{}
	checked := 0
	for _, f := range named {
		if seen[f.rel] && !f.nonEmpty {
			continue
		}
		seen[f.rel] = true
		clean := filepath.Clean(f.rel)
		if filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, "../") {
			return "", refusef(id, "a leg record names %q, which is not a path inside the bundle", f.rel)
		}
		st, err := files.Stat(ctx, filepath.Join(dir, clean))
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return "", refusef(id, "%s is named by a leg record and is not in the bundle", clean)
		case err != nil:
			return "", refusef(id, "%s cannot be examined: %v", clean, err)
		case st.IsDir || st.IsSymlink:
			return "", refusef(id, "%s is named by a leg record and is not a regular file", clean)
		case f.nonEmpty && st.Size == 0:
			return "", refusef(id, "%s is EMPTY, and its leg record says it was included", clean)
		}
		checked++
	}
	return fmt.Sprintf("%d leg record(s) name %d file(s); every one is present and every included one is non-empty",
		nParts, checked), nil
}

func walkPartFiles(node any, on bool, out *[]partFile) {
	switch v := node.(type) {
	case map[string]any:
		if b, ok := v["included"].(bool); ok {
			on = b
		}
		if b, ok := v["present"].(bool); ok {
			on = b
		}
		if f, ok := v["file"].(string); ok && f != "" && on {
			*out = append(*out, partFile{rel: f, nonEmpty: true})
		}
		if list, ok := v["files"].([]any); ok {
			for _, item := range list {
				switch it := item.(type) {
				case string:
					*out = append(*out, partFile{rel: it})
				case map[string]any:
					if f, ok := it["file"].(string); ok && f != "" {
						*out = append(*out, partFile{rel: f})
					}
				}
			}
		}
		for k, child := range v {
			if k == "files" {
				continue
			}
			walkPartFiles(child, on, out)
		}
	case []any:
		for _, child := range v {
			walkPartFiles(child, on, out)
		}
	}
}

// ---------------------------------------------------------------- qdrant

// checkQdrantLeg: every included collection's snapshot is a readable tar with
// entries; its manifest counts are the ones the leg recorded (parts/qdrant.json
// is under SHA256SUMS and the manifest is not, so this is what catches an
// edited manifest); and, under a fence, `points_after` is the live census.
func checkQdrantLeg(ctx context.Context, d BundleCheckDrivers, dir, id string, man *checkManifest,
	census QdrantCensus) (string, error) {
	cols := man.Stores.Qdrant.Collections
	included := 0
	for _, c := range cols {
		if c.Included {
			included++
		}
	}
	if included == 0 {
		return "no collection snapshot is in this bundle", nil
	}
	var part qdrantPart
	body, err := d.Files.ReadFile(ctx, filepath.Join(dir, partsDir, "qdrant.json"))
	if err != nil {
		return "", refusef(id, "the manifest lists %d collection snapshot(s) and %s/qdrant.json, the leg's own "+
			"record of them, cannot be read: %v", included, partsDir, err)
	}
	if err := json.Unmarshal(body, &part); err != nil {
		return "", refusef(id, "%s/qdrant.json is not the qdrant leg's record: %v", partsDir, err)
	}
	recorded := map[string]qdrantCollection{}
	for _, c := range part.Collections {
		recorded[c.Name] = c
	}
	entries := 0
	for _, c := range cols {
		if !c.Included {
			continue
		}
		if c.File == nil || *c.File == "" {
			return "", refusef(id, "collection %s is included and names no snapshot file", c.Name)
		}
		n, err := d.Archive.Entries(ctx, filepath.Join(dir, *c.File))
		if err != nil {
			return "", refusef(id, "collection %s: %s is not a readable snapshot: %v", c.Name, *c.File, err)
		}
		if n == 0 {
			return "", refusef(id, "collection %s: %s is a tar with no entries", c.Name, *c.File)
		}
		entries += n
		rec, ok := recorded[c.Name]
		if !ok {
			return "", refusef(id, "collection %s is in the manifest and not in the qdrant leg's own record", c.Name)
		}
		if rec.PointsAfter != c.PointsAfter || rec.PointsBefore != c.PointsBefore {
			return "", refusef(id, "collection %s: the manifest says %d→%d points and the leg recorded %d→%d",
				c.Name, c.PointsBefore, c.PointsAfter, rec.PointsBefore, rec.PointsAfter)
		}
		if census != nil {
			live, err := census(ctx, c.Name)
			if err != nil {
				return "", refusef(id, "collection %s: the census under the fence failed: %v", c.Name, err)
			}
			if live != c.PointsAfter {
				return "", refusef(id, "collection %s: the manifest's points_after is %d and the census under the "+
					"fence counts %d", c.Name, c.PointsAfter, live)
			}
		}
	}
	how := "agree with the leg's record"
	if census != nil {
		how += " and with the census under the fence"
	}
	return fmt.Sprintf("%d collection snapshot(s), %d tar entries; the counts %s", included, entries, how), nil
}

// ---------------------------------------------------------------- elasticsearch

// esRepoRoot is the part of elasticsearch's `index-N` this check reads: the
// map from index NAME to the repository's directory id for it.
type esRepoRoot struct {
	Indices map[string]struct {
		ID string `json:"id"`
	} `json:"indices"`
}

// checkESLeg: the bundle's repository directory holds `index-N` — the JSON
// root elasticsearch writes last, after every blob it names — and a non-empty
// `indices/<id>/` for every index the manifest includes, where <id> is the
// one that root assigns the index (the directory is named by id, not by name).
func checkESLeg(ctx context.Context, files jobs.Files, dir, id string, man *checkManifest) (string, error) {
	if man.esRepo() == "" {
		return "the elasticsearch leg is not in this bundle", nil
	}
	rel := filepath.Join("elasticsearch", "snapshots", id)
	repo := filepath.Join(dir, rel)
	ents, err := files.ReadDir(ctx, repo)
	if err != nil {
		return "", refusef(id, "%s/ cannot be listed: %v", rel, err)
	}
	gen := -1
	for _, e := range ents {
		rest, ok := strings.CutPrefix(e.Name, "index-")
		if !ok || e.IsDir {
			continue
		}
		if n, err := strconv.Atoi(rest); err == nil && n > gen {
			gen = n
		}
	}
	if gen < 0 {
		return "", refusef(id, "%s/ holds no index-* file: it is not an elasticsearch repository, or its root "+
			"was never written", rel)
	}
	rootRel := filepath.Join(rel, "index-"+strconv.Itoa(gen))
	body, err := files.ReadFile(ctx, filepath.Join(dir, rootRel))
	if err != nil {
		return "", refusef(id, "%s cannot be read: %v", rootRel, err)
	}
	var root esRepoRoot
	if err := json.Unmarshal(body, &root); err != nil {
		return "", refusef(id, "%s is not the JSON repository root elasticsearch writes: %v", rootRel, err)
	}
	n := 0
	for _, idx := range man.includedIndices() {
		entry, ok := root.Indices[idx]
		if !ok || entry.ID == "" {
			return "", refusef(id, "index %s is in the manifest and not in %s", idx, rootRel)
		}
		idxRel := filepath.Join(rel, "indices", entry.ID)
		sub, err := files.ReadDir(ctx, filepath.Join(dir, idxRel))
		if err != nil || len(sub) == 0 {
			return "", refusef(id, "index %s: %s/ is missing or empty", idx, idxRel)
		}
		n++
	}
	return fmt.Sprintf("%s holds index-%d and a directory for each of %d included index(es)", rel, gen, n), nil
}

// ---------------------------------------------------------------- postgres

func checkPostgresLeg(ctx context.Context, files jobs.Files, dir, id string, man *checkManifest) (string, error) {
	pg := man.Stores.Postgres
	if !pg.Included || pg.File == nil || *pg.File == "" {
		return "no postgres dump is in this bundle (kind " + orDefault(pg.Kind, "unknown") + ")", nil
	}
	head, err := files.ReadHead(ctx, filepath.Join(dir, *pg.File), len(pgDumpMagic))
	if err != nil {
		return "", refusef(id, "%s cannot be read: %v", *pg.File, err)
	}
	if string(head) != pgDumpMagic {
		return "", refusef(id, "%s does not begin with pg_dump's custom-format header (%q): it is not a `pg_dump "+
			"-Fc` archive", *pg.File, pgDumpMagic)
	}
	return *pg.File + " carries pg_dump's custom-format header", nil
}

// ---------------------------------------------------------------- sqlite

func checkSQLiteLeg(ctx context.Context, db jobs.SQLite, dir, id string, man *checkManifest) (string, error) {
	if len(man.SQLite) == 0 {
		return "no SQLite copy is in this bundle", nil
	}
	for _, e := range man.SQLite {
		verdict, err := db.IntegrityCheck(ctx, filepath.Join(dir, e.File))
		if err != nil {
			return "", refusef(id, "%s: %v", e.File, err)
		}
		if verdict != "ok" {
			return "", refusef(id, "%s: PRAGMA integrity_check answered %q", e.File, verdict)
		}
	}
	return fmt.Sprintf("%d SQLite copy/copies pass PRAGMA integrity_check", len(man.SQLite)), nil
}

// ---------------------------------------------------------------- marking

// setManifestFlag rewrites ONE boolean member of a bundle manifest, leaving
// every other member exactly as the bundle wrote it — a generic map, not this
// build's struct, because a manifest written by a later version must survive
// being marked by this one. SHA256SUMS does not cover manifest.json (the
// manifest carries ITS digest), so marking leaves every checksum true.
func setManifestFlag(ctx context.Context, files jobs.Files, path, member string, value bool) error {
	body, err := files.ReadFile(ctx, path)
	if err != nil {
		return err
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		return fmt.Errorf("%w: %s is not readable as JSON: %v", jobs.ErrRefused, path, err)
	}
	man[member] = value
	out, err := json.MarshalIndent(man, "", "  ")
	if err != nil {
		return err
	}
	return files.WriteAtomic(ctx, path, append(out, '\n'), 0o640)
}

// MarkBundleChecked sets `checked` in the manifest of the bundle in dir. It is
// what `backup verify` calls after a CheckBundle that passed.
func MarkBundleChecked(ctx context.Context, files jobs.Files, dir string) error {
	return setManifestFlag(ctx, files, filepath.Join(dir, "manifest.json"), "checked", true)
}

// ---------------------------------------------------------------- the step

// addBundleCheck is the backup's own deep check of the bundle it has just
// finished, run BEFORE the fence lifts so the qdrant census is still a count
// of a store nothing is writing to. On success the manifest says `checked:
// true` (and the registry record that follows says so too); on failure the job
// fails with the first problem, naming the file or collection, and rolls back
// like any failed backup — the bundle goes back to `<id>.partial`, the API
// comes back up and `last_backup` keeps naming the previous recovery point.
func (p *planner) addBundleCheck(bundleDir string, fence bool, scope scopeSet) {
	q := p.t.Stores.Qdrant
	path := filepath.Join(bundleDir, "manifest.json")
	warnings := []string{"read-only until it passes: every file re-hashed, the manifest validated against " +
		"bundle_manifest.json, the leg records' files present, each qdrant snapshot read as a tar, the " +
		"elasticsearch repository's index-* and per-index directories, the postgres dump's header and every " +
		"SQLite copy's integrity_check; then `checked: true` in the manifest"}
	live := fence && scope.has(scopeStores) && q.Capabilities.Snapshot
	if live {
		warnings = append(warnings, "the qdrant counts are re-taken now, under the fence, and must equal the "+
			"manifest's points_after")
	} else {
		warnings = append(warnings, "this bundle is not a fenced store bundle, so there is no census to compare "+
			"the qdrant counts with; they are compared with the leg's own record only")
	}
	p.addFor("files", step{
		Kind: "backup", Title: "check the bundle: hashes, manifest, snapshot structure, counts",
		Targets:    []string{bundleDir},
		WouldWrite: []model.WouldWrite{{Path: path, Mode: "0640", Preview: model.NullString("")}},
		Warnings:   warnings,
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			id := p.bundleID(sc)
			drv := sc.Ops.Drivers
			var census QdrantCensus
			if live {
				url := q.URL
				census = func(ctx context.Context, c string) (int64, error) { return drv.Qdrant().Count(ctx, url, c) }
			}
			res, cerr := CheckBundle(ctx, BundleCheckDrivers{Files: drv.Files(), Archive: drv.Archive(),
				SQLite: drv.SQLite()}, dir, id, census)
			for _, l := range res.Legs {
				verdict := "ok"
				if !l.OK {
					verdict = "FAIL"
				}
				sc.Logf("%s %s: %s", verdict, l.Leg, l.Detail)
			}
			if cerr != nil {
				return "", cerr
			}
			if err := MarkBundleChecked(ctx, drv.Files(), dir); err != nil {
				return "", fmt.Errorf("marking bundle %s checked: %w", id, err)
			}
			return fmt.Sprintf("bundle %s checked: %d file(s) re-hashed, %d leg(s) passed", id, res.Files,
				len(res.Legs)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			err = setManifestFlag(ctx, sc.Ops.Drivers.Files(), filepath.Join(dir, "manifest.json"), "checked", false)
			if errors.Is(err, fs.ErrNotExist) {
				return "no manifest to unmark", nil
			}
			if err != nil {
				return "", err
			}
			return "the manifest says checked=false again", nil
		},
	})
}

// ---------------------------------------------------------------- the schema subset

// schemaChecker is the subset of JSON Schema bundle_manifest.json uses.
type schemaChecker struct {
	root     map[string]any
	problems []string
}

func (v *schemaChecker) fail(path, format string, a ...any) {
	v.problems = append(v.problems, path+": "+fmt.Sprintf(format, a...))
}

// resolve follows a local `$ref` ("#/$defs/RelPath"). A cross-file ref is not
// resolvable here and answers nil, which the caller treats as "unchecked".
func (v *schemaChecker) resolve(ref string) map[string]any {
	if !strings.HasPrefix(ref, "#/") {
		return nil
	}
	node := any(v.root)
	for _, seg := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		m, ok := node.(map[string]any)
		if !ok {
			return nil
		}
		node = m[seg]
	}
	out, _ := node.(map[string]any)
	return out
}

func (v *schemaChecker) check(schema map[string]any, doc any, path string) {
	if ref, ok := schema["$ref"].(string); ok {
		if target := v.resolve(ref); target != nil {
			v.check(target, doc, path)
		}
		return
	}
	if opts, ok := schema["oneOf"].([]any); ok {
		// oneOf here is always "<something> or null".
		if doc == nil {
			return
		}
		for _, o := range opts {
			if m, ok := o.(map[string]any); ok && m["type"] != "null" {
				v.check(m, doc, path)
				return
			}
		}
		return
	}
	// `"type": ["string", "null"]` — the contract's other way of spelling
	// nullable, beside oneOf. A null under such a schema is VALID and must not
	// be type-checked against the non-null half, which is what a light bundle's
	// excluded elasticsearch leg (repo: null, snapshot: null) is.
	if doc == nil && nullable(schema) {
		return
	}
	if c, ok := schema["const"]; ok && !sameJSON(c, doc) {
		v.fail(path, "is %v, the contract says %v", doc, c)
	}
	if enum, ok := schema["enum"].([]any); ok {
		found := false
		for _, e := range enum {
			if sameJSON(e, doc) {
				found = true
			}
		}
		if !found {
			v.fail(path, "is %v, outside the enum %v", doc, enum)
		}
	}
	switch typeOf(schema) {
	case "object":
		m, ok := doc.(map[string]any)
		if !ok {
			v.fail(path, "is %T, the contract says object", doc)
			return
		}
		props, _ := schema["properties"].(map[string]any)
		for _, req := range stringsOf(schema["required"]) {
			if _, ok := m[req]; !ok {
				v.fail(path, "is missing the required member %q", req)
			}
		}
		if extra, ok := schema["additionalProperties"].(bool); ok && !extra {
			for k := range m {
				if _, known := props[k]; !known {
					v.fail(path, "carries %q, which the contract does not allow", k)
				}
			}
		}
		for k, sub := range props {
			child, ok := m[k]
			if !ok {
				continue
			}
			if subSchema, ok := sub.(map[string]any); ok {
				v.check(subSchema, child, path+"."+k)
			}
		}
	case "array":
		items, ok := doc.([]any)
		if !ok {
			v.fail(path, "is %T, the contract says array", doc)
			return
		}
		sub, _ := schema["items"].(map[string]any)
		if sub == nil {
			return
		}
		for i, child := range items {
			v.check(sub, child, fmt.Sprintf("%s[%d]", path, i))
		}
	case "string":
		s, ok := doc.(string)
		if !ok {
			v.fail(path, "is %T, the contract says string", doc)
			return
		}
		if pat, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pat)
			switch {
			case err != nil:
				v.fail(path, "the contract's pattern %s does not compile: %v", pat, err)
			case !re.MatchString(s):
				v.fail(path, "%q does not match %s", s, pat)
			}
		}
		if min, ok := schema["minLength"].(float64); ok && len(s) < int(min) {
			v.fail(path, "%q is shorter than %v", s, min)
		}
	case "integer", "number":
		n, ok := doc.(float64)
		if !ok {
			v.fail(path, "is %T, the contract says a number", doc)
			return
		}
		if min, ok := schema["minimum"].(float64); ok && n < min {
			v.fail(path, "%v is under the minimum %v", n, min)
		}
	case "boolean":
		if _, ok := doc.(bool); !ok {
			v.fail(path, "is %T, the contract says boolean", doc)
		}
	}
}

// typeOf reads `type`, which may be a string or a list ("string"|null).
// nullable reports whether the schema's `type` list admits null.
func nullable(schema map[string]any) bool {
	list, ok := schema["type"].([]any)
	if !ok {
		return false
	}
	for _, one := range list {
		if s, ok := one.(string); ok && s == "null" {
			return true
		}
	}
	return false
}

func typeOf(schema map[string]any) string {
	switch t := schema["type"].(type) {
	case string:
		return t
	case []any:
		for _, one := range t {
			if s, ok := one.(string); ok && s != "null" {
				return s
			}
		}
	}
	return ""
}

func stringsOf(v any) []string {
	items, _ := v.([]any)
	out := make([]string, 0, len(items))
	for _, i := range items {
		if s, ok := i.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func sameJSON(a, b any) bool {
	ja, _ := json.Marshal(a)
	jb, _ := json.Marshal(b)
	return string(ja) == string(jb)
}
