package settings

import (
	"strings"
	"testing"
)

// Every value rule belongs to a key the env API may edit: a rule on a key that
// is not public is a rule nothing consults.
func TestEveryRuleIsForAPublicKey(t *testing.T) {
	for k, r := range publicRules {
		if Classify(k) != Public {
			t.Errorf("%s has a value rule but classifies as %s", k, Classify(k))
		}
		if r.Check == nil || r.Help == "" {
			t.Errorf("%s: a rule needs a check and help", k)
		}
		if r.Scope != ScopeAPIRestart && r.Scope != ScopeNewIngests {
			t.Errorf("%s: unknown scope %q", k, r.Scope)
		}
	}
}

func TestValidatePublic(t *testing.T) {
	ok := map[string][]string{
		"TEXT_SYMBOL_GLOSSARY_BOOST":  {"0", "1", "1.5", " 2 "},
		"SYMBOL_GLOSSARY_EXTRACTION":  {"off", "rules"},
		"SYMBOL_GLOSSARY_CONFIG_JSON": {"", `{}`, `{"definition_window":200}`},
		"DUPLICATE_POLICY":            {"refuse", "allow", "off", "Refuse"},
		"DOI_ENRICHMENT_PUBMED_IDS":   {"true", "false", "1", "0", "yes", "Off"},
		"LOG_LEVEL":                   {"anything", ""}, // no rule: any string
	}
	for k, vs := range ok {
		for _, v := range vs {
			if err := ValidatePublic(k, v); err != nil {
				t.Errorf("ValidatePublic(%s, %q) = %v, want accepted", k, v, err)
			}
		}
	}
	bad := map[string][]string{
		"TEXT_SYMBOL_GLOSSARY_BOOST":  {"-1", "-0.001", "x", "", "NaN", "+Inf", "1,5"},
		"SYMBOL_GLOSSARY_EXTRACTION":  {"on", "llm", "RULES", ""},
		"SYMBOL_GLOSSARY_CONFIG_JSON": {"{", "null", `"x"`, "[]"},
		"DUPLICATE_POLICY":            {"skip", ""},
		"DOI_ENRICHMENT_PUBMED_IDS":   {"maybe", ""},
	}
	for k, vs := range bad {
		for _, v := range vs {
			err := ValidatePublic(k, v)
			if err == nil || !strings.Contains(err.Error(), k) {
				t.Errorf("ValidatePublic(%s, %q) = %v, want a refusal naming the key", k, v, err)
			}
		}
	}
}

// #775 names these keys; the boost is query-time and the extraction is not.
func TestGlossaryRuleScopes(t *testing.T) {
	if r, _ := RuleFor("TEXT_SYMBOL_GLOSSARY_BOOST"); r.Scope != ScopeAPIRestart ||
		!strings.Contains(r.Help, "no re-ingest") {
		t.Errorf("TEXT_SYMBOL_GLOSSARY_BOOST rule = %+v", r)
	}
	if r, _ := RuleFor("SYMBOL_GLOSSARY_EXTRACTION"); r.Scope != ScopeNewIngests ||
		!strings.Contains(r.Help, "backfill") {
		t.Errorf("SYMBOL_GLOSSARY_EXTRACTION rule = %+v", r)
	}
}
