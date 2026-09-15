"""Named server-side prompt templates — the level-2 seam from ADR-0008.

`/v1/query` has exactly one prompt (``llm._SYSTEM_PROMPT``) and no way to change
it, so the literature console (#544) builds its extraction prompt in the browser
and posts it to a *second* service that fronts the same model this tenant is
already configured for. ADR-0008 closes that gap without opening a general text
box: the operator writes templates into a reviewable file, the caller **selects**
one by id and **fills declared slots**, and never authors prompt text.

This module is the whole of that mechanism and none of its wiring: load +
validate a file, render a selected template against caller values, and project a
template onto the public wire shape. It imports nothing from ``ragstack.api``
and knows nothing about HTTP; the router that maps :class:`TemplateRenderError`
onto 422 lives elsewhere.

Three properties are the reason this file is worth reading carefully:

**Validation happens at LOAD, not at request time** (ADR-0008 decision 3 rule 5).
Every authoring mistake this module can detect — an undeclared slot reference, a
missing length cap, a marker in ``system`` — raises :class:`TemplateValidationError`
from :func:`load_templates`, so a bad file fails the boot loudly instead of
500ing whichever caller happens to select that template first. The asymmetry is
deliberate: an operator reading a stack trace at startup can fix the file; a user
seeing a 500 cannot.

**Slot values are values, never source** (rule 1). :func:`render` walks a tree
that was parsed from the template body and *appends* resolved values to an output
buffer. A value is never scanned for markers, so ``{{context}}`` inside a value
stays inert text — not because it is escaped, but because there is no second pass
in which it could be seen. That is a structural guarantee, and the reason the
renderer is hand-rolled rather than delegated to a templating library whose
default is usually the opposite.

**What this does NOT do:** it does not solve prompt injection, and ADR-0008 says
so at length. Rule 4 (no substitution in ``system``, enforced here at load) keeps
caller text out of the operator's framing. It does nothing about a template
author who interpolates a slot into an instruction position in ``user``. What is
bounded here is abuse and non-reproducibility, not adversarial input.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

#: Names that resolve from server state rather than from ``template_vars``.
#: They are NOT slots: a template may reference them, but a caller may never set
#: them — a ``vars`` key named ``context`` is rejected exactly like a typo would
#: be. Were they settable, a caller could replace the retrieved passages with
#: text of its own, which is level 3 (refused) wearing a level-2 costume.
RESERVED_NAMES = frozenset({"context", "columns", "label"})

#: Kept in step with `id` in contracts/schemas/prompt_templates_response.json.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: Sanity bound on a template's declared output ceiling. Not a model limit —
#: models differ — just wide enough for any real table and narrow enough to
#: catch a fat-fingered extra zero at load rather than at request time.
_MAX_OUTPUT_TOKENS_CEILING = 100_000

#: Slot names are lowercase identifiers. Narrow on purpose: the name appears in
#: a JSON request body, in the marker syntax, and in error messages, and a
#: charset that admits ``{{ foo-bar }}`` invites the reader to guess which of
#: several plausible spellings the template meant.
_SLOT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: A well-formed marker: ``{{name}}``, ``{{#name}}``, ``{{/name}}``. No internal
#: whitespace is tolerated — see :func:`_parse` for why a *near*-marker is an
#: error rather than literal text.
_TOKEN_RE = re.compile(r"\{\{(#|/)?([a-z][a-z0-9_]*)\}\}")

_OUTPUTS = ("text", "table")

# Keys accepted in a template record / a slot record. Checked strictly because
# `contracts/schemas/prompt_templates_response.json` is `additionalProperties:
# false` on the way out, and because the realistic typo here is `slot:` for
# `slots:` — which, tolerated, would silently load a template with no declared
# slots and no length caps.
_TEMPLATE_KEYS = frozenset(
    {
        "id", "version", "label", "output", "columns", "slots", "system", "user",
        "max_output_tokens",
    }
)
_SLOT_KEYS = frozenset({"name", "required", "max_len", "label"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PromptTemplateError(Exception):
    """Base for everything this module raises."""


class TemplateValidationError(PromptTemplateError):
    """A template file is malformed. Raised by :func:`load_templates`, i.e. at
    startup — never in a request path. The message always names the offending
    template and field, because the reader is an operator staring at a boot
    failure with no request to inspect."""


class TemplateRenderError(PromptTemplateError):
    """Caller-supplied values do not fit the selected template. The API maps
    this to **422** (ADR-0008 decision 3 rule 3): an undeclared or oversized
    value is a client error, and answering it with a silently truncated or
    empty clause is the worst available outcome."""

    def __init__(self, message: str, *, template_id: str, slot: str | None = None) -> None:
        super().__init__(message)
        self.template_id = template_id
        self.slot = slot


# ---------------------------------------------------------------------------
# The data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """One named value a template accepts in ``template_vars``.

    ``max_len`` has no default and :func:`load_templates` refuses a template that
    omits it (ADR-0008 decision 3 rule 2). A default would be a cap nobody chose,
    applied to a field whose right cap is domain knowledge — an organism name is
    not an essay — and the failure mode of a too-generous default is a prompt
    bloated past the model's context by caller-supplied text.
    """

    name: str
    required: bool
    max_len: int
    label: str | None = None


@dataclass(frozen=True)
class PromptTemplate:
    """A loaded, validated template. Immutable: templates are deployment
    configuration read once at startup, and a mutable one would let a request
    path edit what every later request renders."""

    id: str
    version: int
    label: str
    output: Literal["text", "table"]
    columns: tuple[str, ...] | None
    slots: tuple[Slot, ...]
    system: str
    user: str
    hash: str
    #: Ceiling on GENERATED tokens for this template, or None to use the server's
    #: llm_max_output_tokens. A table template needs materially more room than a
    #: prose one: it asks for as many rows as the literature supports, and a cap
    #: that is comfortable for a paragraph silently truncates the table mid-row.
    max_output_tokens: int | None = None

    def slot(self, name: str) -> Slot | None:
        return next((s for s in self.slots if s.name == name), None)


# ---------------------------------------------------------------------------
# Parsing the `user` body
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Text:
    text: str


@dataclass(frozen=True)
class _Var:
    name: str


@dataclass(frozen=True)
class _Section:
    name: str
    body: tuple[_Node, ...]


_Node = _Text | _Var | _Section


def _parse(body: str) -> tuple[_Node, ...]:
    """Parse a ``user`` body into nodes, or raise ``ValueError`` describing the
    first problem. Callers add the template's identity to the message.

    **The same parse runs at load and at render.** :func:`load_templates` parses
    to validate; :func:`render` parses to emit. Neither has its own notion of
    what the syntax means, so a body that survives validation cannot surprise the
    renderer. (Re-parsing per request is a few microseconds on bodies of this
    size; caching it would mean storing a parse on a frozen dataclass whose
    shape is fixed by the contract, and the divergence risk is not worth it.)

    **A near-marker is an error, not text.** ``{{ organism }}``, ``{{Organism}}``
    and ``{{{organism}}}`` are all rejected. Treating them as literal text is the
    exact failure ADR-0008 rule 3 exists to prevent — the prompt renders, the
    model answers, and nothing anywhere says the value never arrived.
    """
    stack: list[tuple[str | None, list[_Node]]] = [(None, [])]
    i = 0
    while True:
        j = body.find("{{", i)
        if j < 0:
            if i < len(body):
                stack[-1][1].append(_Text(body[i:]))
            break
        if j > i:
            stack[-1][1].append(_Text(body[i:j]))
        m = _TOKEN_RE.match(body, j)
        if m is None:
            raise ValueError(
                f"unparsable substitution marker at offset {j}: {body[j:j + 24]!r} "
                "(expected {{name}}, {{#name}} or {{/name}}, name matching [a-z][a-z0-9_]*)"
            )
        kind, name = m.group(1), m.group(2)
        if kind == "#":
            if name in RESERVED_NAMES:
                # `{{#context}}` would read as "only when passages were found",
                # a conditional on server state that the template author cannot
                # test and the caller cannot influence. Say no rather than
                # inventing semantics for it.
                raise ValueError(f"reserved name {name!r} cannot open a conditional section")
            stack.append((name, []))
        elif kind == "/":
            open_name = stack[-1][0]
            if open_name is None:
                raise ValueError(f"{{{{/{name}}}}} closes a section that was never opened")
            if open_name != name:
                raise ValueError(f"{{{{/{name}}}}} closes section {open_name!r}")
            _, nodes = stack.pop()
            stack[-1][1].append(_Section(name, tuple(nodes)))
        else:
            stack[-1][1].append(_Var(name))
        i = m.end()
    if len(stack) > 1:
        raise ValueError(f"section {stack[-1][0]!r} is never closed")
    return tuple(stack[0][1])


def _referenced(nodes: Iterable[_Node]) -> set[str]:
    """Every name the body mentions, section conditions included — a slot used
    only as ``{{#genes}}…{{/genes}}`` is referenced even if its value is never
    interpolated."""
    seen: set[str] = set()
    for n in nodes:
        if isinstance(n, _Var):
            seen.add(n.name)
        elif isinstance(n, _Section):
            seen.add(n.name)
            seen |= _referenced(n.body)
    return seen


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


def content_hash(
    *,
    version: int,
    label: str,
    max_output_tokens: int | None = None,
    output: str,
    columns: Iterable[str] | None,
    slots: Iterable[Slot],
    system: str,
    user: str,
) -> str:
    """The ``hash`` of ADR-0008 decision 4: sha256 over a canonical
    serialization of the template's content, hex, first 16 chars.

    **What counts as content.** Anything that can change the bytes sent to the
    model (``system``, ``user``, ``columns``, ``output``, slot ``name``, and
    **``label``**), change which requests are accepted (slot ``required`` and
    ``max_len``), or change **how much of the answer survives**
    (``max_output_tokens`` — two tenants differing only there produce materially
    different tables from one corpus), plus ``version`` so a bump is visible in
    the hash too. ``id``
    is excluded because hashes are only ever compared *within* an id.

    **``label`` IS content, and an earlier version of this had it wrong.** It
    was excluded as "a string for a picker" — but ``label`` is in
    ``RESERVED_NAMES``, so ``{{label}}`` renders into the USER MESSAGE, and three
    of the four templates we ship use it. Editing a label therefore changed the
    prompt the model saw and left the hash identical: two tenants holding
    ``ppi-extraction`` v1 with materially different instructions, both echoing
    the same ``(id, version, hash)``, which is verbatim the drift ADR-0008
    decision 4 exists to make visible. A string that can reach the model is
    content, whatever it is called. A slot's ``label`` stays excluded — that one
    genuinely cannot render.

    **Slot order is content.** Reordering slots cannot change a rendered prompt,
    so this is deliberately conservative — it reports drift that does not affect
    output rather than risk hiding drift that does.

    **Why JSON rather than concatenation.** Joining fields end to end makes
    ``system="ab", user="c"`` and ``system="a", user="bc"`` hash identically;
    a structured, key-sorted encoding cannot.

    **Why 16 chars is enough.** This is a drift detector for hand-copied
    operator config, not a security primitive. Anyone able to author a colliding
    prompt file already controls the tenant's prompts outright.
    """
    payload = {
        "version": version,
        "label": label,
        "output": output,
        "columns": list(columns) if columns is not None else None,
        "system": system,
        "user": user,
        "slots": [
            {"name": s.name, "required": s.required, "max_len": s.max_len} for s in slots
        ],
    }
    # OMITTED WHEN UNSET, not serialized as null. Including the key
    # unconditionally moved the hash of every template that does NOT declare a
    # ceiling — `literature-summary` went b6a671b3a40015cf -> e1f00e1ba9444840
    # while its bytes were untouched and it stayed v1. Two tenants either side of
    # that upgrade would serve the same (id, version) with different hashes: the
    # drift detector firing where nothing drifted, which is the false positive
    # decision 4 exists to avoid.
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    return hashlib.sha256(_canonical_payload_json(payload).encode("utf-8")).hexdigest()[:16]


def _canonical_payload_json(payload: dict[str, Any]) -> str:
    """The exact bytes the hash is taken over. Split out so a test can assert
    what is IN the payload rather than only comparing two hashes — comparing
    hashes could not see an unset ceiling leaking in as an explicit null."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _fail(where: str, field: str, message: str) -> TemplateValidationError:
    return TemplateValidationError(f"{where}: field {field!r}: {message}")


def _read_records(path: Path) -> list[Any]:
    """Decode the file to a list of records. Format is chosen by suffix rather
    than sniffed: a boot-time config file that silently parses as the *other*
    format is a worse outcome than a startup error naming the suffixes we take.
    """
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TemplateValidationError(f"{path}: not valid JSON: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on the install extra
            # pyyaml is a `dev` extra, not a runtime dependency (python/pyproject.toml).
            # Say that plainly here: "No module named 'yaml'" at boot, from a file the
            # operator just wrote, reads as a bug in RAGStack rather than a missing wheel.
            raise TemplateValidationError(
                f"{path}: reading a YAML template file needs PyYAML installed "
                "(pip install pyyaml  # or reinstall ragstack, which requires it), or write the file as .json"
            ) from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise TemplateValidationError(f"{path}: not valid YAML: {exc}") from exc
    else:
        raise TemplateValidationError(
            f"{path}: unsupported template file suffix {path.suffix!r} (expected .yaml, .yml or .json)"
        )
    if data is None:
        # An empty document. A tenant that configured the file but has not written
        # a template yet is a legitimate state; the endpoint answers `[]`.
        return []
    if not isinstance(data, list):
        raise TemplateValidationError(
            f"{path}: expected a list of template records at the top level, got {type(data).__name__}"
        )
    return data


def _load_slots(where: str, raw_slots: Any) -> tuple[Slot, ...]:
    if raw_slots is None:
        raw_slots = []
    if not isinstance(raw_slots, list):
        raise _fail(where, "slots", f"expected a list, got {type(raw_slots).__name__}")
    slots: list[Slot] = []
    seen: set[str] = set()
    for n, raw in enumerate(raw_slots):
        at = f"{where}: slot #{n}"
        if not isinstance(raw, dict):
            raise TemplateValidationError(f"{at}: expected a mapping, got {type(raw).__name__}")
        extra = set(raw) - _SLOT_KEYS
        if extra:
            raise TemplateValidationError(f"{at}: unknown key(s) {sorted(extra)}")
        name = raw.get("name")
        if not isinstance(name, str) or not _SLOT_NAME_RE.match(name):
            raise _fail(at, "name", f"must match {_SLOT_NAME_RE.pattern}, got {name!r}")
        if name in RESERVED_NAMES:
            # Not merely shadowing: a slot named `context` would be settable via
            # template_vars, which is how a caller would replace the retrieved
            # passages with its own text.
            raise _fail(at, "name", f"{name!r} is reserved and cannot be a slot")
        if name in seen:
            raise _fail(at, "name", f"duplicate slot name {name!r}")
        seen.add(name)
        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise _fail(at, "required", f"must be a boolean, got {required!r}")
        max_len = raw.get("max_len")
        # `isinstance(True, int)` is True in Python, and a stray `max_len: yes`
        # in YAML decodes to a bool — which would then pass an `int` check and
        # cap the slot at 1 character.
        if not isinstance(max_len, int) or isinstance(max_len, bool) or max_len < 1:
            raise _fail(
                at,
                "max_len",
                f"every slot must declare an integer cap >= 1 (ADR-0008 decision 3 rule 2), got {max_len!r}",
            )
        label = raw.get("label")
        if label is not None and not isinstance(label, str):
            raise _fail(at, "label", f"must be a string, got {label!r}")
        slots.append(Slot(name=name, required=required, max_len=max_len, label=label))
    return tuple(slots)


def _load_one(where: str, raw: Any) -> PromptTemplate:
    if not isinstance(raw, dict):
        raise TemplateValidationError(
            f"{where}: expected a mapping, got {type(raw).__name__}"
        )
    extra = set(raw) - _TEMPLATE_KEYS
    if extra:
        raise TemplateValidationError(f"{where}: unknown key(s) {sorted(extra)}")

    tid = raw.get("id")
    if not isinstance(tid, str) or not tid or tid != tid.strip():
        raise _fail(where, "id", f"must be a non-empty string without surrounding whitespace, got {tid!r}")
    if not _ID_PATTERN.match(tid):
        # The published contract bounds `id` because it is caller-supplied and
        # reaches logs. Accepting a wider one here let the BOOT succeed and then
        # made GET /v1/prompt-templates violate its own schema at runtime —
        # inverting ADR-0008 rule 5, which exists so an authoring error fails
        # loudly at load rather than becoming someone else's 500.
        raise _fail(where, "id", f"{tid!r} does not match {_ID_PATTERN.pattern}")
    where = f"{where} template {tid!r}"

    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _fail(where, "version", f"must be an integer >= 1, got {version!r}")

    max_output_tokens = raw.get("max_output_tokens")
    if max_output_tokens is not None and (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or max_output_tokens < 1
    ):
        raise _fail(where, "max_output_tokens", f"must be an integer >= 1, got {max_output_tokens!r}")
    if max_output_tokens is not None and max_output_tokens > _MAX_OUTPUT_TOKENS_CEILING:
        # An authoring slip (an extra zero) otherwise reaches the model server as
        # a verbatim request and comes back a 400, which this app degrades into a
        # generic "[answer generation failed]" — the fault named nowhere near
        # where it was made. Caught at load, like every other authoring error.
        raise _fail(
            where,
            "max_output_tokens",
            f"{max_output_tokens} exceeds the sanity ceiling of {_MAX_OUTPUT_TOKENS_CEILING}",
        )
    label = raw.get("label")
    if not isinstance(label, str) or not label:
        raise _fail(where, "label", f"must be a non-empty string, got {label!r}")

    output = raw.get("output")
    if output not in _OUTPUTS:
        raise _fail(where, "output", f"must be one of {list(_OUTPUTS)}, got {output!r}")

    columns_raw = raw.get("columns")
    if output == "table":
        if not isinstance(columns_raw, list) or not columns_raw:
            raise _fail(where, "columns", "output 'table' requires a non-empty list of column names")
        if not all(isinstance(c, str) and c for c in columns_raw):
            raise _fail(where, "columns", f"every column must be a non-empty string, got {columns_raw!r}")
        for c in columns_raw:
            if "\t" in c:
                # `{{columns}}` renders TAB-JOINED, so an embedded tab turns N
                # declared columns into N+1 fields in the prompt — and a client
                # parsing the answer against the declaration mis-aligns every
                # row. Load-time detectable, so detect it at load.
                raise _fail(where, "columns", f"column {c!r} contains a tab")
        if len(set(columns_raw)) != len(columns_raw):
            raise _fail(
                where, "columns", f"column names must be unique, got {columns_raw!r}"
            )
        columns: tuple[str, ...] | None = tuple(columns_raw)
    else:
        if columns_raw is not None:
            # Not ignored: columns on a `text` template means the author meant
            # `output: table` and the deployment would quietly emit prose.
            raise _fail(where, "columns", "output 'text' must not declare columns")
        columns = None

    system = raw.get("system")
    if not isinstance(system, str) or not system:
        raise _fail(where, "system", f"must be a non-empty string, got {system!r}")
    user = raw.get("user")
    if not isinstance(user, str) or not user:
        raise _fail(where, "user", f"must be a non-empty string, got {user!r}")

    # Rule 4: slots never reach the system message. `system` is emitted verbatim
    # by render(), so a `{{…}}` there would ship to the model as literal braces —
    # an author who wrote one believed it would be filled, and silently shipping
    # their un-filled marker is not a kindness. Any `{{` at all is the error.
    if "{{" in system:
        raise _fail(
            where,
            "system",
            "must contain no substitution markers — the system message is rendered "
            "with no substitution at all (ADR-0008 decision 3 rule 4)",
        )

    slots = _load_slots(where, raw.get("slots"))

    try:
        nodes = _parse(user)
    except ValueError as exc:
        raise _fail(where, "user", str(exc)) from exc

    declared = {s.name for s in slots}
    used = _referenced(nodes)

    undeclared = sorted(used - declared - RESERVED_NAMES)
    if undeclared:
        raise _fail(
            where,
            "user",
            f"references undeclared slot(s) {undeclared}; declared: {sorted(declared)}",
        )

    # An unreferenced slot is an ERROR, not a warning. It is the same failure as
    # rule 3's typo, one level up: the template advertises a knob in
    # GET /v1/prompt-templates, a caller fills it, validation accepts the value,
    # and it changes nothing about the prompt — a wrong result with no error
    # anywhere, and this time the *server* invited it. A warning would be a line
    # in a log nobody reads at the moment a caller is being misled, and the fix
    # (delete the slot, or reference it) is trivial and belongs to the operator
    # who is already looking at the file.
    unreferenced = sorted(declared - used)
    if unreferenced:
        raise _fail(
            where,
            "slots",
            f"slot(s) {unreferenced} are declared but never referenced in 'user' — "
            "delete them or reference them; an advertised slot that does nothing "
            "is a silently wrong prompt for whoever fills it",
        )

    if "columns" in used and columns is None:
        raise _fail(where, "user", "references {{columns}} but output is 'text' (no columns declared)")

    if "context" not in used:
        # Not fatal: a template that summarizes only its slots is odd but not
        # broken, and refusing it would be this module inventing a rule ADR-0008
        # does not state. Worth saying out loud at boot, though — a RAG prompt
        # that never receives the retrieved passages generates ungrounded text.
        log.warning(
            "prompt template %r does not reference {{context}}: retrieved passages "
            "will not reach the model",
            tid,
        )

    return PromptTemplate(
        id=tid,
        version=version,
        label=label,
        output=output,  # one of _OUTPUTS — proved by the check above
        columns=columns,
        slots=slots,
        system=system,
        user=user,
        max_output_tokens=max_output_tokens,
        hash=content_hash(
            version=version,
            label=label,
            max_output_tokens=max_output_tokens,
            output=output,
            columns=columns,
            slots=slots,
            system=system,
            user=user,
        ),
    )


def load_templates(path: str | Path) -> dict[str, PromptTemplate]:
    """Read, validate and index a template file: ``{id: PromptTemplate}``.

    Raises :class:`TemplateValidationError` on the first problem, naming the
    template and the field. Call this at startup and let it propagate — ADR-0008
    decision 3 rule 5 wants a malformed file to fail the boot, not to 500 the
    first caller who selects the broken template.
    """
    p = Path(path)
    if not p.is_file():
        raise TemplateValidationError(f"{p}: prompt template file not found")
    records = _read_records(p)
    out: dict[str, PromptTemplate] = {}
    for n, raw in enumerate(records):
        t = _load_one(f"{p} record #{n}", raw)
        if t.id in out:
            # Last-wins would make the file's meaning depend on its order and
            # hide a copy-paste that was meant to be an edit.
            raise TemplateValidationError(f"{p}: duplicate template id {t.id!r}")
        out[t.id] = t
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(t: PromptTemplate, vars: Mapping[str, str], context: str) -> tuple[str, str]:
    """Render ``t`` into ``(system_message, user_message)``.

    ``context`` is the already-formatted passage block (see
    ``llm.RagGenerator._format_context``) and arrives as an argument, never
    through ``vars``.

    Raises :class:`TemplateRenderError` — which the API answers 422 — when
    ``vars`` carries a name the template does not declare, omits a required
    slot, or exceeds a slot's ``max_len``.

    The substitution is **single-pass by construction**: the body is parsed into
    nodes first, and rendering only appends resolved values to an output buffer.
    Nothing appended is ever re-examined, so neither ``{{`` in one value nor a
    later slot's marker spelled out inside an earlier value can be interpreted.
    There is no escaping step to get wrong, because there is no second pass.
    """
    declared = {s.name: s for s in t.slots}

    # Unknown keys first: a caller who typo'd a name should hear about the typo,
    # not about the required slot they thought they had just filled.
    unknown = sorted(set(vars) - set(declared))
    if unknown:
        # Reserved names land here too, and that is the point: `context` is not a
        # slot, so setting it is exactly as wrong as setting `orgnaism`.
        raise TemplateRenderError(
            f"template {t.id!r} does not declare {unknown}; declared slots: {sorted(declared)}",
            template_id=t.id,
            slot=unknown[0],
        )

    values: dict[str, str] = {}
    for name, slot in declared.items():
        raw = vars.get(name)
        if raw is None or raw == "":
            # An explicit "" is treated as absent. A required slot filled with the
            # empty string renders the same empty clause a missing one would, so
            # accepting it would reopen rule 3's hole through the front door; if a
            # template can meaningfully run without the value, the slot is optional.
            if slot.required:
                raise TemplateRenderError(
                    f"template {t.id!r} requires a non-empty value for slot {name!r}",
                    template_id=t.id,
                    slot=name,
                )
            values[name] = ""
            continue
        if not isinstance(raw, str):
            # These values arrive from JSON, where `{"year": 2025}` is an int.
            # Coercing would put an unbounded repr into the prompt past the cap.
            raise TemplateRenderError(
                f"template {t.id!r} slot {name!r} must be a string, got {type(raw).__name__}",
                template_id=t.id,
                slot=name,
            )
        # Characters, not bytes: `max_len` is an authoring statement about the
        # shape of the value ("an organism name"), and a cap that shrank for
        # non-ASCII text would reject legitimate values in some languages only.
        if len(raw) > slot.max_len:
            raise TemplateRenderError(
                f"template {t.id!r} slot {name!r} is {len(raw)} characters, over its "
                f"declared max_len of {slot.max_len}",
                template_id=t.id,
                slot=name,
            )
        values[name] = raw

    values["context"] = context
    # Tab-joined: `output: table` templates ask for TSV, so the column list is
    # pasted straight into the header instruction.
    values["columns"] = "\t".join(t.columns or ())
    values["label"] = t.label

    parts: list[str] = []
    _emit(_parse(t.user), values, parts)
    # `system` verbatim — rule 4. Load-time validation has already proved it holds
    # no markers, so there is nothing here to strip or escape.
    return t.system, "".join(parts)


def _emit(nodes: Iterable[_Node], values: Mapping[str, str], out: list[str]) -> None:
    """Append the rendered nodes to ``out``. Append-only: this is where the
    single-pass guarantee actually lives."""
    for n in nodes:
        if isinstance(n, _Text):
            out.append(n.text)
        elif isinstance(n, _Var):
            out.append(values[n.name])
        else:
            # A section renders when its slot has a non-empty value — the
            # "{{#genes}} involving genes/proteins: {{genes}}{{/genes}}" idiom,
            # so an omitted optional slot drops its whole clause rather than
            # leaving a dangling "involving genes/proteins: ".
            if values[n.name]:
                _emit(n.body, values, out)


# ---------------------------------------------------------------------------
# Wire projection
# ---------------------------------------------------------------------------


def to_wire(t: PromptTemplate) -> dict[str, Any]:
    """One item of ``templates[]`` in ``contracts/schemas/prompt_templates_response.json``.

    **``system`` and ``user`` are deliberately absent.** Prompt bodies are
    operator configuration; handing them to every authenticated caller publishes
    the tenant's tuning and turns a template into something to copy into the
    level-3 request we refused to build. What a caller needs to *use* a template
    is here — its id, what it accepts, and what it produces — and what it needs
    to *cite* a result is ``(id, version, hash)``.
    """
    item: dict[str, Any] = {
        "id": t.id,
        "version": t.version,
        "hash": t.hash,
        "label": t.label,
        "output": t.output,
    }
    # Omitted rather than null when absent: the schema is additionalProperties
    # false with `columns` optional, and `"columns": null` fails its `type: array`.
    if t.columns is not None:
        item["columns"] = list(t.columns)
    # Same rule. The contract declared this and to_wire did not emit it, so the
    # schema advertised a field no caller could ever see — and the schema-
    # validation test could not catch it, because a missing OPTIONAL field is
    # valid. A caller needs it to know why one template's table runs longer.
    if t.max_output_tokens is not None:
        item["max_output_tokens"] = t.max_output_tokens
    slots: list[dict[str, Any]] = []
    for s in t.slots:
        entry: dict[str, Any] = {"name": s.name, "required": s.required, "max_len": s.max_len}
        if s.label is not None:
            entry["label"] = s.label
        slots.append(entry)
    item["slots"] = slots
    return item
