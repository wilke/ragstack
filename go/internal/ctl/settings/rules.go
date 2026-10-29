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
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
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

// oneOfFold is oneOf after config.py's lower() (ValidatePublic has already
// refused surrounding whitespace).
func oneOfFold(allowed ...string) func(string) error {
	inner := oneOf(allowed...)
	return func(v string) error { return inner(strings.ToLower(v)) }
}

// decimalFloat is the DECIMAL float syntax Python's float() and pydantic
// accept: an optional sign, digits with single underscores between them, an
// optional fraction ("5.", ".5"), an optional exponent. strconv.ParseFloat
// alone would also take hex floats ("0x1p4" = 16), which pydantic refuses.
var decimalFloat = regexp.MustCompile(
	`^[+-]?(\d(_?\d)*(\.(\d(_?\d)*)?)?|\.\d(_?\d)*)([eE][+-]?\d(_?\d)*)?$`)

// nonNegativeFloat is a finite decimal float >= 0. NaN, infinities and
// negatives parse in pydantic but are refused here deliberately: none is a
// meaningful boost.
func nonNegativeFloat(v string) error {
	switch strings.TrimLeft(strings.ToLower(v), "+-") {
	case "nan", "inf", "infinity":
		return fmt.Errorf("%q must be a finite number >= 0", v)
	}
	if !decimalFloat.MatchString(v) {
		return fmt.Errorf("%q is not a number (decimal notation)", v)
	}
	f, err := strconv.ParseFloat(strings.ReplaceAll(v, "_", ""), 64)
	if err != nil {
		return fmt.Errorf("%q is not a number (decimal notation)", v)
	}
	if math.IsNaN(f) || math.IsInf(f, 0) || f < 0 {
		return fmt.Errorf("%q must be a finite number >= 0", v)
	}
	return nil
}

// pydanticBool is the set of strings pydantic accepts for a bool field
// (case-insensitive, as pydantic folds it; no surrounding whitespace).
func pydanticBool(v string) error {
	switch strings.ToLower(v) {
	case "0", "off", "f", "false", "n", "no", "1", "on", "t", "true", "y", "yes":
		return nil
	}
	return fmt.Errorf("%q is not a boolean (true|false|1|0|yes|no|on|off)", v)
}

// glossaryConfigKeys are the field names of GlossaryConfig
// (python/ragstack/ingestion/symbol_glossary.py). config_from_json ignores an
// unknown key with only a log warning, so a typo would look applied.
// TestGlossaryConfigKeysMatchPython keeps the two in step.
var glossaryConfigKeys = set(
	"max_definitions_per_symbol", "max_definition_words", "max_entries_per_chunk", "max_chars_per_chunk",
	"allow_single_letter", "positional", "attach_only_with_value", "definition_window",
	"rule_where", "rule_appositive", "rule_is_the", "rule_parenthetical", "rule_respectively",
)

// glossaryConfigJSON is empty ("the defaults") or a JSON object whose keys are
// all GlossaryConfig fields. Value types are coerced (or refused, with a
// warning) by the API's config_from_json.
func glossaryConfigJSON(v string) error {
	if v == "" {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(v), &m); err != nil || m == nil {
		return fmt.Errorf("not a JSON object (the API would silently fall back to the defaults)")
	}
	for k := range m {
		if !glossaryConfigKeys[k] {
			return fmt.Errorf("%q is not a GlossaryConfig key (the API would ignore it); known: %s",
				k, strings.Join(keys(glossaryConfigKeys), ", "))
		}
	}
	return nil
}

// glossaryLLMKnobs are the keys SYMBOL_GLOSSARY_LLM_INPUTS_JSON may set and
// their Python types — python/ragstack/ingestion/glossary_inputs.py's
// KNOB_TYPES, kept in step by TestGlossaryLLMKnobsMatchPython. The API refuses
// to BOOT on anything else, so the ctl refuses it at `env set` instead.
var glossaryLLMKnobs = map[string]string{
	"glossary_llm_concurrency":   "int",
	"glossary_llm_timeout_s":     "float",
	"glossary_llm_max_tokens":    "int",
	"glossary_llm_window_chars":  "int",
	"glossary_llm_max_windows":   "int",
	"glossary_llm_thinking":      "str",
	"glossary_llm_repair_rounds": "int",
	"glossary_llm_cutoff_retry":  "str",
	"glossary_llm_deadline_s":    "float",
	"glossary_llm_min_chars":     "int",
	"glossary_llm_seed":          "int",
	"glossary_llm_extra_body":    "str",
}

