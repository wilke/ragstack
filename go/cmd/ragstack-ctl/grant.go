package main

// `fleet grant` — give the service account access to the managed roots
// through POSIX ACLs.
//
// This is a LOCAL CLI action: no job, no daemon, no op_request. It has to run
// as the OWNER of the paths (wilke), and the daemon runs as the ctl account
// (svcbvbrc) — which is precisely the account that has no access yet. A
// daemon-side implementation would be asking the locked-out account to let
// itself in, and the kernel refuses: only a path's owner (or root, which the
// ctl never is) may set its ACL.
//
// The host has no setfacl and no root this week, so the ctl writes the
// system.posix_acl_* xattrs itself — see internal/ctl/acl.

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"

	"github.com/ragstack/ragstack/internal/ctl/acl"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

func grantUsage() int {
	fmt.Fprint(stderr, `usage: ragstack-ctl fleet grant --user NAME [flags]

Gives NAME read/write access to the managed roots through POSIX ACLs, and
gives the owning group nothing. Run it AS THE OWNER of those roots (wilke):
only a path's owner may set its ACL, so this is a local action — it never
goes through the daemon, which runs as the very account being granted.

  --user NAME     the account to grant (e.g. svcbvbrc). Required.
  --roots A,B,C   the roots to act on. Default: <rag-root>/data/tenants,
                  <rag-root>/repos/tenants, <rag-root>/backups/tenants
  --rag-root DIR  deployment root (default /rag or $RAG_ROOT)
  --recursive     walk the whole tree (default true)
  --revoke        remove NAME's entries instead of adding them
  --dry-run       print the table, write nothing
  --json          machine-readable changes
  --quiet         print only paths that changed

What it writes, per path:
  a directory  user::<unchanged>  user:NAME:rwx  group::---  mask::rwx  other::---
               and the same list as the DEFAULT ACL, so new files inherit it
  a file       user::<unchanged>  user:NAME:rw-  group::---  mask::rw-  other::---
  secrets.env, ctl-secrets.env, *.bak-*        user:NAME:r--

group:: is zeroed only when the owning group is cels (gid 20001) — the point
of the exercise: 1869 people must not get what the one account gets. Any other
owning group keeps its bits. other:: is always zeroed. Nothing is ever
chmod'ed or chown'ed, no symlink is followed, and a path this account does not
own is reported and skipped.

Exit: 0 ok · 1 error · 2 usage · 3 refused (a root you do not own)
`)
	return exitUsage
}

func cmdFleetGrant(args []string, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet grant", flag.ContinueOnError)
	fs.SetOutput(stderr)
	username := fs.String("user", "", "account to grant")
	rootsCSV := fs.String("roots", "", "comma-separated roots (default: the three managed roots)")
	root := fs.String("rag-root", ragRoot, "deployment root")
	recursive := fs.Bool("recursive", true, "walk the whole tree")
	revoke := fs.Bool("revoke", false, "remove the named entries instead")
	dryRun := fs.Bool("dry-run", false, "compute the changes without writing")
	asJSON := fs.Bool("json", jsonOut, "machine-readable output")
	quiet := fs.Bool("quiet", false, "print only the paths that changed")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *username == "" {
		return grantUsage()
	}

	// A name or a numeric uid. The lookup goes through hostfacts, which asks
	// NSS (getent) when Go's own /etc/passwd parser draws a blank: the ctl is
	// a static binary, and on coconut the service account is a directory
	// account that `user.Lookup` cannot see — the first dry run on the host
	// refused "no such account svcbvbrc" for exactly that reason.
	resolved := hostfacts.LookupUID(*username)
	if n, err := strconv.Atoi(*username); err == nil && n >= 0 {
		resolved = n
	}
	if resolved < 0 {
		return fail(fmt.Errorf("no such account %q on this host (neither /etc/passwd nor getent knows it)", *username))
	}
	uid := uint32(resolved)

	roots, rc := grantRoots(*rootsCSV, *root)
	if rc != exitOK {
		return rc
	}
	// Refuse EVERY root we do not own before touching ANY of them. A partial
	// grant across three roots is the state hardest to reason about later.
	self := os.Getuid()
	if rc := refuseUnowned(roots, self, *username); rc != exitOK {
		return rc
	}

	opts := acl.GrantOptions{
		Recursive: *recursive,
		Revoke:    *revoke,
		DryRun:    *dryRun,
		Self:      self,
	}
	var all []acl.Change
	for _, r := range roots {
		changes, err := acl.Apply(r, uid, opts)
		all = append(all, changes...)
		if err != nil {
			printGrantTable(all, *asJSON, *quiet, *dryRun, *revoke, *username)
			return fail(fmt.Errorf("%s: %w", r, err))
		}
	}
	printGrantTable(all, *asJSON, *quiet, *dryRun, *revoke, *username)
	return exitOK
}

