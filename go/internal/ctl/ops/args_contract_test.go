package ops

import (
	"os"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The contract is the source of truth for `x-ctl-op-args`; the Go table in
// args.go is what the daemon actually enforces. This file parses the ONE
// block of the OpenAPI document the table mirrors and asserts they are the
// same thing, field by field.
//
// It parses rather than imports because the module has no YAML dependency and
// the ctl deliberately ships none — the binary is static and has no runtime
// but the standard library. The parser below reads only the shape this block
// is written in (two-space indentation, one flow mapping per property), and
// it FAILS rather than guesses when it meets anything else, so a contract
// rewritten in another style breaks the test instead of silently passing it.

const contractPath = "../../../../contracts/ctl/openapi.yaml"

// contractProp is one property of one verb as the contract spells it.
type contractProp struct {
	Type      string
	Enum      []string
	Pattern   string
	MaxLength int
	Unique    bool
	ItemType  string
	ItemEnum  []string
}

type contractSpec struct {
	Required []string
	Props    map[string]contractProp
	AddlOK   bool
}

func TestArgSchemaMatchesContract(t *testing.T) {
	contract := parseOpArgs(t)

	// 1. the verb set.
	var have []string
	for v := range contract {
		have = append(have, v)
	}
	sort.Strings(have)
	want := append([]string(nil), ContractVerbs...)
	sort.Strings(want)
	if !reflect.DeepEqual(have, want) {
		t.Fatalf("x-ctl-op-args verbs =\n%v\nContractVerbs =\n%v", have, want)
	}
	for _, v := range have {
		if _, ok := argSchemas[v]; !ok {
			t.Errorf("no argSchemas row for contract verb %q", v)
		}
	}

	// 2. field by field.
	for _, verb := range have {
		cs := contract[verb]
		spec := argSchemas[verb]
		if cs.AddlOK {
			t.Errorf("%s: the contract allows additionalProperties; the table refuses them unconditionally", verb)
		}
		if got, want := spec.required(), sortedCopy(cs.Required); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: required = %v, contract says %v", verb, got, want)
		}
		if got, want := spec.names(), propNames(cs.Props); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: properties = %v, contract says %v", verb, got, want)
		}
		for name, cp := range cs.Props {
			f, ok := spec.field(name)
			if !ok {
				continue // already reported by the property-name comparison
			}
			if got := f.Kind.String(); got != cp.Type {
				t.Errorf("%s.%s: type %q, contract says %q", verb, name, got, cp.Type)
			}
			if !reflect.DeepEqual(sortedCopy(f.Enum), sortedCopy(cp.Enum)) {
				t.Errorf("%s.%s: enum %v, contract says %v", verb, name, f.Enum, cp.Enum)
			}
			if f.Pattern != cp.Pattern {
				t.Errorf("%s.%s: pattern %q, contract says %q", verb, name, f.Pattern, cp.Pattern)
			}
			if f.MaxLength != cp.MaxLength {
				t.Errorf("%s.%s: maxLength %d, contract says %d", verb, name, f.MaxLength, cp.MaxLength)
			}
			if f.Unique != cp.Unique {
				t.Errorf("%s.%s: uniqueItems %v, contract says %v", verb, name, f.Unique, cp.Unique)
			}
			if !reflect.DeepEqual(sortedCopy(f.ItemEnum), sortedCopy(cp.ItemEnum)) {
				t.Errorf("%s.%s: items.enum %v, contract says %v", verb, name, f.ItemEnum, cp.ItemEnum)
			}
		}
	}
}

// Every verb of the contract enum is a verb the registry answers, and the
// three extra entry points are exactly the three the plan names.
func TestRegistryAnswersEveryContractVerb(t *testing.T) {
	r := NewRegistry(Deps{})
	for _, v := range ContractVerbs {
		op, ok := r.Lookup(v)
		if !ok {
			t.Errorf("no Op for contract verb %q", v)
			continue
		}
		if op.Verb() != v {
			t.Errorf("Lookup(%q).Verb() = %q", v, op.Verb())
		}
	}
	extra := []string{"create", "gateway-apply", "gateway-reload", "settings-put"}
	if got, want := len(r.Verbs()), len(ContractVerbs)+len(extra); got != want {
		t.Errorf("registry has %d verbs, want %d (the enum plus %v)", got, want, extra)
	}
	for _, v := range extra {
		if _, ok := r.Lookup(v); !ok {
			t.Errorf("no Op for %q", v)
		}
	}
	if _, ok := r.Lookup("purge"); ok {
		t.Error("the registry answers a verb nobody defined")
	}
}

// ---------------------------------------------------------------- the parser