// glossaryLLMChoices are glossary_inputs.KNOB_CHOICES.
var glossaryLLMChoices = map[string][]string{
	"glossary_llm_thinking":     {"off", "on", "unset"},
	"glossary_llm_cutoff_retry": {"off", "split"},
}

// glossaryLLMPositive must be > 0 when given (the tool refuses 0 with --llm on);
// glossaryWindowOverlap is symbol_glossary_llm.WINDOW_OVERLAP.
var glossaryLLMPositive = set("glossary_llm_timeout_s", "glossary_llm_max_tokens",
	"glossary_llm_window_chars", "glossary_llm_max_windows")

const glossaryWindowOverlap = 2000

// glossaryInputsJSON is empty or a JSON object of known knobs with the API's
// types (glossary_inputs.glossary_settings_problems): an int is a JSON
// integer, a float any number, never a boolean, none negative; the choices;
// the required limits > 0; the window longer than the overlap; extra_body a
// JSON-object string that sets no request field the step owns and carries no
// credential-like key.
func glossaryInputsJSON(v string) error {
	if v == "" {
		return nil
	}
	dec := json.NewDecoder(strings.NewReader(v))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil || m == nil {
		return fmt.Errorf("not a JSON object (the API refuses to boot)")
	}
	for _, k := range keysOf(m) {
		want, known := glossaryLLMKnobs[k]
		if !known {
			return fmt.Errorf("%q is not a glossary step knob (the API refuses to boot); known: %s",
				k, strings.Join(keysOf(glossaryLLMKnobs), ", "))
		}
		if err := glossaryKnobValue(k, want, m[k]); err != nil {
			return err
		}
	}
	return nil
}

func glossaryKnobValue(k, want string, raw any) error {
	switch want {
	case "int", "float":
		n, ok := raw.(json.Number)
		if !ok {
			return fmt.Errorf("%s must be %s", k, want)
		}
		f, err := n.Float64()
		if err != nil {
			return fmt.Errorf("%s must be %s", k, want)
		}
		if want == "int" {
			if _, err := n.Int64(); err != nil {
				return fmt.Errorf("%s must be int", k)
			}
		}
		if f < 0 {
			return fmt.Errorf("%s must be >= 0", k)
		}
		if glossaryLLMPositive[k] && f <= 0 {
			return fmt.Errorf("%s must be > 0 with llm on", k)
		}
		if k == "glossary_llm_window_chars" && f <= glossaryWindowOverlap {
			return fmt.Errorf("%s must be > %d (the window overlap)", k, glossaryWindowOverlap)
		}
	case "str":
		s, ok := raw.(string)
		if !ok {
			return fmt.Errorf("%s must be str", k)
		}
		if choices, ok := glossaryLLMChoices[k]; ok && !contains(choices, s) {
			return fmt.Errorf("%s must be one of %s", k, strings.Join(choices, "|"))
		}
		if k == "glossary_llm_extra_body" {
			return glossaryExtraBody(s)
		}
	}
	return nil
}

// glossaryCoreFields are symbol_glossary_llm.CORE_REQUEST_FIELDS.
var glossaryCoreFields = set("model", "messages", "max_tokens", "temperature", "seed")

// The credential-like key rule of symbol_glossary_llm._secret_like.
var (
	glossarySecretKeys = set("api_key", "apikey", "authorization", "auth", "token", "secret",
		"password", "passwd", "bearer", "credential", "credentials", "x_api_key", "key")
	glossarySecretSuffixes = []string{"_token", "_secret", "_password", "_api_key", "_apikey", "_key",
		"_authorization", "_credential", "_credentials"}
	camelBoundary = regexp.MustCompile(`([a-z0-9])([A-Z])|([A-Z])([A-Z][a-z])`)
)

