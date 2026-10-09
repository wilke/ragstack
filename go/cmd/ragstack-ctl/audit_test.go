package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

func auditRow(id int64, at, op string) model.AuditRow {
	return model.AuditRow{
		ID: id, At: at, Phase: model.AuditPhase("result"), Principal: "key:ops", AuthMethod: model.AuthMethod("api_key"),
		RequestID: "0123456789abcdef", Op: op, Tenant: "dev", Outcome: "succeeded",
		ArgsRedacted: map[string]any{"token": "[REDACTED]"},
	}
}

func auditServer(t *testing.T, truncated bool) *fakeCtl {
	return newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(model.AuditResponse{
			Rows: []model.AuditRow{
				auditRow(3, "2026-10-09T15:00:00Z", "restart"),
				auditRow(2, "2026-10-09T14:00:00Z", "env-set"),
				auditRow(1, "2026-10-08T10:00:00Z", "backup"),
			},
			Limit: 3, Truncated: truncated,
		})
	})
}

// `audit list` reads GET /v1/audit with the tenant and limit the contract
// defines, and prints the rows.
func TestAuditListSendsItsFilters(t *testing.T) {
	f := auditServer(t, false)
	rc, out, errs := capture(t, "audit", "list", "--server", f.srv.URL, "--tenant", "dev", "--limit", "3")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	last := f.last(t)
	if last.Method != http.MethodGet || last.Path != "/v1/audit" {
		t.Errorf("%s %s", last.Method, last.Path)
	}
	if last.Query.Get("tenant") != "dev" || last.Query.Get("limit") != "3" {
		t.Errorf("query %v", last.Query)
	}
	for _, want := range []string{"restart", "env-set", "backup", "key:ops"} {
		if !strings.Contains(out, want) {
			t.Errorf("%q missing:\n%s", want, out)
		}
	}
}

// --since keeps rows at or after the instant, in table and JSON form; the
// redacted args come through untouched.
func TestAuditListSince(t *testing.T) {
	f := auditServer(t, false)
	rc, out, errs := capture(t, "audit", "list", "--server", f.srv.URL, "--since", "2026-10-09T14:00:00Z")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if strings.Contains(out, "backup") || !strings.Contains(out, "env-set") || !strings.Contains(out, "restart") {
		t.Errorf("--since filtered wrongly:\n%s", out)
	}
	if q := f.last(t).Query; q.Get("since") != "" {
		t.Errorf("--since must not be sent (the contract has no such parameter): %v", q)
	}

	rc, out, errs = capture(t, "--json", "audit", "list", "--server", f.srv.URL, "--since", "2026-10-09T14:30:00Z")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	var got model.AuditResponse
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if len(got.Rows) != 1 || got.Rows[0].ID != 3 || got.Rows[0].ArgsRedacted["token"] != "[REDACTED]" {
		t.Errorf("rows %+v", got.Rows)
	}
}

// A truncated page that never reached --since says so: older matching rows
// may lie beyond the limit.
func TestAuditListSinceWarnsWhenTheLimitEndsFirst(t *testing.T) {
	f := auditServer(t, true)
	_, _, errs := capture(t, "audit", "list", "--server", f.srv.URL, "--since", "2026-01-01T00:00:00Z")
	if !strings.Contains(errs, "raise --limit") {
		t.Errorf("no warning on a truncated window: %q", errs)
	}
}

func TestAuditListUsage(t *testing.T) {
	for _, args := range [][]string{
		{"audit"},
		{"audit", "bogus"},
		{"audit", "list", "--since", "yesterday"},
		{"audit", "list", "--limit", "5000"},
	} {
		if rc, _, _ := capture(t, args...); rc != exitUsage {
			t.Errorf("%v: rc %d, want usage", args, rc)
		}
	}
}

// The verb reports the daemon's refusal (a viewer is 403) rather than
// printing an empty table.
func TestAuditListReportsForbidden(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"code":"forbidden","detail":"operator role required","request_id":"0123456789abcdef"}`))
	})
	rc, out, _ := capture(t, "audit", "list", "--server", f.srv.URL)
	if rc == exitOK || strings.Contains(out, "PHASE") {
		t.Errorf("a 403 printed a table (rc %d):\n%s", rc, out)
	}
}
