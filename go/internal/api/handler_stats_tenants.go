package api

import (
	"net/http"

	"github.com/ragstack/ragstack/internal/auth"
)

// TenantCollectionCount is one (tenant, collection) cell of the breakdown.
type TenantCollectionCount struct {
	Collection  string `json:"collection"`
	VectorCount *int   `json:"vector_count"`
	TextCount   *int   `json:"text_count"`
}

// TenantRow is one tenant's row of the breakdown.
type TenantRow struct {
	Tenant      string                  `json:"tenant"`
	Own         bool                    `json:"own"`
	Collections []TenantCollectionCount `json:"collections"`
}

// TenantsResponse is the body for GET /v1/stats/tenants: who the caller is,
// what it may reach, and where its readable data lives.
type TenantsResponse struct {
	Tenant       string              `json:"tenant"`
	Role         string              `json:"role"`
	Readable     []string            `json:"readable"`
	RestrictedTo []string            `json:"restricted_to"`
	AuthEnabled  bool                `json:"auth_enabled"`
	Policy       map[string][]string `json:"policy"`
	Tenants      []TenantRow         `json:"tenants"`
}

// HandleStatsTenants answers "who am I, and what may I reach".
//
// The API has no /v1/me, so this is where every client — the reference UI, and
// `conformance/test_grading.py`, which resolves its four principals here before
// it can assert anything about reader independence — turns a credential into a
// subject and a role. It existed only on the Python side, which is why the Go
// server could not be pointed at a suite that needs to know who its keys are.
//
// The IDENTITY half is real and is what this endpoint is for here. The COUNT
// half is not: the Go pipeline has no collection registry and no store behind
// it (see HandleListCollections), so the per-(tenant × collection) breakdown is
// an empty `tenants` list rather than fabricated numbers. `restricted_to` is
// null because the Go side has no TENANT_COLLECTIONS allowlist to be confined
// by — that is the truthful answer, not a placeholder, and it is the one the P2
// persona reads (`conformance/personas.py`).
func (s *Server) HandleStatsTenants(w http.ResponseWriter, r *http.Request) {
	p := auth.FromContext(r.Context())
	readable := []string{p.Tenant}
	if p.Tenant != "public" {
		readable = append(readable, "public")
	}
	writeJSON(w, http.StatusOK, TenantsResponse{
		Tenant:       p.Tenant,
		Role:         p.Role,
		Readable:     readable,
		RestrictedTo: nil,
		AuthEnabled:  s.Auth.Enabled(),
		Policy:       nil,
		Tenants:      []TenantRow{},
	})
}
