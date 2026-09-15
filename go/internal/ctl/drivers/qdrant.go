package drivers

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// RealQdrant is the qdrant REST surface the ctl needs, and nothing else.
//
// Every collection and snapshot name this driver is given becomes a path
// segment, and one of them — the snapshot name — comes back from the STORE
// rather than from the ctl. So both are validated against a path-safe pattern
// on the way in AND on the way out: a snapshot called `../../etc` would
// otherwise be a store telling the ctl where to write.
type RealQdrant struct{ h *httpStores }

var _ jobs.Qdrant = (*RealQdrant)(nil)

var (
	// qdrantCollectionRe is deliberately narrower than qdrant's own rule. The
	// collections the ctl ever names are the ones a ragstack tenant creates,
	// and every character outside this set is one that has to survive a URL
	// path, a filesystem path inside the container, and a tar entry in a
	// bundle unchanged.
	qdrantCollectionRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	qdrantSnapshotRe   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`)
)

// snapshotLocationPrefix is the only place a recover may read from. The
// location is a path INSIDE the container, bound there by the tenant's unit;
// accepting an arbitrary URL would let a registry row (or a bundle manifest)
// point the store at a file — or an HTTP endpoint — nobody approved, and
// qdrant would fetch it with the store's own privileges.
const snapshotLocationPrefix = "file:///qdrant/snapshots/"

func checkCollection(name string) error {
	if !qdrantCollectionRe.MatchString(name) {
		return fmt.Errorf("%w: %q is not a collection name the ctl will put in a path (want %s)",
			jobs.ErrRefused, name, qdrantCollectionRe)
	}
	return nil
}

func checkSnapshotName(name string) error {
	if !qdrantSnapshotRe.MatchString(name) {
		return fmt.Errorf("%w: %q is not a snapshot name the ctl will put in a path (want %s)",
			jobs.ErrRefused, name, qdrantSnapshotRe)
	}
	return nil
}

// waitTrue is the query every mutating snapshot call carries. Without it
// qdrant answers as soon as the operation is ACCEPTED, and the backup would
// rename a file that is still being written.
func waitTrue() url.Values { return url.Values{"wait": []string{"true"}} }

// Ready is GET /readyz, falling back to GET /collections on a 404 and ONLY on
// a 404.
//
// The fallback is not belt and braces: /readyz arrived in qdrant 1.9 and the
// images a long-lived tenant pins may predate it, where it answers 404. A 404
// from /readyz is therefore "this store does not have that endpoint", not
// "this store is not ready" — and /collections answering 200 is the older
// proof of the same fact.
//
// Every OTHER answer from /readyz is the answer, and nothing overrides it.
// Falling back on any error made the probe report READY for a store that had
// just said 503 "not ready": qdrant serves /collections while it is still
// loading segments, so the fallback contradicted the one endpoint whose whole
// job is to say it is not up yet, and a job gated on readiness went ahead
// against a store that was still coming up.
func (q *RealQdrant) Ready(ctx context.Context, baseURL string) error {
	_, err := q.h.do(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet, path: "/readyz", timeout: listTimeout,
	})
	if err == nil {
		return nil
	}
	if statusOf(err) != http.StatusNotFound {
		return fmt.Errorf("qdrant is not ready: %v", err)
	}
	if _, fb := q.h.do(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet, path: "/collections", timeout: listTimeout,
	}); fb != nil {
		return fmt.Errorf("qdrant is not ready: %v (and the /collections fallback, for a store too old to have /readyz: %v)", err, fb)
	}
	return nil
}

// Collections lists the collection names, sorted.
func (q *RealQdrant) Collections(ctx context.Context, baseURL string) ([]string, error) {
	var body struct {
		Result struct {
			Collections []struct {
				Name string `json:"name"`
			} `json:"collections"`
		} `json:"result"`
	}
	if err := q.h.doJSON(ctx, baseURL, storeOrigin, request{
		method: http.MethodGet, path: "/collections", timeout: listTimeout,
	}, &body); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(body.Result.Collections))
	for _, c := range body.Result.Collections {
		out = append(out, c.Name)
	}
	sort.Strings(out)
	return out, nil
}

// Count is the EXACT point count of a collection.
//
// exact:true costs a scan on a large collection and is the whole reason the
// ctl counts: an approximate count cannot prove that a fenced backup captured
// every point, which is the only claim `backup --fence` makes.
func (q *RealQdrant) Count(ctx context.Context, baseURL, collection string) (int64, error) {
	if err := checkCollection(collection); err != nil {
		return 0, err
	}
	var body struct {
		Result struct {
			Count int64 `json:"count"`
		} `json:"result"`
	}
	if err := q.h.doJSON(ctx, baseURL, storeOrigin, request{
		method:  http.MethodPost,
		path:    "/collections/" + url.PathEscape(collection) + "/points/count",
		body:    map[string]any{"exact": true},
		timeout: q.h.long,
	}, &body); err != nil {
		return 0, err
	}
	return body.Result.Count, nil
}

// Snapshot takes a snapshot of one collection and returns its file name.
func (q *RealQdrant) Snapshot(ctx context.Context, baseURL, collection string) (string, error) {
	if err := checkCollection(collection); err != nil {
		return "", err
	}
	var body struct {
		Result struct {
			Name string `json:"name"`
		} `json:"result"`
	}
	if err := q.h.doJSON(ctx, baseURL, storeOrigin, request{
		method:  http.MethodPost,
		path:    "/collections/" + url.PathEscape(collection) + "/snapshots",
		query:   waitTrue(),
		timeout: q.h.long,
	}, &body); err != nil {
		return "", err
	}
	if body.Result.Name == "" {
		return "", fmt.Errorf("qdrant took a snapshot of %s but named no file", collection)
	}
	// The name the STORE chose is about to become a filesystem path the backup
	// renames, so it is checked exactly as a name the caller supplied would be.
	if err := checkSnapshotName(body.Result.Name); err != nil {
		return "", fmt.Errorf("qdrant named the snapshot of %s %q: %w", collection, body.Result.Name, err)
	}
	return body.Result.Name, nil
}

// Recover restores a collection from a snapshot file already inside the
// container's snapshot directory.
func (q *RealQdrant) Recover(ctx context.Context, baseURL, collection, location string) error {
	if err := checkCollection(collection); err != nil {
		return err
	}
	if !strings.HasPrefix(location, snapshotLocationPrefix) {
		return fmt.Errorf("%w: a recover location must start with %s; %q does not",
			jobs.ErrRefused, snapshotLocationPrefix, location)
	}
	// A prefix alone is not containment: `file:///qdrant/snapshots/../../etc`
	// starts with it and names something else entirely.
	if strings.Contains(location, "..") {
		return fmt.Errorf("%w: the recover location %q traverses out of %s",
			jobs.ErrRefused, location, snapshotLocationPrefix)
	}
	return q.h.doJSON(ctx, baseURL, storeOrigin, request{
		method:  http.MethodPut,
		path:    "/collections/" + url.PathEscape(collection) + "/snapshots/recover",
		query:   waitTrue(),
		body:    map[string]any{"location": location},
		timeout: q.h.long,
	}, nil)
}

// DeleteSnapshot removes a snapshot file from the store's snapshot directory.
//
// It is Snapshot's ROLLBACK, so a snapshot that is already gone is not an
// error: a rollback that runs twice — a retried job, a crash between the
// delete and the checkpoint — must not fail the second time. This matches the
// fake, which drops an unknown name silently.
func (q *RealQdrant) DeleteSnapshot(ctx context.Context, baseURL, collection, name string) error {
	if err := checkCollection(collection); err != nil {
		return err
	}
	if err := checkSnapshotName(name); err != nil {
		return err
	}
	_, err := q.h.do(ctx, baseURL, storeOrigin, request{
		method:   http.MethodDelete,
		path:     "/collections/" + url.PathEscape(collection) + "/snapshots/" + url.PathEscape(name),
		query:    waitTrue(),
		timeout:  q.h.long,
		okStatus: []int{http.StatusOK, http.StatusNotFound},
	})
	return err
}