func parseOpArgs(t *testing.T) map[string]contractSpec {
	t.Helper()
	b, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("reading the contract: %v", err)
	}
	lines := strings.Split(string(b), "\n")
	start := -1
	for i, l := range lines {
		if l == "x-ctl-op-args:" {
			start = i + 1
			break
		}
	}
	if start < 0 {
		t.Fatal("x-ctl-op-args not found in the contract")
	}
	out := map[string]contractSpec{}
	verb, inProps := "", false
	for _, raw := range lines[start:] {
		if strings.TrimSpace(raw) == "" {
			continue
		}
		indent := len(raw) - len(strings.TrimLeft(raw, " "))
		if indent == 0 {
			break // the next top-level key
		}
		line := strings.TrimSpace(raw)
		switch indent {
		case 2:
			if !strings.HasSuffix(line, ":") {
				t.Fatalf("unexpected verb line %q", raw)
			}
			verb = strings.TrimSuffix(line, ":")
			out[verb] = contractSpec{Props: map[string]contractProp{}}
			inProps = false
		case 4:
			key, value := splitKV(t, line)
			switch key {
			case "type":
				if value != "object" {
					t.Fatalf("%s: args type %q, want object", verb, value)
				}
				inProps = false
			case "required":
				s := out[verb]
				s.Required = flowStrings(t, value)
				out[verb] = s
				inProps = false
			case "additionalProperties":
				s := out[verb]
				s.AddlOK = value != "false"
				out[verb] = s
				inProps = false
			case "properties":
				inProps = true
				if value != "" && value != "{}" {
					t.Fatalf("%s: unexpected properties value %q", verb, value)
				}
			default:
				t.Fatalf("%s: unexpected key %q", verb, key)
			}
		case 6:
			if !inProps {
				t.Fatalf("%s: property %q outside a properties block", verb, line)
			}
			name, value := splitKV(t, line)
			m, ok := parseFlow(t, value).(map[string]any)
			if !ok {
				t.Fatalf("%s.%s: expected a flow mapping, got %q", verb, name, value)
			}
			out[verb].Props[name] = contractPropOf(t, verb, name, m)
		default:
			t.Fatalf("unexpected indentation %d in %q", indent, raw)
		}
	}
	return out
}

func contractPropOf(t *testing.T, verb, name string, m map[string]any) contractProp {
	t.Helper()
	p := contractProp{}
	for k, v := range m {
		switch k {
		case "description", "default":
			// documentation and client-side defaults: the daemon applies
			// neither, it only refuses what does not fit.
		case "type":
			p.Type = str(v)
		case "pattern":
			p.Pattern = str(v)
		case "enum":
			p.Enum = strList(v)
		case "uniqueItems":
			p.Unique = str(v) == "true"
		case "maxLength":
			n, err := strconv.Atoi(str(v))
			if err != nil {
				t.Fatalf("%s.%s: maxLength %q", verb, name, str(v))
			}
			p.MaxLength = n
		case "items":
			im, ok := v.(map[string]any)
			if !ok {
				t.Fatalf("%s.%s: items is not a mapping", verb, name)
			}
			p.ItemType, p.ItemEnum = str(im["type"]), strList(im["enum"])
		default:
			t.Fatalf("%s.%s: unhandled schema keyword %q — the table cannot be compared against it", verb, name, k)
		}
	}
	return p
}

// splitKV splits "key: value" at the FIRST colon-space, which is safe here
// because every key in this block is a bare identifier.
func splitKV(t *testing.T, line string) (string, string) {
	t.Helper()
	i := strings.Index(line, ":")
	if i < 0 {
		t.Fatalf("no key in %q", line)
	}
	return line[:i], strings.TrimSpace(line[i+1:])
}

// parseFlow reads YAML flow syntax: {a: b, c: [d, e]} and scalars, with
// double-quoted strings kept whole.
func parseFlow(t *testing.T, s string) any {
	t.Helper()
	v, rest := scanValue(t, strings.TrimSpace(s))
	if strings.TrimSpace(rest) != "" {
		t.Fatalf("trailing input %q in %q", rest, s)
	}
	return v
}

func scanValue(t *testing.T, s string) (any, string) {
	t.Helper()
	s = strings.TrimLeft(s, " ")
	switch {
	case s == "":
		return "", ""
	case s[0] == '{':
		m := map[string]any{}
		s = strings.TrimLeft(s[1:], " ")
		for len(s) > 0 && s[0] != '}' {
			i := strings.Index(s, ":")
			if i < 0 {
				t.Fatalf("no key in flow mapping %q", s)
			}
			key := strings.TrimSpace(s[:i])
			var v any
			v, s = scanValue(t, s[i+1:])
			m[key] = v
			s = strings.TrimLeft(s, " ")
			if len(s) > 0 && s[0] == ',' {
				s = strings.TrimLeft(s[1:], " ")
			}
		}
		if len(s) == 0 {
			t.Fatalf("unterminated flow mapping")
		}
		return m, s[1:]
	case s[0] == '[':
		var list []any
		s = strings.TrimLeft(s[1:], " ")
		for len(s) > 0 && s[0] != ']' {
			var v any
			v, s = scanValue(t, s)
			list = append(list, v)
			s = strings.TrimLeft(s, " ")
			if len(s) > 0 && s[0] == ',' {
				s = strings.TrimLeft(s[1:], " ")
			}
		}
		if len(s) == 0 {
			t.Fatalf("unterminated flow sequence")
		}
		return list, s[1:]
	case s[0] == '"':
		var sb strings.Builder
		i := 1
		for ; i < len(s); i++ {
			if s[i] == '\\' && i+1 < len(s) {
				// The contract's patterns carry `\\s` and `\\.`; YAML's
				// double-quoted escapes collapse the pair, which is exactly
				// what the Go regexp source has to be.
				sb.WriteByte(s[i+1])
				i++
				continue
			}
			if s[i] == '"' {
				break
			}
			sb.WriteByte(s[i])
		}
		if i >= len(s) {
			t.Fatalf("unterminated quoted scalar in %q", s)
		}
		return sb.String(), s[i+1:]
	default:
		end := strings.IndexAny(s, ",}]")
		if end < 0 {
			end = len(s)
		}
		return strings.TrimSpace(s[:end]), s[end:]
	}
}

func flowStrings(t *testing.T, s string) []string {
	t.Helper()
	return strList(parseFlow(t, s))
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

func strList(v any) []string {
	list, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, it := range list {
		out = append(out, str(it))
	}
	return out
}

func sortedCopy(ss []string) []string {
	if len(ss) == 0 {
		return nil
	}
	out := append([]string(nil), ss...)
	sort.Strings(out)
	return out
}

func propNames(m map[string]contractProp) []string {
	if len(m) == 0 {
		return []string{"(none)"}
	}
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
