package grading

import (
	"fmt"
	"strings"
)

// DefaultIssuer qualifies a bare username, matching
// `python/ragstack/api/routers/collections.py::_DEFAULT_ISSUER`.
const DefaultIssuer = "bvbrc"

// reservedServiceSubjects are the shared fallback tenants every unmapped API
// key resolves to (`ragstack.tenancy.DEFAULT_TENANT` / `PUBLIC_TENANT`).
// `@service:default` would name the identity EVERY unmapped key authenticates
// as — an unrestricted `@public` wearing a single-account name — so it is
// refused here exactly as it is on the share path.
var reservedServiceSubjects = []string{"default", "public"}

// ResolveReader maps one `GradingBatchCreateRequest.readers` entry to a
// subject, or returns the 422 reason.
//
// This is the share-grantee vocabulary MINUS the group forms, resolved the way
// `POST /v1/collections/{id}/shares` and the ownership transfer resolve it, so
// `@service:<subject>` cannot come to mean two things:
//
//   - `@public` / `public` / `@group:<id>` / `group:<id>` → refused: a read is
//     assigned to people, and label order is a list of people;
//   - `@service:<subject>` → `<subject>`, kept COLON-FREE. This is the form for
//     a keyed principal: its subject IS its API-key tenant, and a bare name
//     would be qualified to `bvbrc:<name>`, an identity the key never
//     authenticates as — the reader would 404 on their own batch;
//   - anything containing `:` → kept verbatim as a full `issuer:subject`;
//   - anything else → `bvbrc:<username>`.
func ResolveReader(raw string) (string, error) {
	g := strings.TrimSpace(raw)
	if g == "" {
		return "", fmt.Errorf("a reader must not be empty or whitespace")
	}
	if g == "@public" || g == "public" {
		return "", fmt.Errorf(
			"a reader must be a person, not a group: %q names the public group. "+
				"Readers are listed individually, in label order (first = 'A')", raw)
	}
	for _, pref := range []string{"@group:", "group:"} {
		if strings.HasPrefix(g, pref) {
			return "", fmt.Errorf(
				"a reader must be a person, not a group: %q names a group. "+
					"Readers are listed individually, in label order (first = 'A')", raw)
		}
	}
	const servicePrefix = "@service:"
	if strings.HasPrefix(g, servicePrefix) {
		svc := strings.TrimSpace(strings.TrimPrefix(g, servicePrefix))
		if svc == "" {
			return "", fmt.Errorf("a '@service:<subject>' reader must name a non-empty service subject")
		}
		if strings.Contains(svc, ":") {
			return "", fmt.Errorf(
				"service subject %q must be colon-free: ':' is reserved for federated "+
					"'issuer:sub' identities; name one of those with the full subject instead", svc)
		}
		for _, r := range reservedServiceSubjects {
			if svc == r {
				return "", fmt.Errorf(
					"%q is a reserved tenant, not a service account: %v are the shared "+
						"fallback tenants unmapped keys resolve to, so naming one as a reader "+
						"would make every such caller a reader", svc, reservedServiceSubjects)
			}
		}
		return svc, nil
	}
	if strings.Contains(g, ":") {
		issuer, subject, _ := strings.Cut(g, ":")
		if strings.TrimSpace(issuer) == "" || strings.TrimSpace(subject) == "" {
			return "", fmt.Errorf(
				"a full 'issuer:subject' reader must have a non-empty issuer and subject")
		}
		return g, nil
	}
	return DefaultIssuer + ":" + g, nil
}

// ResolveReaders resolves the readers in LABEL order, refusing two entries that
// resolve to one subject — one person cannot be two independent readers, and a
// batch that allowed it would report a κ of somebody against themselves.
func ResolveReaders(raw []string) ([]string, error) {
	resolved := make([]string, 0, len(raw))
	for _, r := range raw {
		subject, err := ResolveReader(r)
		if err != nil {
			return nil, err
		}
		for i, seen := range resolved {
			if seen == subject {
				return nil, fmt.Errorf(
					"%q resolves to %q, which is already reader %s: one subject cannot "+
						"be two independent readers", r, subject, LabelFor(i))
			}
		}
		resolved = append(resolved, subject)
	}
	return resolved, nil
}
