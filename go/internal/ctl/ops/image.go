package ops

// `fleet image prepare --sif PATH` — the operator act that admits an API
// SERVER image into the ctl's image store (PR-F §1.1).
//
// Building an image is an ops step on a checkout (network, git, Python,
// `apptainer build --sandbox`), never something the ctl does. What the ctl
// does is decide whether a built file may be RUN, and it decides from three
// independent witnesses that must all agree before anything is copied:
//
//   - the file itself: sha256(<sif>) == receipt.sha256;
//   - the receipt beside it (`<sif>.receipt.json`, written by the build script
//     and read by python/ragstack/tool_image.py in the same shape):
//     {name, version, commit, build, build_date, sha256};
//   - the image's own labels (`apptainer inspect --labels`):
//     org.ragstack.{version,commit,build} == the receipt's and
//     org.ragstack.role == server. They are read from the STORED copy (the
//     bytes the hash proved), after the copy; a disagreement fails the step
//     and the engine's rollback removes the copy before anything is recorded.
//
// And the commit must RESOLVE in the bare mirror, to itself: a tenant in image
// mode keeps its worktree at that commit (gowe render, env and the static UI
// build read it), so an image built from a commit nobody pushed would be an
// image no tenant could be moved onto. `+sha` dev builds are pushed first.
//
// It is CLI-only for the reason `artifact prepare` is: the image path is an
// argument, and a request saying "run the file at this path" does not belong
// on an HTTP surface. The verb is a job like any other (planned, locked,
// checkpointed, audited) and is absent from api/jobs.go's opVerbs.
//
// Plans are pure. Every host read — the receipt, the hash, the labels, the
// mirror — is a step, so `--dry-run` names what will be checked and the run
// says what was found.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The image labels the build script stamps (apptainer/ragstack-*.def).
const (
	LabelVersion = "org.ragstack.version"
	LabelCommit  = "org.ragstack.commit"
	LabelBuild   = "org.ragstack.build"
	LabelRole    = "org.ragstack.role"
	// RoleServer is the role label of an API server image (the tools image
	// says `worker`).
	RoleServer = "server"
)

// ReceiptSuffix is what the build script appends to an image's file name for
// its receipt: `ragstack-server-v1.6.6-b1.sif.receipt.json`.
const ReceiptSuffix = ".receipt.json"

// imageFileMode is a stored image's and receipt's mode: group-readable, NOT
// group-writable. A SIF anyone in the group could rewrite is a red doctor
// finding (the permission table), and the store is read by the daemon's
// account through the group.
const imageFileMode uint32 = 0o640

var (
	reImageSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)
	reImageCommit = regexp.MustCompile(`^[0-9a-f]{40}$`)
	reBuildSuffix = regexp.MustCompile(`-b([0-9]+)\.sif$`)
	// reImageSource is the charset an image SOURCE path may use: the safe path
	// charset plus `+`, which a `+sha` dev build's name carries.
	reImageSource = regexp.MustCompile(`^/[A-Za-z0-9._+/-]+$`)
)

// ImageReceipt is the receipt the build script writes beside an image. Build
// is a STRING in the files the script writes today ("1") and a number in a
// hand-written one; both decode.
type ImageReceipt struct {
	Name      string        `json:"name"`
	Version   string        `json:"version"`
	Commit    string        `json:"commit"`
	Build     receiptNumber `json:"build"`
	BuildDate string        `json:"build_date"`
	SHA256    string        `json:"sha256"`
}

// receiptNumber decodes the receipt's build: a string of decimal digits
// ("1", what the build script writes) or a bare JSON integer. Anything else —
// a sign, a fraction, a word — is refused.
type receiptNumber int

var reDigits = regexp.MustCompile(`^[0-9]{1,9}$`)

func (n *receiptNumber) UnmarshalJSON(b []byte) error {
	s := string(b)
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		s = s[1 : len(s)-1]
	}
	if !reDigits.MatchString(s) {
		return fmt.Errorf("build %s is not a string of decimal digits", b)
	}
	v, err := strconv.Atoi(s)
	if err != nil {
		return fmt.Errorf("build %s: %v", b, err)
	}
	*n = receiptNumber(v)
	return nil
}

