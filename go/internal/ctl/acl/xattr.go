package acl

import (
	"errors"
	"fmt"
	"os"

	"golang.org/x/sys/unix"
)

// Kind selects which of the two ACLs a path carries.
type Kind string

const (
	// KindAccess is the ACL the kernel checks on every open of this path.
	KindAccess Kind = "access"
	// KindDefault is the ACL a directory hands to the children created
	// inside it. Only a directory may carry one; it is what makes a grant
	// survive the next tenant the ctl provisions.
	KindDefault Kind = "default"
)

// xattr names, from fs/posix_acl_xattr.h.
const (
	xattrAccess  = "system.posix_acl_access"
	xattrDefault = "system.posix_acl_default"
)

func (k Kind) xattr() (string, error) {
	switch k {
	case KindAccess:
		return xattrAccess, nil
	case KindDefault:
		return xattrDefault, nil
	}
	return "", fmt.Errorf("acl: unknown kind %q", string(k))
}

// ErrUnsupported is returned when the filesystem does not carry POSIX ACLs
// (tmpfs without acl, an overlay, a mount option). It is a distinct error so
// a test can skip and the CLI can say "this filesystem has no ACLs" rather
// than "permission denied".
var ErrUnsupported = errors.New("acl: filesystem does not support POSIX ACLs")

// Get reads one of the two ACLs of path, never following a symlink.
//
// An absent xattr is not an error. For KindAccess it means the path's ACL is
// exactly its mode bits, and that is what is returned (FromMode) — every path
// has an access ACL, most just store it in st_mode. For KindDefault it means
// children inherit nothing, and the empty ACL is what says so.
func Get(path string, kind Kind) (ACL, error) {
	name, err := kind.xattr()
	if err != nil {
		return nil, err
	}
	b, err := lgetxattr(path, name)
	switch {
	case err == nil:
		a, perr := Parse(b)
		if perr != nil {
			return nil, fmt.Errorf("%s: %s: %w", path, name, perr)
		}
		return a, nil
	case isAbsent(err):
		if kind == KindDefault {
			return nil, nil
		}
		fi, serr := os.Lstat(path)
		if serr != nil {
			return nil, serr
		}
		return FromMode(fi.Mode()), nil
	case errors.Is(err, unix.EOPNOTSUPP):
		return nil, fmt.Errorf("%s: %w", path, ErrUnsupported)
	default:
		return nil, fmt.Errorf("%s: getxattr %s: %w", path, name, err)
	}
}

// Set writes one of the two ACLs of path, never following a symlink. An empty
// ACL removes the xattr, which for KindDefault is how inheritance is cleared
// and for KindAccess is how a path falls back to its plain mode bits.
//
// Writing an access ACL rewrites st_mode's owner/group/other bits as a side
// effect — that is the kernel's behaviour, not this package's, and it is why
// Grant always carries the owner's existing bits into the list it builds
// instead of choosing new ones.
func Set(path string, kind Kind, a ACL) error {
	name, err := kind.xattr()
	if err != nil {
		return err
	}
	if kind == KindDefault {
		fi, err := os.Lstat(path)
		if err != nil {
			return err
		}
		if !fi.IsDir() {
			return fmt.Errorf("acl: %s is not a directory; only a directory carries a default ACL", path)
		}
	}
	if len(a) == 0 {
		return Remove(path, kind)
	}
	if err := a.Validate(); err != nil {
		return fmt.Errorf("%s: refusing to write %s: %w", path, name, err)
	}
	if err := lsetxattr(path, name, a.Encode()); err != nil {
		if errors.Is(err, unix.EOPNOTSUPP) {
			return fmt.Errorf("%s: %w", path, ErrUnsupported)
		}
		return fmt.Errorf("%s: setxattr %s: %w", path, name, err)
	}
	return nil
}

// Remove deletes one of the two ACL xattrs. An xattr that is already absent
// is success: Remove states the end state, not a transition.
func Remove(path string, kind Kind) error {
	name, err := kind.xattr()
	if err != nil {
		return err
	}
	if err := unix.Lremovexattr(path, name); err != nil {
		if isAbsent(err) {
			return nil
		}
		if errors.Is(err, unix.EOPNOTSUPP) {
			return fmt.Errorf("%s: %w", path, ErrUnsupported)
		}
		return fmt.Errorf("%s: removexattr %s: %w", path, name, err)
	}
	return nil
}

// isAbsent is the kernel's "this path has no such xattr". ENODATA and ENOATTR
// are the same number on Linux, and ENOTSUP is NOT checked here: it is the
// same number as EOPNOTSUPP, which means "this filesystem has no ACLs" — a
// very different fact, and one an operator has to be told rather than have
// silently read as "no ACL set".
func isAbsent(err error) bool { return errors.Is(err, unix.ENODATA) }

// lgetxattr reads an xattr with the buffer dance the syscall requires: ask
// for the size, then read, and retry once if the value grew in between.
func lgetxattr(path, name string) ([]byte, error) {
	size, err := unix.Lgetxattr(path, name, nil)
	if err != nil {
		return nil, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		buf := make([]byte, size)
		n, err := unix.Lgetxattr(path, name, buf)
		if err == nil {
			return buf[:n], nil
		}
		if !errors.Is(err, unix.ERANGE) {
			return nil, err
		}
		if size, err = unix.Lgetxattr(path, name, nil); err != nil {
			return nil, err
		}
	}
	return nil, fmt.Errorf("%s: %s kept changing size while being read", path, name)
}

func lsetxattr(path, name string, value []byte) error {
	return unix.Lsetxattr(path, name, value, 0)
}
