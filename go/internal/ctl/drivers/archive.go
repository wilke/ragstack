package drivers

import (
	"archive/tar"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// RealArchive is the tar half of `backup --tar` and `restore --from`.
//
// Extraction is the dangerous direction and the code below is shaped by that:
// a bundle is operator input that may have travelled, and every classic tar
// attack — an absolute name, a `../` name, a symlink whose target is written
// through afterwards, a hardlink to /etc/shadow, a header that claims eight
// bytes and delivers eight gigabytes — is refused by name rather than
// defended against generically. The whole extraction also happens in a staging
// directory and is moved into place only once EVERY entry has passed, so a tar
// whose thousandth entry is hostile leaves nothing of its first nine hundred
// behind.
type RealArchive struct{ opts RealOptions }

var _ jobs.Archive = (*RealArchive)(nil)

// Extraction defaults, applied when ArchiveLimits leaves a field zero. They
// are not tuned to any particular bundle: they are the point past which an
// archive is not a bundle at all.
const (
	defaultMaxEntries = 100000
	defaultMaxBytes   = 64 << 30 // 64 GiB
	// maxNameBytes is PATH_MAX-ish. A longer name cannot be created on any
	// filesystem here, so accepting it only means failing later and messily.
	maxNameBytes = 4096
	// extractModeFile/Dir are what an extracted bundle gets: the same modes
	// the ctl writes a bundle with, never the ones the archive asks for. A tar
	// that carried 0777 (or setuid) would otherwise re-create it.
	extractModeFile = 0o640
	extractModeDir  = 0o750
	// archiveMode is the mode of a tar this driver writes.
	archiveMode = 0o640
)

// Create writes a tar of dir to out with relative names.
func (a *RealArchive) Create(ctx context.Context, dir, out string) error {
	if _, err := paths.SafePath("/", dir); err != nil {
		return fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	// The RESOLVED out path, and the one every syscall below uses: an `out`
	// whose parent is a symlink pointing out of the deployment is a tar
	// written outside every approved root, and a lexical check cannot see it.
	out, err := resolvedContained(out, a.opts.ApprovedRoots)
	if err != nil {
		return err
	}
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return fmt.Errorf("%w: %s is not an existing directory to archive", jobs.ErrRefused, dir)
	}
	if _, err := os.Lstat(out); err == nil {
		return fmt.Errorf("%w: %s already exists; an archive never overwrites", jobs.ErrRefused, out)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return err
	}

	f, err := os.OpenFile(out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, archiveMode)
	if err != nil {
		return err
	}
	// A half-written tar is removed rather than left: the bundle's checksum
	// step would otherwise record it, and a truncated archive is a bundle that
	// verifies and does not restore.
	committed := false
	defer func() {
		f.Close()
		if !committed {
			_ = os.Remove(out)
		}
	}()

	tw := tar.NewWriter(f)
	err = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		rel, err := filepath.Rel(dir, p)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil // the root itself is implied by the relative names
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		switch {
		case info.Mode().IsDir():
			return tw.WriteHeader(&tar.Header{
				Typeflag: tar.TypeDir,
				Name:     filepath.ToSlash(rel) + "/",
				Mode:     int64(extractModeDir),
				ModTime:  info.ModTime(),
				Format:   tar.FormatPAX,
			})
		case info.Mode().IsRegular():
			if err := tw.WriteHeader(&tar.Header{
				Typeflag: tar.TypeReg,
				Name:     filepath.ToSlash(rel),
				Mode:     int64(info.Mode().Perm()),
				Size:     info.Size(),
				ModTime:  info.ModTime(),
				Format:   tar.FormatPAX,
			}); err != nil {
				return err
			}
			src, err := os.Open(p)
			if err != nil {
				return err
			}
			defer src.Close()
			// Exactly Size bytes: a file the tenant is still appending to
			// would otherwise write more than the header declared and corrupt
			// every entry after it.
			if _, err := io.CopyN(tw, src, info.Size()); err != nil {
				return fmt.Errorf("archiving %s: %w", rel, err)
			}
			return nil
		default:
			// A symlink refuses the WHOLE archive rather than being skipped.
			// Skipping is the tempting answer and it is wrong: the caller
			// asked for an archive of this directory, and one that silently
			// lacks a file the directory has is a backup that restores to
			// something other than what was backed up. Naming it lets an
			// operator decide.
			return fmt.Errorf("%w: %s is a %s; the ctl archives regular files and directories only",
				jobs.ErrRefused, rel, kindOf(info.Mode()))
		}
	})
	if err != nil {
		return err
	}
	if err := tw.Close(); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	committed = true
	return nil
}

