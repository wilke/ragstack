package main

// `ragstack-ctl audit list` — the sanctioned read of the audit log, over
// GET /v1/audit (operator role; `args_redacted` is redacted by the daemon
// exactly as the API redacts it).
//
// It exists because of #716: the only other way to read the audit rows was to
// open `jobs.db` with sqlite3 or python, and doing that as any account but the
// daemon's leaves foreign-owned `jobs.db-shm`/`jobs.db-wal` beside the store,
// after which the daemon cannot open its own job store at its next start.
// Read jobs with `job list|show` and audit rows with this; never open the
// database directly.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

func auditUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl audit list [--tenant T] [--since RFC3339] [--limit N] [--server URL] [--api-key-file F] [--json]

  list   audit rows (intent + result of every mutation), newest first, with
         redacted arguments. OPERATOR role. --limit (1..1000, server default
         100) bounds what the daemon returns; --since then keeps only rows at
         or after that instant (filtered here, so raise --limit to reach
         further back).

Never read jobs.db with sqlite3/python: an account other than the daemon's
leaves its own -shm/-wal there and locks the daemon out of its store (#716).
`)
	return exitUsage
}

func cmdAudit(args []string, jsonOut bool) int {
	if len(args) == 0 {
		return auditUsage()
	}
	switch args[0] {
	case "list":
		return cmdAuditList(args[1:], jsonOut)
	case "help", "-h", "--help":
		auditUsage()
		return exitOK
	default:
		return usageErr("audit: unknown verb %q (list)", args[0])
	}
}

func cmdAuditList(args []string, jsonOut bool) int {
	fs := flag.NewFlagSet("audit list", flag.ContinueOnError)
	fs.SetOutput(stderr)
	r := addReadFlags(fs, jsonOut)
	tenant := fs.String("tenant", "", "restrict to one tenant")
	since := fs.String("since", "", "only rows at or after this RFC 3339 instant")
	limit := fs.Int("limit", 0, "how many rows the daemon returns (1..1000, default 100)")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() != 0 {
		return usageErr("audit list: unexpected argument %q", fs.Arg(0))
	}
	var sinceT time.Time
	if *since != "" {
		t, err := time.Parse(time.RFC3339Nano, *since)
		if err != nil {
			return usageErr("audit list: --since %q is not RFC 3339 (e.g. 2026-10-09T09:00:00-05:00)", *since)
		}
		sinceT = t
	}
	if *limit < 0 || *limit > 1000 {
		return usageErr("audit list: --limit %d is outside 1..1000", *limit)
	}
	q := url.Values{}
	if *tenant != "" {
		q.Set("tenant", *tenant)
	}
	if *limit > 0 {
		q.Set("limit", strconv.Itoa(*limit))
	}
	c, err := r.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	resp, err := c.get(context.Background(), "/v1/audit", q)
	if err != nil {
		return failClient(err)
	}
	if resp.Status != http.StatusOK {
		return reportHTTPError(resp)
	}
	if *r.asJSON && sinceT.IsZero() {
		return writeRaw(resp.Body)
	}
	var list model.AuditResponse
	if err := json.Unmarshal(resp.Body, &list); err != nil {
		return failClient(fmt.Errorf("the audit list is not audit_response.json: %w", err))
	}
	rows, reachedSince := filterAuditSince(list.Rows, sinceT)
	if !sinceT.IsZero() && list.Truncated && !reachedSince {
		fmt.Fprintf(stderr, "(the %d-row limit ended before --since; older matching rows may exist — raise --limit)\n",
			list.Limit)
	}
	if *r.asJSON {
		list.Rows = rows
		return printJSON(list)
	}
	fmt.Fprintf(stdout, "%-6s %-25s %-6s %-22s %-16s %-12s %-12s %s\n",
		"ID", "AT", "PHASE", "PRINCIPAL", "OP", "TENANT", "OUTCOME", "JOB")
	for _, row := range rows {
		fmt.Fprintf(stdout, "%-6d %-25s %-6s %-22s %-16s %-12s %-12s %s\n",
			row.ID, row.At, row.Phase, row.Principal, row.Op, orNone(string(row.Tenant)),
			orNone(row.Outcome), orNone(string(row.JobID)))
		if e := string(row.Error); e != "" {
			fmt.Fprintf(stdout, "       error %s\n", e)
		}
	}
	if list.Truncated && sinceT.IsZero() {
		fmt.Fprintf(stderr, "(truncated at the %d-row limit)\n", list.Limit)
	}
	return exitOK
}

// filterAuditSince keeps the rows at or after since (all of them when since is
// zero). Rows come newest first; reachedSince reports whether a row OLDER than
// since was seen, i.e. whether the window is complete. A row whose `at` does
// not parse is kept: dropping an audit row silently is the worse failure.
func filterAuditSince(rows []model.AuditRow, since time.Time) ([]model.AuditRow, bool) {
	if since.IsZero() {
		return rows, true
	}
	out := make([]model.AuditRow, 0, len(rows))
	reached := false
	for _, row := range rows {
		at, err := time.Parse(time.RFC3339Nano, row.At)
		if err == nil && at.Before(since) {
			reached = true
			continue
		}
		out = append(out, row)
	}
	return out, reached
}