func secretLikeKey(key string) bool {
	k := camelBoundary.ReplaceAllString(strings.TrimSpace(key), "${1}${3}_${2}${4}")
	k = strings.ReplaceAll(strings.ToLower(k), "-", "_")
	if glossarySecretKeys[k] {
		return true
	}
	for _, s := range glossarySecretSuffixes {
		if strings.HasSuffix(k, s) {
			return true
		}
	}
	return false
}

func secretPaths(obj any, schemaNames bool) []string {
	var out []string
	switch o := obj.(type) {
	case map[string]any:
		for k, v := range o {
			if !schemaNames && secretLikeKey(k) {
				out = append(out, k)
			}
			_, isMap := v.(map[string]any)
			out = append(out, secretPaths(v, !schemaNames && k == "properties" && isMap)...)
		}
	case []any:
		for _, v := range o {
			out = append(out, secretPaths(v, false)...)
		}
	}
	return out
}

func glossaryExtraBody(s string) error {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	var obj map[string]any
	if err := json.Unmarshal([]byte(s), &obj); err != nil || obj == nil {
		return fmt.Errorf("glossary_llm_extra_body must be a JSON object string")
	}
	for k := range obj {
		if glossaryCoreFields[k] {
			return fmt.Errorf("glossary_llm_extra_body may not set %q: the step sets it itself", k)
		}
	}
	if p := secretPaths(obj, false); len(p) > 0 {
		sort.Strings(p)
		return fmt.Errorf("glossary_llm_extra_body may not carry credential-like keys %v", p)
	}
	return nil
}

