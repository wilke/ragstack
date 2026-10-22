package settings

import (
	"os"
	"regexp"
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
		// Decimal forms Python's float() and pydantic also accept (#781 review).
		"TEXT_SYMBOL_GLOSSARY_BOOST": {"0", "1", "1.5", "1_000", "+1", ".5", "5.", "1e3", "1E-2", "-0", "2.5e+1"},
		"SYMBOL_GLOSSARY_EXTRACTION": {"off", "rules"},
		"SYMBOL_GLOSSARY_CONFIG_JSON": {"", `{}`, `{"definition_window":200}`,
			`{"attach_only_with_value":true,"rule_respectively":false}`},
		"DUPLICATE_POLICY":          {"refuse", "allow", "off", "Refuse"},
		"DOI_ENRICHMENT_PUBMED_IDS": {"true", "false", "1", "0", "yes", "Off", "TRUE"},
		"LOG_LEVEL":                 {"anything", ""}, // no rule: any string
	}
	for k, vs := range ok {
		for _, v := range vs {
			if err := ValidatePublic(k, v); err != nil {
				t.Errorf("ValidatePublic(%s, %q) = %v, want accepted", k, v, err)
			}
		}
	}
	bad := map[string][]string{
		"TEXT_SYMBOL_GLOSSARY_BOOST": {"-1", "-0.001", "x", "", "NaN", "+Inf", "inf", "1,5",
			// hex floats: strconv.ParseFloat takes them, pydantic does not
			"0x1p4", "0X10", "0x1.8p1", "-0x1p-2",
			// malformed decimals / underscores Python refuses
			"1__0", "_1", "1_", ".", "e3", "1e",
			// whitespace and control characters (ValidatePublic, for every ruled key)
			" 2 ", "1 ", "1\n", "1\r"},
		"SYMBOL_GLOSSARY_EXTRACTION": {"on", "llm", "RULES", "", " rules", "rules\n"},
		"SYMBOL_GLOSSARY_CONFIG_JSON": {"{", "null", `"x"`, "[]", `{"definiton_window":200}`, " {}",
			"{}\n"},
		"DUPLICATE_POLICY":          {"skip", "", " refuse", "allow\n"},
		"DOI_ENRICHMENT_PUBMED_IDS": {"maybe", "", " true ", "1 ", "true\n", "\ttrue"},
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

// glossaryPy is python/ragstack/ingestion/symbol_glossary.py.
const glossaryPy = "../../../../python/ragstack/ingestion/symbol_glossary.py"

// TestGlossaryConfigKeysMatchPython: the keys SYMBOL_GLOSSARY_CONFIG_JSON may
// carry are exactly GlossaryConfig's fields, so a field added in Python is
// not refused by the ctl and a field removed is not accepted.
func TestGlossaryConfigKeysMatchPython(t *testing.T) {
	b, err := os.ReadFile(glossaryPy)
	if err != nil {
		t.Fatalf("read %s: %v", glossaryPy, err)
	}
	src := string(b)
	start := strings.Index(src, "class GlossaryConfig:")
	end := strings.Index(src, "\nDEFAULT_CONFIG = GlossaryConfig()")
	if start < 0 || end < start {
		t.Fatalf("%s: cannot find the GlossaryConfig body", glossaryPy)
	}
	field := regexp.MustCompile(`(?m)^    ([a-z][a-z0-9_]*)\s*:`)
	py := map[string]bool{}
	for _, m := range field.FindAllStringSubmatch(src[start:end], -1) {
		py[m[1]] = true
	}
	if len(py) < 5 {
		t.Fatalf("parsed only %d GlossaryConfig fields", len(py))
	}
	for k := range py {
		if !glossaryConfigKeys[k] {
			t.Errorf("GlossaryConfig.%s is missing from glossaryConfigKeys (rules.go)", k)
		}
	}
	for k := range glossaryConfigKeys {
		if !py[k] {
			t.Errorf("glossaryConfigKeys lists %s, which is not a GlossaryConfig field", k)
		}
	}
}