// kindOf names a non-regular file for a refusal message.
func kindOf(m fs.FileMode) string {
	switch {
	case m&fs.ModeSymlink != 0:
		return "symlink"
	case m&fs.ModeDevice != 0:
		return "device"
	case m&fs.ModeNamedPipe != 0:
		return "fifo"
	case m&fs.ModeSocket != 0:
		return "socket"
	default:
		return "special file"
	}
}

// Extract unpacks tarPath under dest.
func (a *RealArchive) Extract(ctx context.Context, tarPath, dest string, limits jobs.ArchiveLimits) error {
	if _, err := paths.SafePath("/", tarPath); err != nil {
		return fmt.Errorf("%w: %v", jobs.ErrRefused, err)
	}
	// The containment check is made on the RESOLVED destination and the
	// extraction then happens under exactly that path. It used to check the
	// string it was given and afterwards EvalSymlinks it for the staging
	// directory without re-checking, so a `dest` whose own name — or whose
	// parent — was a symlink out of the deployment passed the check and was
	// written to anyway.
	realDest, err := resolvedContainedNoLeafLink(dest, a.opts.ApprovedRoots)
	if err != nil {
		return err
	}
	// dest must EXIST: creating it here would create it with the wrong mode
	// and outside the Files driver's rules, and an extraction into a directory
	// nobody made is an extraction nobody scoped.
	if st, err := os.Stat(realDest); err != nil || !st.IsDir() {
		return fmt.Errorf("%w: %s is not an existing directory to extract into", jobs.ErrRefused, dest)
	}
	maxEntries := limits.MaxEntries
	if maxEntries <= 0 {
		maxEntries = defaultMaxEntries
	}
	maxBytes := limits.MaxBytes
	if maxBytes <= 0 {
		maxBytes = defaultMaxBytes
	}

	f, err := os.Open(tarPath)
	if err != nil {
		return err
	}
	defer f.Close()

	// Staging. Everything lands here first; the move into dest happens only
	// after the last entry has been read and accepted, so a refusal anywhere
	// leaves dest exactly as it was.
	var nonce [8]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	staging := filepath.Join(realDest, ".extract-"+hex.EncodeToString(nonce[:]))
	if err := os.Mkdir(staging, extractModeDir); err != nil {
		return err
	}
	committed := false
	defer func() {
		if !committed {
			_ = os.RemoveAll(staging)
		}
	}()

	tops, err := unpack(ctx, tar.NewReader(f), staging, maxEntries, maxBytes)
	if err != nil {
		return err
	}
	// Per TOP-LEVEL entry, so that a bundle directory appears under dest whole
	// or not at all.
	for _, name := range tops {
		from, to := filepath.Join(staging, name), filepath.Join(realDest, name)
		if _, err := os.Lstat(to); err == nil {
			return fmt.Errorf("%w: %s already exists in %s; an extraction never overwrites", jobs.ErrRefused, name, dest)
		} else if !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		if err := os.Rename(from, to); err != nil {
			return err
		}
	}
	committed = true
	return os.Remove(staging) // empty now; every top-level entry moved out
}

