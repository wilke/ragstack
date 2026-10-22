package settings

// Value rules for public keys (#775).
//
// Classify decides WHETHER the typed env API may edit a key; this table
// decides, for the keys that have a row, WHAT a value may be and what an
// operator should know when it changes. A public key without a row is accepted
// as any string, as every public key was before this table existed — the API's
// own validation at start-up is the backstop. A row exists where a bad value
// would be silent: config.py turns an unknown glossary mode into "off" with a
// warning, so an `env set` typo would otherwise look applied and do nothing.
//
// Mirrors python/ragstack/config.py; keep the accepted values in step with it.

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Restart scopes: what has to happen before a new value is in effect.
const (
	// ScopeAPIRestart: read by the API at start-up and used per request;
	// `tenant restart <t> --only api` applies it, no re-ingest.
	ScopeAPIRestart = "api-restart"
	// ScopeNewIngests: read at start-up, applied to ingests that run after
	// the restart; chunks already in an index keep what they were built with.
	ScopeNewIngests = "new-ingests"
)

// PublicRule is the value check and the operator note for one public key.
type PublicRule struct {
	// Check returns nil for an acceptable value.
	Check func(value string) error
	// Scope is one of the Scope* constants.
	Scope string
	// Help is shown with every `env set` of the key.
	Help string
}

func oneOf(allowed ...string) func(string) error {
	return func(v string) error {
		for _, a := range allowed {
			if v == a {
				return nil
			}
		}
		return fmt.Errorf("%q is not one of %s", v, strings.Join(allowed, "|"))
	}
}

// oneOfFold is oneOf after config.py's strip().lower().
func oneOfFold(allowed ...string) func(string) error {
	inner := oneOf(allowed...)
	return func(v string) error { return inner(strings.ToLower(strings.TrimSpace(v))) }
}

// nonNegativeFloat is a finite float >= 0, as pydantic parses a float field.
func nonNegativeFloat(v string) error {
	f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
	if err != nil {
		return fmt.Errorf("%q is not a number", v)
	}
	if math.IsNaN(f) || math.IsInf(f, 0) || f < 0 {
		return fmt.Errorf("%q must be a finite number >= 0", v)
	}
	return nil
}

// pydanticBool is the set of strings pydantic accepts for a bool field.
func pydanticBool(v string) error {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "0", "off", "f", "false", "n", "no", "1", "on", "t", "true", "y", "yes":
		return nil
	}
	return fmt.Errorf("%q is not a boolean (true|false|1|0|yes|no|on|off)", v)
}

// jsonObjectOrEmpty is a JSON object, or empty for "the defaults".
func jsonObjectOrEmpty(v string) error {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(v), &m); err != nil || m == nil {
		return fmt.Errorf("not a JSON object (the API would silently fall back to the defaults)")
	}
	return nil
}

const glossaryNote = " The glossary is a separate BM25 field (`symbol_glossary`) on the text index; " +
	"see docs/plans/symbol-glossary.md."

var publicRules = map[string]PublicRule{
	// #754/#763: the per-document symbol glossary.
	"SYMBOL_GLOSSARY_EXTRACTION": {
		Check: oneOf("off", "rules"),
		Scope: ScopeNewIngests,
		Help: "off|rules. Applies only to documents ingested in-process by the API after " +
			"`tenant restart <t> --only api`, and to scripts/backfill_symbol_glossary.py; chunks already " +
			"indexed are unchanged until they are backfilled or re-ingested. A gowe tenant's worker " +
			"ingest does not read it (the CWL takes no glossary input yet)." + glossaryNote,
	},
	"SYMBOL_GLOSSARY_CONFIG_JSON": {
		Check: jsonObjectOrEmpty,
		Scope: ScopeNewIngests,
		Help: "GlossaryConfig overrides as a JSON object (empty = defaults). Like SYMBOL_GLOSSARY_EXTRACTION, " +
			"it affects only ingests after `tenant restart <t> --only api` and the backfill." + glossaryNote,
	},
	"TEXT_SYMBOL_GLOSSARY_BOOST": {
		Check: nonNegativeFloat,
		Scope: ScopeAPIRestart,
		Help: "a number >= 0; 0 (the default) leaves the BM25 query byte-identical to before #754. " +
			"Query-time: it takes effect at `tenant restart <t> --only api`, with no re-ingest. On an index " +
			"without the field it is a no-op, so backfill first. Trial only with the reranker on." + glossaryNote,
	},
	// #747: the duplicate-paper guard.
	"DUPLICATE_POLICY": {
		Check: oneOfFold("refuse", "allow", "off"),
		Scope: ScopeNewIngests,
		Help: "refuse|allow|off — the duplicate-paper guard's default for an API ingest whose request " +
			"names no policy: refuse a DOI/PMCID/PMID the target collection already holds, allow it (chunks " +
			"carry duplicate_of), or no lookup (#747). Applies to ingests after `tenant restart <t> --only api`.",
	},
	"DOI_ENRICHMENT_PUBMED_IDS": {
		Check: pydanticBool,
		Scope: ScopeNewIngests,
		Help: "resolve pmid/pmcid from the NCBI ID Converter during DOI enrichment (#596). " +
			"Applies to ingests after `tenant restart <t> --only api`.",
	},
}

// RuleFor returns the value rule for key, if it has one.
func RuleFor(key string) (PublicRule, bool) {
	r, ok := publicRules[strings.TrimSpace(key)]
	return r, ok
}

// ValidatePublic checks value for a PUBLIC key. A key without a rule accepts
// any value. The error names the key.
func ValidatePublic(key, value string) error {
	r, ok := RuleFor(key)
	if !ok || r.Check == nil {
		return nil
	}
	if err := r.Check(value); err != nil {
		return fmt.Errorf("%s=%s: %v", key, value, err)
	}
	return nil
}
