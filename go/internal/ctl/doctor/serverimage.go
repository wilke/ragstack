package doctor

// The image-mode rows (PR-F): a tenant whose registry row carries
// `server_image` runs its API from that SIF in an apptainer instance, so the
// file it names has to exist, has to be the bytes the row records, should be
// one the fleet prepared, and has to be able to see the tool-image
// directories its own configuration names.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// DefaultAPIBindRoots are the host roots an image-mode API instance may have
// bound into it beyond the ctl's own approved roots, when the daemon is not
// configured otherwise (CTL_API_BIND_ROOTS): the shared model cache, the
// GoWe image tree and the read-only config directory.
var DefaultAPIBindRoots = []string{"/rag/cache", "/scout/containers", "/rag/config"}

// envAPIBindRoots is the daemon configuration that widens (or narrows) the
// API instance's bind roots. A comma list; an entry may carry a `:ro`/`:rw`
// suffix, which this check ignores (containment is the question here, not the
// mount mode).
const envAPIBindRoots = "CTL_API_BIND_ROOTS"

// apiBindRoots is Options.APIBindRoots, else CTL_API_BIND_ROOTS from the
// process environment, else DefaultAPIBindRoots.
func (d *run) apiBindRoots() []string {
	raw := d.opts.APIBindRoots
	if raw == nil {
		if v := strings.TrimSpace(os.Getenv(envAPIBindRoots)); v != "" {
			raw = strings.Split(v, ",")
		} else {
			raw = DefaultAPIBindRoots
		}
	}
	out := make([]string, 0, len(raw))
	for _, r := range raw {
		r = strings.TrimSpace(r)
		if i := strings.LastIndex(r, ":"); i > 0 && (r[i+1:] == "ro" || r[i+1:] == "rw") {
			r = r[:i]
		}
		if r != "" && filepath.IsAbs(r) {
			out = append(out, filepath.Clean(r))
		}
	}
	return out
}

// ImageHashCache remembers the sha256 of image files keyed by (path, size,
// mtime), for the life of the process that holds it. A doctor run that is
// not about to start an image reads from it and never hashes; a run under
// `--op start` or `--op update-code` hashes and refreshes it. A file that was
// rewritten in place changes size or mtime and so misses the cache.
type ImageHashCache struct {
	mu sync.Mutex
	m  map[imageHashKey]string
}

type imageHashKey struct {
	path  string
	size  int64
	mtime int64
}

// NewImageHashCache returns an empty cache.
func NewImageHashCache() *ImageHashCache { return &ImageHashCache{m: map[imageHashKey]string{}} }

func (c *ImageHashCache) get(k imageHashKey) (string, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	v, ok := c.m[k]
	return v, ok
}

func (c *ImageHashCache) put(k imageHashKey, sum string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.m == nil {
		c.m = map[imageHashKey]string{}
	}
	c.m[k] = sum
}

// processImageHashes is the cache a run uses when Options.ImageHashes is nil:
// one per process, so the daemon's repeated runs share it.
var processImageHashes = NewImageHashCache()

// imageHashOps are the ops whose doctor run hashes an image file: the two
// that are about to run it.
var imageHashOps = map[string]bool{"start": true, "update-code": true}

// hashFile is the streaming sha256 of one file.
func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// serverImageChecks are the image-mode findings of one row.
//
// Every finding here is a WARNING on its own. server_image_missing and
// server_image_mismatch are raised to errors by the precondition table for
// exactly the two ops that are about to run the image (`start`,
// `update-code`), so those are red and cannot be forced; every other op —
// `stop`, `backup`, `decommission`, `purge`, `handover`, the ones an operator
// needs on a tenant whose image file is gone — is not blocked by it.
func (d *run) serverImageChecks(t *registry.Tenant) {
	si := t.ServerImage
	if si == nil {
		return
	}
	d.serverImageFileCheck(t.Name, si)
	if rec, ok := d.fleet.ServerImages[si.Name]; !ok || rec == nil {
		d.add(model.LevelWarn, ServerImageUnregistered, t.Name, fmt.Sprintf(
			"server_image %s is not in the fleet's server_images: no `fleet image prepare` vouches for it", si.Name))
	} else if rec.SHA256 != si.SHA256 {
		d.add(model.LevelWarn, ServerImageUnregistered, t.Name, fmt.Sprintf(
			"server_image %s records sha256 %s, but the fleet prepared that name with sha256 %s", si.Name, si.SHA256, rec.SHA256))
	}
	d.imageDirsCheck(t)
}

func (d *run) serverImageFileCheck(tenant string, si *registry.ServerImage) {
	st, err := os.Stat(si.Path)
	switch {
	case err != nil && os.IsNotExist(err):
		d.add(model.LevelWarn, ServerImageMissing, tenant, fmt.Sprintf(
			"server_image %s: %s does not exist; the API instance cannot start from it", si.Name, si.Path))
		return
	case err != nil:
		d.add(model.LevelWarn, ServerImageMissing, tenant, fmt.Sprintf(
			"server_image %s: %s cannot be read: %v", si.Name, si.Path, err))
		return
	case !st.Mode().IsRegular():
		d.add(model.LevelWarn, ServerImageMissing, tenant, fmt.Sprintf(
			"server_image %s: %s is not a regular file", si.Name, si.Path))
		return
	}
	cache := d.opts.ImageHashes
	if cache == nil {
		cache = processImageHashes
	}
	hash := d.opts.HashImage
	if hash == nil {
		hash = hashFile
	}
	key := imageHashKey{path: si.Path, size: st.Size(), mtime: st.ModTime().UnixNano()}
	sum, cached := cache.get(key)
	if imageHashOps[d.opts.Op] {
		var herr error
		sum, herr = hash(si.Path)
		if herr != nil {
			d.add(model.LevelWarn, ServerImageMissing, tenant, fmt.Sprintf(
				"server_image %s: %s cannot be hashed: %v", si.Name, si.Path, herr))
			return
		}
		cache.put(key, sum)
		cached = true
	}
	if cached && sum != si.SHA256 {
		d.add(model.LevelWarn, ServerImageMismatch, tenant, fmt.Sprintf(
			"server_image %s: %s hashes to %s, the registry records %s (size %d, modified %s)",
			si.Name, si.Path, sum, si.SHA256, st.Size(), st.ModTime().UTC().Format(time.RFC3339)))
	}
}

// imageDirsCheck reports every GOWE_IMAGE_DIRS entry of an image-mode row
// that falls outside the API bind roots.
func (d *run) imageDirsCheck(t *registry.Tenant) {
	raw := strings.TrimSpace(t.Settings["GOWE_IMAGE_DIRS"])
	if raw == "" {
		return
	}
	roots := d.apiBindRoots()
	for _, dir := range strings.Split(raw, ",") {
		dir = strings.TrimSpace(dir)
		if dir == "" {
			continue
		}
		inside := false
		if filepath.IsAbs(dir) {
			clean := filepath.Clean(dir)
			for _, r := range roots {
				if under(clean, r) {
					inside = true
					break
				}
			}
		}
		if !inside {
			d.add(model.LevelWarn, ImageDirOutsideBindRoots, t.Name, fmt.Sprintf(
				"GOWE_IMAGE_DIRS entry %q is outside the API bind roots [%s]: the API instance cannot see it, so the "+
					"tool-image boot check would not find the image there", dir, strings.Join(roots, " ")))
		}
	}
}
