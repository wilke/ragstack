package drivers

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// RealElasticsearch is the ES surface `backup` and `restore --as` need: the
// readiness probe, the inventory, and the filesystem snapshot repository.
//
// The whole point of the driver is the VERDICT it insists on. ES answers 200
// to a snapshot that only partly succeeded, so a client that checks the status
// code alone records a bundle that cannot restore the data it claims to hold —
// the worst failure this verb has, because it is silent until the day somebody
// needs the backup. Every completion here is checked against the body: state
// SUCCESS, zero failed shards, and for a restore every shard accounted for.
type RealElasticsearch struct{ h *httpStores }

var _ jobs.Elasticsearch = (*RealElasticsearch)(nil)

var (
	// esRepoRe covers a repository and a snapshot name. Both are ctl-built
	// (`ctl-<bundle-id>`, `verify-<bundle-id>`) and both become directory
	// names under path.repo, so they are lowercase and path-safe.
	esRepoRe = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,127}$`)
	// A REPOSITORY name may carry uppercase (ES restricts only the file-name
	// characters); the ctl's `ctl-<ts>` repos carry the stamp's T and Z, and
	// the bundle manifest's schema pins that form. A SNAPSHOT name must be
	// lowercase — ES refuses "Invalid snapshot name, must be lowercase" — so
	// snapshots keep the stricter esRepoRe.
	esRepoNameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	// esIndexRe is looser only in length: an index name is the tenant's, not
	// the ctl's, and ragstack's are already of the form `<tenant>-chunks`.
	esIndexRe = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,254}$`)
)

// esRepoRoot is where the tenant's unit binds the snapshot directory inside
// the container, and the only location the ctl will register. ES itself
// refuses a location outside its `path.repo`, but the ctl refuses FIRST and
// with a sentence that names the rule, rather than passing an operator's typo
// to the cluster and quoting the 500 that comes back.
const esRepoRoot = "/usr/share/elasticsearch/snapshots/"

func checkESRepo(kind, name string) error {
	re := esRepoRe
	if kind == "repository" {
		re = esRepoNameRe
	}
	if !re.MatchString(name) {
		return fmt.Errorf("%w: %q is not a %s name the ctl will put in a path (want %s)",
			jobs.ErrRefused, name, kind, re)
	}
	return nil
}

func checkESIndex(name string) error {
	if !esIndexRe.MatchString(name) {
		return fmt.Errorf("%w: %q is not an index name the ctl will put in a path (want %s)",
			jobs.ErrRefused, name, esIndexRe)
	}
	return nil
}

// Ready waits for the cluster to reach at least yellow.
//
// Yellow rather than green because a single-node tenant cluster never goes
// green: every index has unassigned replicas by construction, and demanding
// green would mean no tenant on this host is ever ready.
func (e *RealElasticsearch) Ready(ctx context.Context, baseURL string) error {
	_, err := e.h.do(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet,
		path:   "/_cluster/health",
		query: url.Values{
			"wait_for_status": []string{"yellow"},
			"timeout":         []string{"30s"},
		},
		timeout: readyTimeout,
	})
	if err != nil {
		return fmt.Errorf("elasticsearch is not ready: %v", err)
	}
	return nil
}

// Indices lists the tenant's indices, sorted, with ES's own system indices
// (every name beginning with a dot: .security, .geoip_databases, .kibana…)
// left out. They are the cluster's, not the tenant's; a backup that snapshotted
// them would be capturing another tenant's credentials store on a shared node,
// and a restore that expected them would fail on a fresh one.
func (e *RealElasticsearch) Indices(ctx context.Context, baseURL string) ([]string, error) {
	var rows []struct {
		Index string `json:"index"`
	}
	if err := e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet,
		path:   "/_cat/indices",
		query:  url.Values{"format": []string{"json"}, "h": []string{"index"}},
	}, &rows); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		if r.Index == "" || strings.HasPrefix(r.Index, ".") {
			continue
		}
		out = append(out, r.Index)
	}
	sort.Strings(out)
	return out, nil
}

// Count is the document count of one index.
func (e *RealElasticsearch) Count(ctx context.Context, baseURL, index string) (int64, error) {
	if err := checkESIndex(index); err != nil {
		return 0, err
	}
	var body struct {
		Count int64 `json:"count"`
	}
	if err := e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method:  http.MethodGet,
		path:    "/" + url.PathEscape(index) + "/_count",
		timeout: listTimeout,
	}, &body); err != nil {
		return 0, err
	}
	return body.Count, nil
}

// RegisterRepo registers a filesystem snapshot repository at location.
func (e *RealElasticsearch) RegisterRepo(ctx context.Context, baseURL, repo, location string, readonly bool) error {
	if err := checkESRepo("repository", repo); err != nil {
		return err
	}
	// Absolute, clean and under the bound root — checked in that order so the
	// error names the rule that was broken rather than the last one.
	if !strings.HasPrefix(location, "/") {
		return fmt.Errorf("%w: a repository location must be absolute; %q is not", jobs.ErrRefused, location)
	}
	// Clean, so that `..` cannot be smuggled past the prefix check below by a
	// spelling ES would normalise and the ctl would not.
	if filepath.Clean(location) != location {
		return fmt.Errorf("%w: the repository location %q is not clean (want %q)",
			jobs.ErrRefused, location, filepath.Clean(location))
	}
	if !strings.HasPrefix(location+"/", esRepoRoot) {
		return fmt.Errorf("%w: a repository location must be under %s; %q is not",
			jobs.ErrRefused, esRepoRoot, location)
	}
	return e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodPut,
		path:   "/_snapshot/" + url.PathEscape(repo),
		body: map[string]any{
			"type": "fs",
			"settings": map[string]any{
				"location": location,
				"readonly": readonly,
			},
		},
		timeout: listTimeout,
	}, nil)
}

