package drivers

import "testing"

// TestUnhealthyToleratesTheStateFieldAndNewChecks pins that the ctl's deep
// health parser keeps working on the #751 body shape: a `state` beside `ok`,
// the new dependency check names, and `disabled` checks (ok:true) that must
// not be reported as failing.
func TestUnhealthyToleratesTheStateFieldAndNewChecks(t *testing.T) {
	cases := []struct {
		name, body, want string
	}{
		{
			// unhealthy only runs on a non-200; with nothing failing it falls
			// back to the overall status, and a disabled check is not listed.
			name: "all ok or disabled",
			body: `{"status":"ok","checks":[
				{"name":"vector","ok":true,"state":"ok","latency_ms":1.5},
				{"name":"reranker","ok":true,"state":"disabled","detail":"not configured"}]}`,
			want: "the tenant reports status ok",
		},
		{
			name: "reranker down and embedder degraded",
			body: `{"status":"degraded","checks":[
				{"name":"vector","ok":true,"state":"ok"},
				{"name":"reranker","ok":false,"state":"down","detail":"timeout"},
				{"name":"embedder","ok":false,"state":"degraded","detail":null},
				{"name":"doi_resolver","ok":true,"state":"disabled"}]}`,
			want: "failing checks: embedder, reranker (timeout)",
		},
		{
			name: "degraded status with no failing check",
			body: `{"status":"degraded","checks":[{"name":"llm","ok":true,"state":"ok"}]}`,
			want: "the tenant reports status degraded",
		},
	}
	for _, c := range cases {
		if got := unhealthy([]byte(c.body)); got != c.want {
			t.Errorf("%s: unhealthy = %q, want %q", c.name, got, c.want)
		}
	}
}