// glossaryModel is a served model id: non-empty, no whitespace, no control
// character (it is sent verbatim as the request's `model`).
func glossaryModel(v string) error {
	if v == "" {
		return fmt.Errorf("empty (with SYMBOL_GLOSSARY_EXTRACTION=llm the API refuses to boot)")
	}
	for _, r := range v {
		if unicode.IsSpace(r) || unicode.IsControl(r) {
			return fmt.Errorf("a model id has no whitespace or control character")
		}
	}
	return nil
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

const glossaryNote = " The glossary is a separate BM25 field (`symbol_glossary`) on the text index; " +
	"see docs/plans/symbol-glossary.md."

var publicRules = map[string]PublicRule{
	// #828: document-structure metadata on chunks.
	"STRUCTURE_METADATA": {
		Check: oneOf("off", "on"),
		Scope: ScopeNewIngests,
		Help: "off|on (default off): stamp section/role/block metadata (section_title, section_path, " +
			"section_level, section_roles, block_types, caption_labels, refers_to, structure_source, " +
			"structure_version) on the chunks of documents that carry a structure. Payload fields only: " +
			"content, chunk ids and vectors are unchanged, so it forks no collection. Applies to documents " +
			"ingested after `tenant restart <t> --only api`, in-process, and on a gowe tenant only when the " +
			"registered workflow declares the `structure_metadata` input and names a stamped tools image. " +
			"Chunks already indexed are never re-stamped (no backfill); a collection re-ingested into is " +
			"then partially covered. See docs/plans/828-document-structure.md.",
	},
	// #754/#763: the per-document symbol glossary.
	"SYMBOL_GLOSSARY_EXTRACTION": {
		Check: oneOf("off", "rules", "llm"),
		Scope: ScopeNewIngests,
		Help: "off|rules|llm (default rules, #783). llm (#847 G2b): a gowe tenant's workflow " +
			"glossary step also asks SYMBOL_GLOSSARY_LLM_ENDPOINT/_MODEL (both required, or the API " +
			"refuses to boot); in-process it reads as rules. Applies to documents ingested after " +
			"`tenant restart <t> --only api`, on both ingest paths: in-process, and a gowe tenant's worker " +
			"ingest, which receives it as the `symbol_glossary` workflow input when the registered " +
			"pdf-ingest-scatter workflow declares it. Chunks already indexed are unchanged until they are " +
			"re-ingested or backfilled; backfill before raising TEXT_SYMBOL_GLOSSARY_BOOST above 0. " +
			"scripts/backfill_symbol_glossary.py does NOT read it (it takes --config and --llm-cache on its " +
			"command line)." + glossaryNote,
	},
	"SYMBOL_GLOSSARY_CONFIG_JSON": {
		Check: glossaryConfigJSON,
		Scope: ScopeNewIngests,
		Help: "GlossaryConfig overrides as a JSON object (empty = defaults); every key must be a " +
			"GlossaryConfig field. Unlike SYMBOL_GLOSSARY_EXTRACTION, it affects only in-process API ingests " +
			"after `tenant restart <t> --only api` (a gowe worker runs the default config); the backfill " +
			"script takes its own --config." + glossaryNote,
	},
	// #847 G2b: the glossary step's model and limits (gowe, llm mode).
	"SYMBOL_GLOSSARY_LLM_MODEL": {
		Check: glossaryModel,
		Scope: ScopeNewIngests,
		Help: "the served model id the glossary step asks (#847 G2b), sent as a workflow input with " +
			"SYMBOL_GLOSSARY_EXTRACTION=llm on a gowe tenant; required there (no fallback to LLM_MODEL). " +
			"Applies to ingests after `tenant restart <t> --only api`.",
	},
	"SYMBOL_GLOSSARY_LLM_INPUTS_JSON": {
		Check: glossaryInputsJSON,
		Scope: ScopeNewIngests,
		Help: "the glossary step's limits as a JSON object keyed by the workflow input names " +
			"(glossary_llm_concurrency, _timeout_s, _max_tokens, _window_chars, _max_windows, _thinking, " +
			"_repair_rounds, _cutoff_retry, _deadline_s, _min_chars, _seed, _extra_body); omitted keys take " +
			"the API's defaults. Concurrency is the tenant's TOTAL in flight, split across a run's batches. " +
			"Applies to ingests after `tenant restart <t> --only api`.",
	},
	"TEXT_SYMBOL_GLOSSARY_BOOST": {
		Check: nonNegativeFloat,
		Scope: ScopeAPIRestart,
		Help: "a number >= 0; 0 (the default) leaves the BM25 query byte-identical to before #754. " +
			"Query-time: it takes effect at `tenant restart <t> --only api`, with no re-ingest. On an index " +
			"without the field it is a no-op, so backfill first. Trial only with the reranker on." + glossaryNote,
	},
	"RERANK_INCLUDE_SYMBOL_GLOSSARY": {
		Check: pydanticBool,
		Scope: ScopeAPIRestart,
		Help: "true|false (default true): the cross-encoder scores a chunk's content plus its " +
			"`symbol_glossary` instead of content alone; a chunk without a glossary is scored exactly as " +
			"before. Query-time: it takes effect at `tenant restart <t> --only api`, with no re-ingest. It " +
			"only matters for chunks that carry a glossary (extracted since v1.6.12 or backfilled), is " +
			"independent of TEXT_SYMBOL_GLOSSARY_BOOST, and changes reranked result ORDER on those " +
			"collections (never a source's content) — tell evaluation users before changing it." + glossaryNote,
	},
	"RERANK_GLOSSARY_TIMEOUT": {
		Check: nonNegativeFloat,
		Scope: ScopeAPIRestart,
		Help: "seconds, a number >= 0 (default 2): the bound on the one Elasticsearch lookup that gives " +
			"vector-only rerank candidates their glossary when RERANK_INCLUDE_SYMBOL_GLOSSARY is on. With ES " +
			"down or slow a reranked query waits at most this long and reranks without the glossary. 0 turns " +
			"the lookup off (only BM25 hits are scored with their glossary). Query-time: it takes effect at " +
			"`tenant restart <t> --only api`, with no re-ingest." + glossaryNote,
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
	// As ValidateSurface: no surrounding whitespace and no control character.
	// pydantic does not strip a bool or float, so " true " would pass here
	// and fail Settings() at the next boot; a newline would pass the plan and
	// fail when the env file is written.
	if strings.TrimSpace(value) != value || strings.ContainsAny(value, "\n\r\x00") {
		return fmt.Errorf("%s=%q: the value has leading or trailing whitespace or a control character", key, value)
	}
	if err := r.Check(value); err != nil {
		return fmt.Errorf("%s=%s: %v", key, value, err)
	}
	return nil
}
