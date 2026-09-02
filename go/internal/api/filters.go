package api

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

// Filter-value grammar (issue #471), the Go half of a contract that is
// enforced for real on the Python side.
//
// This implementation's handlers are stubs: `QueryRequest.Filters` is decoded
// and never read (ADR-0006 — Python is the complete implementation). That is
// precisely why the check here is a DECODE-LEVEL shape check and nothing more:
// a stub cannot prove it applies a filter correctly, but it can refuse a body
// the contract does not admit, which is what keeps the conformance suite
// ungated across both implementations. Without it the invalid-filter
// conformance case would pass vacuously on Go — a stub answering 200 to
// everything is not agreement.
//
// The grammar (contracts/schemas/{query,retrieve}_request.json, and
// python/ragstack/stores/filters.py, which is where it is derived and
// documented at length):
//
//   - a SCALAR value is a string, an integer, or a boolean;
//   - a LIST value is homogeneous: all strings, or all integers. Booleans are
//     scalar-only and the two types may not be mixed;
//   - an empty list is legal and matches nothing (issue #196);
//   - a float, null, an object, a nested list, a boolean list element and a
//     mixed list are refused with 400.
//
// The bound is what Qdrant's MatchValue / MatchAny can carry, measured rather
// than assumed: MatchValue takes a bool but MatchAny does not, and MatchAny is
// list[str] | list[int] rather than "a list of scalars".
//
// Objects are called out by name in the message because that is the reported
// defect: `{"year": {"gte": 2025}}` was a 500 on Python. Range operators are
// planned work (docs/plans/date-filtering.md), not a silently-ignored input.

// knownIntFields mirrors KNOWN_INT_FIELDS in python/ragstack/stores/filters.py:
// fields whose values are integers, so a string is a type error rather than
// something to coerce. Elasticsearch dynamically maps `year` as a `long` and
// coerces "2025" at query time; Qdrant compares typed and matches nothing. The
// same request therefore returned a different number of hits depending on which
// retrieval leg ran, which is the defect this refuses rather than papers over.
var knownIntFields = map[string]bool{"year": true}

const filterGrammar = "a filter value must be a string, an integer or a boolean, " +
	"or a list of strings or a list of integers — one type per list, booleans only " +
	"as scalars, an empty list matches nothing; floats, nulls, objects and nested " +
	"lists are not supported, and neither are range operators such as {\"gte\": ...} " +
	"— use exact values"

// decodeJSONBody decodes a request body with UseNumber, so JSON numbers arrive
// as json.Number (their literal text) instead of float64.
//
// This is load-bearing for the grammar, not a style choice. Go's default
// decodes every JSON number into a float64, which erases the distinction
// between `2025` and `2025.0` — and Python refuses the second. UseNumber keeps
// the literal, so `2025` is an integer and `2025.0` / `1e3` are floats on both
// implementations. `Filters` is the only `map[string]any` in these request
// structs, so nothing else changes shape.
func decodeJSONBody(body io.Reader, dst any) error {
	dec := json.NewDecoder(body)
	dec.UseNumber()
	return dec.Decode(dst)
}

// validateFilterValues returns a human-readable reason when `filters` carries a
// value outside the grammar above, or "" when every value is admissible. A nil
// or empty map is unconstrained and always fine.
func validateFilterValues(filters map[string]any) string {
	for key, value := range filters {
		if reason := checkFilterValue(key, value); reason != "" {
			return reason
		}
	}
	return ""
}

func checkFilterValue(key string, value any) string {
	if list, ok := value.([]any); ok {
		return checkFilterList(key, list)
	}
	return checkFilterScalar(key, value, false)
}

// checkFilterList enforces what MatchAny can carry: every element admissible on
// its own, no booleans, and ONE type across the whole list. An empty list is
// valid — it matches nothing (#196) — so the homogeneity check starts empty and
// stays satisfied.
func checkFilterList(key string, list []any) string {
	kinds := map[string]bool{}
	for _, item := range list {
		if reason := checkFilterScalar(key, item, true); reason != "" {
			return reason
		}
		if _, isBool := item.(bool); isBool {
			return refusal(key, fmt.Sprintf("list element %v is a boolean — booleans are scalar-only", item))
		}
		kinds[filterKind(item)] = true
	}
	if len(kinds) > 1 {
		return refusal(key, "list mixes string and integer values — a list must be all strings or all integers")
	}
	return ""
}

func checkFilterScalar(key string, value any, inList bool) string {
	where := "value"
	if inList {
		where = "list element"
	}
	if value == nil {
		return refusal(key, where+" is null")
	}
	if s, ok := value.(string); ok {
		if knownIntFields[key] {
			return refusal(key, fmt.Sprintf(
				"%s %q is a string but %q is an integer field (values are matched by type, not coerced)",
				where, s, key))
		}
		return ""
	}
	if b, ok := value.(bool); ok {
		if knownIntFields[key] {
			return refusal(key, fmt.Sprintf(
				"%s %v is a boolean but %q is an integer field (values are matched by type, not coerced)",
				where, b, key))
		}
		return ""
	}
	if n, ok := value.(json.Number); ok {
		// UseNumber keeps the literal, so a float is recognisable by its text —
		// exactly as Python distinguishes 2025 from 2025.0.
		if strings.ContainsAny(n.String(), ".eE") {
			return refusal(key, fmt.Sprintf("%s %s is a float", where, n.String()))
		}
		return ""
	}
	if _, ok := value.(map[string]any); ok {
		return refusal(key, where+" is an object — range operators are not supported")
	}
	if _, ok := value.([]any); ok {
		return refusal(key, where+" is a nested list")
	}
	return refusal(key, fmt.Sprintf("%s %v is not a supported type", where, value))
}

// filterKind collapses an element to its grammar type for the homogeneity
// check. Booleans are rejected before this is reached.
func filterKind(value any) string {
	if _, ok := value.(string); ok {
		return "string"
	}
	return "number"
}

func refusal(key, reason string) string {
	return fmt.Sprintf("unsupported filter value for %q: %s; %s", key, reason, filterGrammar)
}