// ParseImageReceipt decodes and checks a receipt for the image file named
// fileName. It is the receipt's own consistency, before anything is compared
// with the file or its labels: a name that is not this file, a build that is
// not the name's -b<N>, a commit that is not a full sha.
func ParseImageReceipt(data []byte, fileName string) (ImageReceipt, error) {
	var r ImageReceipt
	if err := json.Unmarshal(data, &r); err != nil {
		return r, fmt.Errorf("%w: the receipt is not the document the build script writes: %v", jobs.ErrRefused, err)
	}
	var bad []string
	if r.Name != fileName {
		bad = append(bad, fmt.Sprintf("its name is %q but the image file is %q", r.Name, fileName))
	}
	if !registry.ServerImageNamePattern().MatchString(fileName) {
		bad = append(bad, fmt.Sprintf("%q is not a server image name (%s)", fileName, registry.ServerImageNamePattern()))
	} else if m := reBuildSuffix.FindStringSubmatch(fileName); m != nil && m[1] != strconv.Itoa(int(r.Build)) {
		bad = append(bad, fmt.Sprintf("its build is %d but the name says b%s", r.Build, m[1]))
	}
	if r.Build < 1 {
		bad = append(bad, fmt.Sprintf("build %d is below 1", r.Build))
	}
	if strings.TrimSpace(r.Version) == "" {
		bad = append(bad, "it has no version")
	}
	if !reImageCommit.MatchString(r.Commit) {
		bad = append(bad, fmt.Sprintf("commit %q is not a full 40-hex sha", r.Commit))
	}
	if !reImageSHA256.MatchString(r.SHA256) {
		bad = append(bad, fmt.Sprintf("sha256 %q is not 64 hex characters", r.SHA256))
	}
	if len(bad) > 0 {
		return r, fmt.Errorf("%w: the receipt does not describe this image: %s", jobs.ErrRefused, strings.Join(bad, "; "))
	}
	return r, nil
}

// CheckImageLabels compares an image's labels with its receipt and requires
// the server role. Every disagreement is named.
func CheckImageLabels(labels map[string]string, r ImageReceipt) error {
	var bad []string
	for _, c := range []struct{ key, want string }{
		{LabelVersion, r.Version},
		{LabelCommit, r.Commit},
		{LabelBuild, strconv.Itoa(int(r.Build))},
		{LabelRole, RoleServer},
	} {
		if got, ok := labels[c.key]; !ok {
			bad = append(bad, fmt.Sprintf("%s is missing (want %q)", c.key, c.want))
		} else if got != c.want {
			bad = append(bad, fmt.Sprintf("%s is %q, the receipt says %q", c.key, got, c.want))
		}
	}
	if len(bad) > 0 {
		return fmt.Errorf("%w: the image's labels disagree with its receipt: %s", jobs.ErrRefused, strings.Join(bad, "; "))
	}
	return nil
}