// unpack reads every entry into staging and returns the top-level names it
// created, in the order they first appeared.
func unpack(ctx context.Context, tr *tar.Reader, staging string, maxEntries int, maxBytes int64) ([]string, error) {
	var (
		tops     []string
		seenTop  = map[string]bool{}
		entries  int
		budget   = maxBytes
		modeDirs []string // directories whose mode is set after their contents
	)
	for {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("reading the archive: %w", err)
		}
		entries++
		if entries > maxEntries {
			return nil, fmt.Errorf("%w: the archive holds more than %d entries", jobs.ErrRefused, maxEntries)
		}
		name, err := safeEntryName(h.Name)
		if err != nil {
			return nil, err
		}
		switch h.Typeflag {
		case tar.TypeDir, tar.TypeReg:
		case tar.TypeSymlink, tar.TypeLink:
			// Both, and for the same reason: the entry names a path the ctl
			// did not choose, and a later entry written "through" it lands
			// wherever it points. A bundle has no legitimate use for either.
			return nil, fmt.Errorf("%w: the archive entry %s is a %s; the ctl extracts regular files and directories only",
				jobs.ErrRefused, name, linkKind(h.Typeflag))
		default:
			return nil, fmt.Errorf("%w: the archive entry %s is a special entry (type %q); the ctl extracts regular files and directories only",
				jobs.ErrRefused, name, string(h.Typeflag))
		}

		target := filepath.Join(staging, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), extractModeDir); err != nil {
			return nil, err
		}
		// The LEXICAL checks above cannot see a symlink, and this extraction
		// creates directories of its own — so the parent is resolved and
		// re-checked against staging before anything is written into it.
		if err := underStaging(filepath.Dir(target), staging, name); err != nil {
			return nil, err
		}
		if top := topLevel(name); top != "" && !seenTop[top] {
			seenTop[top] = true
			tops = append(tops, top)
		}
		if h.Typeflag == tar.TypeDir {
			if err := os.MkdirAll(target, extractModeDir); err != nil {
				return nil, err
			}
			modeDirs = append(modeDirs, target)
			continue
		}
		written, err := writeEntry(tr, target, budget)
		if err != nil {
			return nil, err
		}
		budget -= written
	}
	// Directory modes LAST: a 0750 directory created before its contents is
	// still writable by this process (it owns it), but a mode set on the way
	// in can be clobbered by MkdirAll for a deeper entry.
	for _, d := range modeDirs {
		if err := os.Chmod(d, extractModeDir); err != nil {
			return nil, err
		}
	}
	return tops, nil
}

func linkKind(t byte) string {
	if t == tar.TypeSymlink {
		return "symlink"
	}
	return "hardlink"
}

// writeEntry copies one file entry, enforcing the remaining byte budget as it
// goes rather than trusting the header's Size — which is a number the archive
// chose and a decompression bomb gets wrong on purpose.
func writeEntry(tr *tar.Reader, target string, budget int64) (int64, error) {
	if budget <= 0 {
		return 0, fmt.Errorf("%w: the archive's contents exceed the extraction byte budget", jobs.ErrRefused)
	}
	f, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, extractModeFile)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	// budget+1, so that an entry which exactly exhausts the budget is
	// distinguishable from one that overruns it.
	n, err := io.CopyN(f, tr, budget+1)
	if err != nil && !errors.Is(err, io.EOF) {
		return n, err
	}
	if n > budget {
		return n, fmt.Errorf("%w: the archive's contents exceed the extraction byte budget", jobs.ErrRefused)
	}
	return n, nil
}

// safeEntryName validates a tar entry name and returns it in slash form.
func safeEntryName(raw string) (string, error) {
	if raw == "" {
		return "", fmt.Errorf("%w: the archive holds an entry with no name", jobs.ErrRefused)
	}
	if len(raw) > maxNameBytes {
		return "", fmt.Errorf("%w: an archive entry name is longer than %d bytes", jobs.ErrRefused, maxNameBytes)
	}
	if strings.ContainsRune(raw, 0) {
		return "", fmt.Errorf("%w: an archive entry name contains a NUL byte", jobs.ErrRefused)
	}
	// Backslashes are refused rather than normalised: on this host they are a
	// legal character in a file name, so an entry called `..\..\x` is not a
	// traversal — but an entry holding one is an archive written for another
	// platform, and guessing which it meant is how a check is defeated.
	if strings.ContainsRune(raw, '\\') {
		return "", fmt.Errorf("%w: the archive entry %q contains a backslash", jobs.ErrRefused, raw)
	}
	if strings.HasPrefix(raw, "/") {
		return "", fmt.Errorf("%w: the archive entry %q is an absolute path", jobs.ErrRefused, raw)
	}
	clean := path.Clean(strings.TrimSuffix(raw, "/"))
	if clean == "." || clean == "" {
		return "", fmt.Errorf("%w: the archive entry %q names no file", jobs.ErrRefused, raw)
	}
	for _, seg := range strings.Split(clean, "/") {
		if seg == ".." {
			return "", fmt.Errorf("%w: the archive entry %q traverses out of the destination", jobs.ErrRefused, raw)
		}
	}
	return clean, nil
}

// topLevel is the first path segment of an entry name.
func topLevel(name string) string {
	if i := strings.IndexByte(name, '/'); i >= 0 {
		return name[:i]
	}
	return name
}

// underStaging resolves dir and confirms it is still inside staging.
func underStaging(dir, staging, entry string) error {
	real, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return err
	}
	if real != staging && !strings.HasPrefix(real, staging+string(filepath.Separator)) {
		return fmt.Errorf("%w: the archive entry %s resolves to %s, outside the extraction directory",
			jobs.ErrRefused, entry, real)
	}
	return nil
}
