package grading

import "testing"

func TestResolveReader(t *testing.T) {
	ok := map[string]string{
		// The form for a keyed principal: its subject IS its API-key tenant,
		// and it stays colon-free.
		"@service:conf-nonadmin": "conf-nonadmin",
		// A full federated subject is kept verbatim.
		"bvbrc:alice@patricbrc.org": "bvbrc:alice@patricbrc.org",
		"keycloak:sub-123":          "keycloak:sub-123",
		// A bare username is qualified with the default issuer.
		"alice": "bvbrc:alice",
		// Whitespace is not an identity.
		"  alice  ": "bvbrc:alice",
	}
	for in, want := range ok {
		got, err := ResolveReader(in)
		if err != nil {
			t.Errorf("ResolveReader(%q): %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("ResolveReader(%q) = %q, want %q", in, got, want)
		}
	}

	// A read is assigned to PEOPLE, and label order is a list of people — so
	// every group form is refused rather than silently naming a set of readers
	// nobody can label. `@service:default` is refused for the same reason
	// wearing a different hat: it names the identity every unmapped key
	// resolves to.
	bad := []string{
		"", "   ", "@public", "public", "@group:lab", "group:lab",
		"@service:", "@service:bvbrc:alice", "@service:default", "@service:public",
		":alice", "bvbrc:", ":",
	}
	for _, in := range bad {
		if got, err := ResolveReader(in); err == nil {
			t.Errorf("ResolveReader(%q) = %q, expected a refusal", in, got)
		}
	}
}

func TestResolveReadersRefusesOneSubjectTwice(t *testing.T) {
	got, err := ResolveReaders([]string{"@service:a", "bob"})
	if err != nil {
		t.Fatalf("unexpected: %v", err)
	}
	if len(got) != 2 || got[0] != "a" || got[1] != "bvbrc:bob" {
		t.Fatalf("resolved in label order: %v", got)
	}
	// Two spellings of one person is not two independent readers — it would
	// report a κ of somebody against themselves.
	if _, err := ResolveReaders([]string{"alice", "bvbrc:alice"}); err == nil {
		t.Fatalf("two entries resolving to one subject must be refused")
	}
}

func TestLabelFor(t *testing.T) {
	for i, want := range map[int]string{0: "A", 1: "B", 25: "Z"} {
		if got := LabelFor(i); got != want {
			t.Errorf("LabelFor(%d) = %q, want %q", i, got, want)
		}
	}
}