func planImagePrepare(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockImages)
	f := p.oc.Fleet
	if f == nil {
		return fmt.Errorf("%w: image-prepare needs a registry snapshot", jobs.ErrValidation)
	}
	sif := argStringOf(args, "sif")
	if sif == "" {
		return fmt.Errorf("%w: image-prepare: sif is required", jobs.ErrValidation)
	}
	// The SOURCE may carry a `+` (a `+sha` dev build's name), which
	// paths.SafePath refuses; it is only ever READ, so it is held to the
	// image-name charset rather than the charset of paths the ctl writes.
	if !reImageSource.MatchString(sif) || filepath.Clean(sif) != sif {
		return p.refuse("sif %q is not an absolute, clean path of [A-Za-z0-9._+/-]", sif)
	}
	name := filepath.Base(sif)
	if !registry.ServerImageNamePattern().MatchString(name) {
		return p.refuse("%q is not a server image: the file name must match %s (what `apptainer/build-image.sh "+
			"--kind server` writes)", name, registry.ServerImageNamePattern())
	}
	mirror := argStringOf(args, "mirror")
	if mirror == "" {
		mirror = p.op.deps.Mirror
	}
	if mirror == "" {
		return p.refuse("no mirror is configured and none was given: `fleet image prepare` requires the image's " +
			"commit to resolve in the bare mirror (CTL_MIRROR, or --mirror)")
	}
	if _, err := paths.SafePath("/", mirror); err != nil {
		return p.refuse("mirror %q is not an absolute, clean path the ctl may read: %v", mirror, err)
	}
	// A name already recorded is answered from the registry snapshot, which is
	// a plan-time read of the REGISTRY (pure): the image store is immutable by
	// name, so preparing a name twice is refused whatever the file says. The
	// run half asks again under the locks.
	if rec, ok := f.ServerImages[name]; ok && rec != nil {
		return p.refuse("server image %s is already prepared (sha256 %s, commit %s, at %s). A prepared image is "+
			"immutable: build a new one (the build number is the -b<N> of its name)", name, rec.SHA256, rec.Commit, rec.Path)
	}

	receiptPath := sif + ReceiptSuffix
	store := p.oc.Roots.ServerImagesDir()
	dst := filepath.Join(store, registry.ServerImageFileName(name))
	dstReceipt := dst + ReceiptSuffix
	// Two names one stored file would serve (`…+x…` and `…-plus-x…`): the
	// second is refused before anything is read.
	for other, rec := range f.ServerImages {
		if rec != nil && rec.Path == dst {
			return p.refuse("server image %s would be stored as %s, which is already the file of %s",
				name, dst, other)
		}
	}

	// Filled by the steps, in order; a plan carries no host reads.
	var receipt ImageReceipt

	p.add(step{
		Kind: "fs", Title: "read the receipt beside the image", Targets: []string{receiptPath},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			data, err := sc.Ops.Drivers.Files().ReadFile(ctx, receiptPath)
			if err != nil {
				if errors.Is(err, fs.ErrNotExist) {
					return "", fmt.Errorf("%w: %s has no receipt beside it (%s): the build script writes one, and an "+
						"image without one cannot be identified", jobs.ErrRefused, sif, receiptPath)
				}
				return "", err
			}
			r, err := ParseImageReceipt(data, name)
			if err != nil {
				return "", err
			}
			if rec, ok := sc.Ops.Fleet.ServerImages[name]; ok && rec != nil && rec.SHA256 != r.SHA256 {
				return "", fmt.Errorf("%w: server image %s is already recorded with sha256 %s; this file's receipt says "+
					"%s. A name is never re-pointed at different bytes", jobs.ErrRefused, name, rec.SHA256, r.SHA256)
			}
			receipt = r
			p.result["version"], p.result["commit"], p.result["build"], p.result["sha256"] =
				r.Version, r.Commit, int(r.Build), r.SHA256
			return fmt.Sprintf("%s: version %s, commit %s, build %d", name, r.Version, r.Commit, r.Build), nil
		},
	})

	p.add(step{
		Kind: "probe", Title: "hash the image and compare it with the receipt", Targets: []string{sif},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			sum, size, err := sc.Ops.Drivers.Files().Sha256(ctx, sif)
			if err != nil {
				return "", err
			}
			if sum != receipt.SHA256 {
				return "", fmt.Errorf("%w: sha256(%s) is %s but its receipt says %s: the file is not the image the "+
					"receipt describes (rebuilt, truncated or swapped)", jobs.ErrRefused, sif, sum, receipt.SHA256)
			}
			return fmt.Sprintf("sha256 %s (%d bytes) matches the receipt", sum, size), nil
		},
	})

	p.add(step{
		Kind: "git", Title: fmt.Sprintf("require the image's commit to resolve in %s", mirror),
		Targets:  []string{mirror},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/git", "-C", mirror, "rev-parse", "--verify", "<receipt.commit>^{commit}"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			got, err := sc.Ops.Drivers.Git().ResolveRef(ctx, mirror, receipt.Commit)
			if err != nil {
				return "", fmt.Errorf("%w: commit %s (the image's) does not resolve in %s: %v. The commit must be "+
					"pushed to the mirror before its image is prepared — a tenant in image mode keeps its worktree at "+
					"that commit", jobs.ErrRefused, receipt.Commit, mirror, err)
			}
			if got != receipt.Commit {
				return "", fmt.Errorf("%w: %s resolved %s to %s; an image's commit must be a full sha in the mirror",
					jobs.ErrRefused, mirror, receipt.Commit, got)
			}
			return receipt.Commit + " is in the mirror", nil
		},
	})

	p.add(step{
		Kind: "fs", Title: "copy the image and its receipt into the image store (0640)", Targets: []string{store},
		WouldWrite: []model.WouldWrite{
			{Path: dst, Mode: "0640", Preview: model.NullString("")},
			{Path: dstReceipt, Mode: "0640", Preview: model.NullString("")},
		},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			if err := files.MkdirAll(ctx, store, dirMode); err != nil {
				return "", err
			}
			// An image already in the store under this name: the same bytes are
			// left alone (a re-run after a crash between this step and the
			// registry), different bytes are refused — the store never
			// overwrites an image a tenant may be running.
			switch sum, _, err := files.Sha256(ctx, dst); {
			case err == nil && sum == receipt.SHA256:
				sc.Logf("%s is already in the store with the same sha256; not copied again", dst)
			case err == nil:
				return "", fmt.Errorf("%w: %s already exists with sha256 %s, not %s; the store never overwrites an "+
					"image (remove it by hand if nothing records it)", jobs.ErrRefused, dst, sum, receipt.SHA256)
			case errors.Is(err, fs.ErrNotExist):
				// The PATHS are recorded before the copy, so a rollback (or an
				// operator after a crash) knows which files this job made.
				if err := sc.Checkpoint("file:"+dst, "file:"+dstReceipt); err != nil {
					return "", err
				}
				if err := files.CopyFile(ctx, sif, dst, imageFileMode); err != nil {
					return "", err
				}
			default:
				return "", err
			}
			if err := files.CopyFile(ctx, receiptPath, dstReceipt, imageFileMode); err != nil {
				return "", err
			}
			// The copy is hashed again: what the registry will record is the
			// store's file, and a short copy must not be recorded as the image.
			sum, _, err := files.Sha256(ctx, dst)
			if err != nil {
				return "", err
			}
			if sum != receipt.SHA256 {
				return "", fmt.Errorf("%w: the copy %s hashes to %s, not %s", jobs.ErrRefused, dst, sum, receipt.SHA256)
			}
			p.result["path"] = dst
			return dst, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var removed []string
			for _, id := range sc.Step.ExternalIDs {
				path, ok := strings.CutPrefix(id, "file:")
				if !ok {
					continue
				}
				if err := sc.Ops.Drivers.Files().Remove(ctx, path); err != nil && !errors.Is(err, fs.ErrNotExist) {
					return "", err
				}
				removed = append(removed, path)
			}
			if len(removed) == 0 {
				return "nothing was copied by this job", nil
			}
			return "removed " + strings.Join(removed, ", "), nil
		},
	})

	p.add(step{
		Kind: "apptainer", Title: "read the stored image's labels and compare them with the receipt (refused: the copy is rolled back)",
		Targets:  []string{dst},
		WouldRun: []model.WouldRun{{Argv: []string{"apptainer", "inspect", "--json", "--labels", dst}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The STORED copy is inspected: it is the file the hash step and the
			// copy's re-hash proved to be the receipt's bytes, and its path is in
			// the charset the instance driver accepts (a `+sha` source is not).
			labels, err := sc.Ops.Drivers.Instances().Labels(ctx, dst)
			if err != nil {
				return "", err
			}
			if err := CheckImageLabels(labels, receipt); err != nil {
				return "", err
			}
			return fmt.Sprintf("labels agree: %s, %s, build %d, role %s", receipt.Version, receipt.Commit,
				receipt.Build, RoleServer), nil
		},
	})

	p.add(step{
		Kind: "registry", Title: "record the server image in the registry", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the first server image recorded is a ONE-WAY DOOR: a ctl binary older than PR-F cannot " +
			"load a registry that carries `server_images` (install and selftest the new ctl first — PR-F D6)"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			cur := sc.Ops.Fleet
			if rec, exists := cur.ServerImages[name]; exists && rec != nil {
				return "", fmt.Errorf("%w: server image %s already exists", jobs.ErrRefused, name)
			}
			if cur.ServerImages == nil {
				cur.ServerImages = map[string]*registry.ServerImageRecord{}
			}
			cur.ServerImages[name] = &registry.ServerImageRecord{
				Version: receipt.Version, Commit: receipt.Commit, Build: int(receipt.Build),
				SHA256: receipt.SHA256, Path: dst,
				PreparedAt: p.stampRFC3339(sc), PreparedBy: sc.Job.Principal,
			}
			if err := save(cur); err != nil {
				delete(cur.ServerImages, name)
				if len(cur.ServerImages) == 0 {
					cur.ServerImages = nil
				}
				return "", err
			}
			sc.Logf("server image %s recorded at registry generation %d", name, cur.Generation)
			return name, nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "nothing was recorded", nil
			}
			delete(sc.Ops.Fleet.ServerImages, name)
			if len(sc.Ops.Fleet.ServerImages) == 0 {
				sc.Ops.Fleet.ServerImages = nil
			}
			return "removed the server image row " + name, save(sc.Ops.Fleet)
		},
	})

	p.result["name"] = name
	p.result["sif"] = sif
	p.result["mirror"] = mirror
	p.result["store"] = store
	p.warn("a prepared server image is immutable: its name is never re-pointed at different bytes")
	return nil
}