// grantRoots resolves --roots, defaulting to the three managed roots. Every
// root must be absolute: a relative path would be resolved against whatever
// directory the operator happened to be in.
func grantRoots(csv, ragRoot string) ([]string, int) {
	if csv == "" {
		r := paths.NewRoots(ragRoot, paths.Overrides{})
		return []string{r.DataDir, r.ReposDir, r.BackupsDir}, exitOK
	}
	var out []string
	for _, p := range strings.Split(csv, ",") {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if !filepath.IsAbs(p) {
			return nil, usageErr("fleet grant: --roots entry %q is not an absolute path", p)
		}
		// Every root lives under the rag root: a grant is a recursive rewrite
		// of group and other bits, and pointing it at a home directory would
		// be a mistake this command must not be able to make.
		if _, err := paths.SafePath(ragRoot, filepath.Clean(p)); err != nil {
			return nil, usageErr("fleet grant: --roots entry %q is not under the rag root %s (%v)", p, ragRoot, err)
		}
		out = append(out, filepath.Clean(p))
	}
	if len(out) == 0 {
		return nil, usageErr("fleet grant: --roots is empty")
	}
	return out, exitOK
}

// refuseUnowned is exit code 3's only cause here: a root somebody else owns.
// A root that does not exist is skipped, not refused — /rag/backups/tenants
// does not exist until the first backup, and refusing the whole command over
// it would make the grant impossible to run before then.
func refuseUnowned(roots []string, self int, grantee string) int {
	for _, r := range roots {
		fi, err := os.Lstat(r)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return fail(err)
		}
		owner, ok := ownerUID(fi)
		if !ok {
			continue
		}
		if owner != self {
			fmt.Fprintf(stderr,
				"ragstack-ctl: refusing: %s is owned by %s, not by you (%s). "+
					"Only the owner may set a POSIX ACL — ask %s to run "+
					"`ragstack-ctl fleet grant --user %s --roots %s`.\n",
				r, nameOfUID(owner), nameOfUID(self), nameOfUID(owner), grantee, r)
			return exitRefused
		}
	}
	return exitOK
}

func nameOfUID(uid int) string {
	if name := hostfacts.UsernameOf(uid); name != "" {
		return name
	}
	return "uid " + strconv.Itoa(uid)
}

// grantResult is the --json shape: the changes plus the counts, so a script
// does not have to re-derive "did anything happen".
type grantResult struct {
	User      string       `json:"user"`
	DryRun    bool         `json:"dry_run"`
	Revoke    bool         `json:"revoke"`
	Changed   int          `json:"changed"`
	Unchanged int          `json:"unchanged"`
	Skipped   int          `json:"skipped"`
	Changes   []acl.Change `json:"changes"`
}

func printGrantTable(changes []acl.Change, asJSON, quiet, dryRun, revoke bool, username string) {
	changed, unchanged, skipped := acl.Summarize(changes)
	if asJSON {
		encode(grantResult{
			User: username, DryRun: dryRun, Revoke: revoke,
			Changed: changed, Unchanged: unchanged, Skipped: skipped,
			Changes: changes,
		})
		return
	}
	verb := "granted to"
	if revoke {
		verb = "revoked from"
	}
	if dryRun {
		verb = "would grant to"
		if revoke {
			verb = "would revoke from"
		}
	}
	// The before → after pair is the whole review surface of a dry run, so it
	// goes on its own lines rather than into columns that wrap.
	for _, c := range changes {
		switch {
		case c.Note != "":
			if !quiet {
				fmt.Fprintf(stdout, "skip  %s  (%s)\n", c.Path, c.Note)
			}
		case c.Before == c.After:
			if !quiet {
				fmt.Fprintf(stdout, "ok    %s\n", c.Path)
			}
		default:
			fmt.Fprintf(stdout, "%s  %s\n        before %s\n        after  %s\n", shortVerb(dryRun), c.Path, c.Before, c.After)
		}
	}
	fmt.Fprintf(stdout, "\n%s %s: %d changed, %d already correct, %d skipped\n",
		verb, username, changed, unchanged, skipped)
	if reasons := acl.SkipReasons(changes); len(reasons) > 0 {
		sort.Strings(reasons)
		fmt.Fprintf(stdout, "skipped: %s\n", strings.Join(reasons, ", "))
	}
	if dryRun && changed > 0 {
		fmt.Fprintln(stdout, "\n(dry run: nothing was written — rerun without --dry-run)")
	}
}

func shortVerb(dryRun bool) string {
	if dryRun {
		return "plan "
	}
	return "set  "
}

// ownerUID reads a path's owning uid. It is its own function because the
// syscall.Stat_t assertion is the one place this file is not portable, and a
// platform where it fails must report "unknown owner", not "owned by 0".
func ownerUID(fi os.FileInfo) (int, bool) {
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return int(st.Uid), true
}