// UnregisterRepo drops the registration and leaves the files where they are —
// which is what lets the backup move the directory into the bundle afterwards.
//
// A 404 is success. This call is the rollback half of RegisterRepo, and a
// rollback that fails because the thing it was undoing is already undone turns
// one failed step into two.
func (e *RealElasticsearch) UnregisterRepo(ctx context.Context, baseURL, repo string) error {
	if err := checkESRepo("repository", repo); err != nil {
		return err
	}
	_, err := e.h.do(ctx, baseURL, storeOrigin, request{
		method:   http.MethodDelete,
		path:     "/_snapshot/" + url.PathEscape(repo),
		timeout:  listTimeout,
		okStatus: []int{http.StatusOK, http.StatusNotFound},
	})
	return err
}

// Snapshots lists the snapshot names a repository holds (GET
// _snapshot/{repo}/_all), sorted. A backup registers the copied repo
// read-only and lists it to prove the copy is a repository ES can read —
// the closest thing to a restore dry run ES offers.
func (e *RealElasticsearch) Snapshots(ctx context.Context, baseURL, repo string) ([]string, error) {
	if err := checkESRepo("repository", repo); err != nil {
		return nil, err
	}
	var body struct {
		Snapshots []struct {
			Snapshot string `json:"snapshot"`
		} `json:"snapshots"`
	}
	if err := e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet,
		path:   "/_snapshot/" + url.PathEscape(repo) + "/_all",
	}, &body); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(body.Snapshots))
	for _, sn := range body.Snapshots {
		if sn.Snapshot != "" {
			out = append(out, sn.Snapshot)
		}
	}
	sort.Strings(out)
	return out, nil
}

// esShards is the shard tally ES reports for a completed snapshot or restore.
type esShards struct {
	Total      int `json:"total"`
	Failed     int `json:"failed"`
	Successful int `json:"successful"`
}

// Snapshot takes a snapshot of every (non-system) index into repo.
func (e *RealElasticsearch) Snapshot(ctx context.Context, baseURL, repo, name string) error {
	if err := checkESRepo("repository", repo); err != nil {
		return err
	}
	if err := checkESRepo("snapshot", name); err != nil {
		return err
	}
	var body struct {
		Snapshot struct {
			Snapshot string   `json:"snapshot"`
			State    string   `json:"state"`
			Indices  []string `json:"indices"`
			Shards   esShards `json:"shards"`
			Failures []any    `json:"failures"`
		} `json:"snapshot"`
	}
	if err := e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodPut,
		path:   "/_snapshot/" + url.PathEscape(repo) + "/" + url.PathEscape(name),
		query:  url.Values{"wait_for_completion": []string{"true"}},
		body: map[string]any{
			// ignore_unavailable:false so an index that vanished between the
			// inventory and the snapshot FAILS rather than being quietly left
			// out of a bundle the manifest says is complete.
			"indices":              "*",
			"ignore_unavailable":   false,
			"include_global_state": false,
		},
		timeout: e.h.long,
	}, &body); err != nil {
		return err
	}
	s := body.Snapshot
	if s.State != "SUCCESS" || s.Shards.Failed != 0 {
		return fmt.Errorf("elasticsearch snapshot %s/%s finished in state %q with %d of %d shards failed (%d indices); a bundle is not written from a partial snapshot",
			repo, name, s.State, s.Shards.Failed, s.Shards.Total, len(s.Indices))
	}
	return nil
}

// Restore restores indices (all of the snapshot's when indices is empty).
func (e *RealElasticsearch) Restore(ctx context.Context, baseURL, repo, name string, indices []string) error {
	if err := checkESRepo("repository", repo); err != nil {
		return err
	}
	if err := checkESRepo("snapshot", name); err != nil {
		return err
	}
	want := "*"
	if len(indices) > 0 {
		for _, idx := range indices {
			if err := checkESIndex(idx); err != nil {
				return err
			}
		}
		want = strings.Join(indices, ",")
	}
	var body struct {
		Snapshot struct {
			Snapshot string   `json:"snapshot"`
			Indices  []string `json:"indices"`
			Shards   esShards `json:"shards"`
		} `json:"snapshot"`
	}
	if err := e.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodPost,
		path:   "/_snapshot/" + url.PathEscape(repo) + "/" + url.PathEscape(name) + "/_restore",
		query:  url.Values{"wait_for_completion": []string{"true"}},
		body: map[string]any{
			"indices":              want,
			"include_global_state": false,
		},
		timeout: e.h.long,
	}, &body); err != nil {
		// An unregistered repository or an unknown snapshot is a 404 and IS a
		// refusal: the restore was asked for something that is not there, which
		// no retry will change.
		if statusOf(err) == http.StatusNotFound {
			return fmt.Errorf("%w: elasticsearch has no snapshot %s/%s to restore: %v", jobs.ErrRefused, repo, name, err)
		}
		return err
	}
	s := body.Snapshot
	if s.Shards.Failed != 0 || s.Shards.Successful != s.Shards.Total || s.Shards.Total == 0 {
		return fmt.Errorf("elasticsearch restore of %s/%s recovered %d of %d shards with %d failed (%d indices); the restore is not complete",
			repo, name, s.Shards.Successful, s.Shards.Total, s.Shards.Failed, len(s.Indices))
	}
	return nil
}
