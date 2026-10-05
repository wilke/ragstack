"""The tool image named by the CWL ``DockerRequirement``s (ADR-0010).

Every shipped ``cwl/*.cwl`` inlines its ``CommandLineTool``s, and each one
names the image that implements it twice — ``dockerPull`` (GoWe reads only
this) and ``dockerImageId`` (cwltool ``--singularity`` reads only this; GoWe
parses it and never checks it). What those values are allowed to be, how they
are rewritten, and how a document is checked for sites a rewrite could not
see, live here, shared by:

* ``python/scripts/stamp_tool_image.py`` — the server-release step that
  writes a built image's name into every ``dockerPull`` and ``dockerImageId``
  (ADR-0010, three-artifact model: the digest stays in the receipt and the
  image's labels, never in the CWL);
* ``tests/unit/test_cwl_tool_image_pin.py`` — the tree-wide pin test (the
  tree is *unstamped* or *stamped*, never mixed);
* the adversarial tests of #642 (``tests/unit/test_gowe_tool_image.py``),
  whose substitution logic moved here from ``ragstack.ingestion.backends``.

``GOWE_TOOL_IMAGE`` is retired (ADR-0010 decision 5, #655): the API refuses to
boot while it is set, and :func:`substitute_tool_image` is no longer called on
the registration path. It stays here because its *checks* — the name shape,
the refuse-on-partial rule — are what the stamping step runs.

Engine facts the shapes below encode (GoWe session, 2026-10-04, on #655):

* ``.sif`` is load-bearing. GoWe keeps a ``dockerPull`` as a local image only
  when it ends in ``.sif``; anything else is prefixed ``docker://`` and sent
  to a registry. Every image name here must carry the suffix.
* A ``+`` in the name is safe: no escaping anywhere, ``resolveApptainerImage``
  is ``filepath.Join(imageDir, name)``. ``ragstack-tools-v1.6.4+a2be96f-b1.sif``
  resolves as written.
* ``dockerImageId`` is parsed and never checked. It carries the same name as
  ``dockerPull``, never a digest — the digest lives in the receipt beside the
  image and in its labels. Whether the image behind that name is the right
  one is enforced only by our own render/boot check against the receipt
  (ADR-0010 decision 7, migration step 4); until that lands it is a recorded
  fact, not a gate.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The committed copy of a built image's receipt (ADR-0010 decision 8, #655
#: step 2), written by ``stamp_tool_image.py`` beside the CWL it stamped:
#: ``cwl/tool-image.receipt.json``. A file's sha256 cannot live inside the
#: file, so the digest the API records on every submission comes from here —
#: offline-checkable, in git next to the ``dockerPull`` it belongs to. Absent on
#: an unstamped tree; ``--check`` refuses a stale one.
RECEIPT_BASENAME = "tool-image.receipt.json"

#: The three per-submission provenance inputs (ADR-0010 decision 8) the API
#: seeds between registration and submission, and the worker's pack step
#: writes into ``manifest.json``/the receipt.
PROVENANCE_INPUTS = ("workflow_id", "tool_image", "tool_image_digest")

#: The unstamped name every shipped CWL carries on ``main``: GoWe joins it onto
#: the worker's ``--image-dir``, where a per-group symlink resolves it to some
#: build. It disappears from the CWL only via stamping at a release.
DEFAULT_TOOL_IMAGE = "ragstack-worker.sif"

#: A stamped image name: ``ragstack-tools-<version>-b<N>.sif`` where
#: ``<version>`` is a derived repo version (``v1.6.4`` or ``v1.6.4+a2be96f``).
STAMPED_IMAGE_RE = re.compile(
    r"^ragstack-tools-(?P<version>v[0-9][^+\s/]*(?:\+[0-9a-f]{4,40})?)-b(?P<build>[1-9][0-9]*)\.sif$"
)

#: A sha256 hex digest, the value recorded in an image's receipt (never in the
#: CWL — ``dockerImageId`` carries the same bare name as ``dockerPull``).
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A bare image FILENAME: GoWe joins it onto `--image-dir`, so a separator (or a
# leading dot, for `..`) would resolve outside that directory.
_TOOL_IMAGE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.sif")


def validate_tool_image(name: str) -> str:
    """Return ``name`` stripped, or raise ``ValueError``.

    Empty is valid (= no image named). Anything else must be a bare filename
    ending in ``.sif``: no ``/`` or ``\\``, no leading dot — a path would escape
    the worker's ``--image-dir``, and a non-``.sif`` name is sent to a
    registry by GoWe rather than resolved as a file.
    """
    value = (name or "").strip()
    if value and (
        not _TOOL_IMAGE_NAME_RE.fullmatch(value)
        # NAME_MAX: a longer name cannot exist as a file in any image dir.
        or len(value.encode("utf-8")) > 255
    ):
        raise ValueError(
            f"tool image {value!r} is not a bare image filename: it must end in "
            "'.sif' and contain no path separator or leading dot (the engine joins it "
            "onto the worker's --image-dir, so a path would escape it, and a name "
            "without .sif is sent to a registry as docker://) and be at most "
            "255 bytes. Example: ragstack-tools-v1.6.4-b1.sif"
        )
    return value


def stamped_image_name(version: str, build: int) -> str:
    """``ragstack-tools-<version>-b<N>.sif`` — the one spelling of a build's name."""
    name = f"ragstack-tools-{version}-b{int(build)}.sif"
    if STAMPED_IMAGE_RE.match(name) is None:
        raise ValueError(f"{version!r}/b{build} does not form a stamped image name")
    return name


class ToolImageError(ValueError):
    """A rewrite could not be applied to every image site of a CWL document."""


# A value of `DockerRequirement`: the key at the START of a (non-comment) line,
# the current name as the whole value — optionally quoted, optionally followed
# by a trailing comment. Anchored on the key so a comment or prose that merely
# MENTIONS the name is left alone.
def _site_re(current: str) -> re.Pattern[str]:
    return re.compile(
        r"^(?P<key>[ \t]*(?P<field>dockerPull|dockerImageId):[ \t]*(?P<q>[\"']?))"
        + re.escape(current)
        + r"(?=(?P=q)[ \t]*(?:#.*)?\r?$)",
        re.MULTILINE,
    )


_TOOL_IMAGE_RE = _site_re(DEFAULT_TOOL_IMAGE)

# The default name as a whole token (not a prefix of `ragstack-worker.sif.bak`).
_DEFAULT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9._-])" + re.escape(DEFAULT_TOOL_IMAGE) + r"(?![A-Za-z0-9._-])"
)


def _residual_image_sites(cwl: str) -> list[int]:
    """1-based line numbers where the default image is still named as an image.

    A site the substitution regex cannot see — a flow mapping
    (``{dockerPull: ragstack-worker.sif}``), a list item (``- dockerPull: …``),
    ``dockerPull :``, a differently-cased key, or the value on the next line —
    would run the unpinned image on that one step. Comment lines are skipped, and
    so is prose that merely mentions the name: a line counts only if it also
    names a docker key or is the bare value alone.
    """
    sites = []
    for n, line in enumerate(cwl.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        code = re.split(r"[ \t]#", line, maxsplit=1)[0]
        if not _DEFAULT_TOKEN_RE.search(code):
            continue
        bare = code.strip().lstrip("-").strip().strip("\"'")
        if "docker" in code.lower() or bare == DEFAULT_TOOL_IMAGE:
            sites.append(n)
    return sites


def substitute_tool_image(cwl: str, tool_image: str, *, source: str = "workflow") -> str:
    """Replace the default tool image in a CWL document's ``DockerRequirement``.

    Empty ``tool_image`` (or the default name itself) returns ``cwl`` unchanged,
    byte for byte. Otherwise every ``dockerPull: ragstack-worker.sif`` (and its
    ``dockerImageId`` twin) is rewritten; nothing else in the text is touched.

    Two failure shapes, handled differently:

    * **Nothing to substitute** (no site names the default at all) logs a
      WARNING: the pin does nothing, but the document is self-consistent — every
      step runs what the CWL names.
    * **A half-substituted document** — some sites rewritten, one the regex
      cannot see left naming the default — RAISES :class:`ToolImageError` naming
      each residual line. A pinned document would otherwise run one step on the
      wrong image with nothing in the logs a reader would connect to it.

    Retired from the registration path by ADR-0010 (#655): nothing calls this
    at boot any more. The stamping step uses :func:`stamp_tool_image` instead,
    which rewrites both keys to the same stamped name — never a digest.
    """
    image = (tool_image or "").strip()
    if not image or image == DEFAULT_TOOL_IMAGE:
        return cwl
    pulls = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal pulls
        if m.group("field") == "dockerPull":
            pulls += 1
        return m.group("key") + image

    out = _TOOL_IMAGE_RE.sub(_sub, cwl)
    residual = _residual_image_sites(out)
    if residual:
        raise ToolImageError(
            f"tool image {image} could not be applied to all of {source}: "
            f"line(s) {', '.join(map(str, residual))} still name {DEFAULT_TOOL_IMAGE} "
            "in a form the substitution does not rewrite (flow mapping, list item, "
            "'dockerPull :', key case, or value on the next line). Those steps would "
            f"run the unpinned image. Write them as 'dockerPull: {DEFAULT_TOOL_IMAGE}' "
            "on one line."
        )
    if pulls == 0:
        log.warning(
            "tool image %s substituted nothing: %s has no 'dockerPull: %s' line, "
            "so its steps run whatever image that CWL names, not the pinned one",
            image, source, DEFAULT_TOOL_IMAGE,
        )
    else:
        log.info("tool image: %s pinned to %s (%d dockerPull site(s))", source, image, pulls)
    return out


# --------------------------------------------------------------------------- #
# Stamping (release time) and the tree-wide pin check
# --------------------------------------------------------------------------- #

#: Every ``dockerPull`` / ``dockerImageId`` line, in the one shape the stamping
#: rewrites: key at the start of the line, a single unquoted-or-quoted scalar
#: value, optional trailing comment.
_ANY_SITE_RE = re.compile(
    r"^(?P<key>[ \t]*(?P<field>dockerPull|dockerImageId):[ \t]*(?P<q>[\"']?))"
    r"(?P<value>[^\s\"'#][^\s\"']*)"
    r"(?=(?P=q)[ \t]*(?:#.*)?\r?$)",
    re.MULTILINE,
)

#: Any line that mentions a docker image key at all (any case, any spacing),
#: for finding sites the rewrite regex could not see.
_DOCKER_KEY_TOKEN_RE = re.compile(r"docker(pull|imageid)", re.IGNORECASE)


def image_sites(cwl: str) -> list[tuple[int, str, str]]:
    """``(1-based line, field, value)`` for every rewritable image site."""
    out = []
    for n, line in enumerate(cwl.splitlines(), start=1):
        m = _ANY_SITE_RE.match(line)
        if m is not None:
            out.append((n, m.group("field"), m.group("value")))
    return out


def unrewritable_sites(cwl: str) -> list[int]:
    """1-based lines that name a docker image key but are not rewritable sites:
    a flow mapping, a list item, ``dockerPull :``, a differently-cased key, a
    value on the next line, or an empty value. Comments are skipped; so is
    prose that mentions the key only after a ``#``."""
    bad = []
    for n, line in enumerate(cwl.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        code = re.split(r"[ \t]#", line, maxsplit=1)[0]
        if not _DOCKER_KEY_TOKEN_RE.search(code):
            continue
        if _ANY_SITE_RE.match(line) is None:
            bad.append(n)
    # The bare default on a line of its own (the value-on-the-next-line shape).
    for n in _residual_image_sites(cwl):
        if n not in bad and _ANY_SITE_RE.match(cwl.splitlines()[n - 1]) is None:
            bad.append(n)
    return sorted(bad)


def stamp_tool_image(cwl: str, name: str, *, source: str = "workflow") -> str:
    """Write ``name`` into every ``dockerPull`` **and** every ``dockerImageId``
    of a CWL document (both keys carry the same bare filename: GoWe reads the
    first, cwltool ``--singularity`` the second — see ``cwl/README.md``).
    Raises :class:`ToolImageError` when any site is unrewritable
    (refuse-on-partial, from #642), when ``name`` is not a well-formed stamped
    ``.sif`` name, or when a current ``dockerPull`` does not end in ``.sif``.

    The image's digest is NOT written into the CWL: it lives in the receipt
    beside the image and in the image's labels, and the identity check
    (ADR-0010 step 4) reads those. The CWL names the image; the receipt proves
    it. A document with no image site at all (a workflow with no container
    step) is returned unchanged.
    """
    if STAMPED_IMAGE_RE.match(name) is None or validate_tool_image(name) != name:
        raise ToolImageError(
            f"{name!r} is not a stamped image name (ragstack-tools-<version>-b<N>.sif); "
            "the .sif suffix is load-bearing — without it GoWe sends the name to a registry"
        )
    bad = unrewritable_sites(cwl)
    if bad:
        raise ToolImageError(
            f"{source}: line(s) {', '.join(map(str, bad))} name a docker image key in a "
            "form the stamping does not rewrite (flow mapping, list item, 'dockerPull :', "
            "key case, or value on the next line). Those steps would run an unstamped "
            "image. Write them as 'dockerPull: <name>' / 'dockerImageId: <name>' on one line."
        )
    # A current value that is not a local image: a hand edit that already sends
    # GoWe to a registry (no .sif) is not something to paper over silently.
    odd = [f"{n} ({field}: {value})" for n, field, value in image_sites(cwl)
           if not value.endswith(".sif")]
    if odd:
        raise ToolImageError(
            f"{source}: line(s) {', '.join(odd)} do not name a .sif image. GoWe sends a "
            "dockerPull without .sif to a registry as docker://<name>; fix the site by hand "
            "before stamping."
        )

    def _sub(m: re.Match[str]) -> str:
        return m.group("key") + name

    return _ANY_SITE_RE.sub(_sub, cwl)


def check_tree_state(docs: dict[str, str]) -> tuple[str, str | None, list[str]]:
    """Classify a set of CWL documents (``{source: text}``) as one tree.

    Returns ``(state, image_name, problems)``: ``state`` is ``"unstamped"``
    (every ``dockerPull`` is :data:`DEFAULT_TOOL_IMAGE`), ``"stamped"`` (every
    ``dockerPull`` names the SAME well-formed stamped image and every
    ``dockerImageId`` equals it — ``image_name`` is that name), ``"empty"`` (no
    image site anywhere) or ``"mixed"``; ``problems`` lists every site that
    disagrees with the state, with its source and line. A tree in any state
    but the first two is a release that cannot be cut.

    Nothing here compares the stamped name to the checkout's own version: a
    server release (tag S) CHOOSES which tools image (built at tag T, or
    ``T+sha`` for a dev server) its CWL names — the three-artifact model of
    ADR-0010 (docs/adr-0010-three-artifacts).
    """
    problems: list[str] = []
    pulls: dict[str, list[str]] = {}
    ids: dict[str, list[str]] = {}
    for source, text in docs.items():
        for n in unrewritable_sites(text):
            problems.append(f"{source}:{n}: docker image key in an unrewritable form")
        for n, field, value in image_sites(text):
            (pulls if field == "dockerPull" else ids).setdefault(value, []).append(f"{source}:{n}")
    if not pulls and not ids:
        return ("empty", None, problems)
    stamped = [v for v in pulls if STAMPED_IMAGE_RE.match(v)]
    default = [v for v in pulls if v == DEFAULT_TOOL_IMAGE]
    other = [v for v in pulls if v not in stamped and v not in default]
    for v in other:
        for where in pulls[v]:
            problems.append(f"{where}: dockerPull {v!r} is neither {DEFAULT_TOOL_IMAGE} "
                            "nor a stamped ragstack-tools-<version>-b<N>.sif name")
    if default and not stamped and not other:
        for v, wheres in ids.items():
            if v != DEFAULT_TOOL_IMAGE:
                for where in wheres:
                    problems.append(f"{where}: dockerImageId {v!r} in an unstamped tree "
                                    f"must be {DEFAULT_TOOL_IMAGE}")
        return ("unstamped", None, problems) if not problems else ("mixed", None, problems)
    if len(stamped) == 1 and not default and not other:
        name = stamped[0]
        for v, wheres in ids.items():
            if v != name:
                for where in wheres:
                    problems.append(f"{where}: dockerImageId {v!r} in a stamped tree must equal "
                                    f"the dockerPull name {name}")
        return ("stamped", name, problems) if not problems else ("mixed", name, problems)
    for v in stamped:
        for where in pulls[v]:
            problems.append(f"{where}: dockerPull {v!r} (stamped)")
    for v in default:
        for where in pulls[v]:
            problems.append(f"{where}: dockerPull {v!r} (unstamped)")
    return ("mixed", None, problems)


# --------------------------------------------------------------------------- #
# Provenance (ADR-0010 decision 8, #655 step 2): what the API seeds per submission
# --------------------------------------------------------------------------- #


def tool_image_of(cwl: str) -> str | None:
    """The ONE image name a CWL document's ``dockerPull`` sites carry, or
    ``None`` when the document names no image or names more than one (a
    mixed document is a release that cannot be cut — see
    :func:`check_tree_state`; the provenance record must not pick one)."""
    names = {value for _n, field, value in image_sites(cwl) if field == "dockerPull"}
    if len(names) != 1:
        if names:
            log.warning("tool image: document names %d distinct images %s; recording none",
                        len(names), sorted(names))
        return None
    return names.pop()


def read_committed_receipt(cwl_dir: str | Path) -> dict[str, Any] | None:
    """``<cwl_dir>/tool-image.receipt.json`` as a dict, or ``None`` when absent
    or unreadable (logged). Never raises: a missing receipt is the normal
    state of an unstamped tree and means ``tool_image_digest: null``."""
    path = Path(cwl_dir) / RECEIPT_BASENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("tool image: committed receipt %s unreadable: %s", path, e)
        return None
    if not isinstance(data, dict):
        log.warning("tool image: committed receipt %s is not a JSON object", path)
        return None
    return data


def tool_image_digest_for(name: str | None, receipt: dict[str, Any] | None) -> str | None:
    """The receipt's sha256 — but only when the receipt names ``name``. A
    receipt left behind from an earlier stamping must not lend its digest to
    an image the CWL no longer names, so a mismatch is ``None`` (and a
    warning), as is a malformed digest."""
    if not name or not receipt:
        return None
    if str(receipt.get("name") or "") != name:
        log.warning("tool image: committed receipt names %r but the CWL names %r; "
                    "recording no digest", receipt.get("name"), name)
        return None
    sha = str(receipt.get("sha256") or "")
    if SHA256_RE.match(sha) is None:
        log.warning("tool image: committed receipt for %s has no 64-hex sha256; "
                    "recording no digest", name)
        return None
    return sha


def declared_workflow_inputs(cwl: str) -> set[str]:
    """The names a CWL ``Workflow`` declares under its top-level ``inputs``
    (mapping or list form). Empty when the text does not parse as YAML or is
    not a workflow — a provenance input must never be sent to a workflow that
    does not declare it (a bulk-plane CWL, or a hand-written one)."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a runtime dependency
        log.warning("tool image: PyYAML missing; cannot read declared workflow inputs")
        return set()
    try:
        doc = yaml.safe_load(cwl)
    except yaml.YAMLError as e:
        log.warning("tool image: workflow text is not YAML (%s); seeding no provenance inputs", e)
        return set()
    if not isinstance(doc, dict):
        return set()
    inputs = doc.get("inputs")
    if isinstance(inputs, dict):
        return {str(k) for k in inputs}
    if isinstance(inputs, list):
        return {str(i.get("id")) for i in inputs if isinstance(i, dict) and i.get("id")}
    return set()


def provenance_inputs(
    cwl: str, cwl_path: str | os.PathLike[str] | None, workflow_id: str,
) -> dict[str, str | None]:
    """The provenance inputs to seed on a submission of ``cwl`` registered as
    ``workflow_id``: ``{workflow_id, tool_image, tool_image_digest}``,
    restricted to the names the workflow declares. ``tool_image`` is the one
    ``dockerPull`` of the registered text (the bare default on an unstamped
    tree); the digest comes from the committed receipt beside ``cwl_path``
    and is ``None`` when there is none or it names a different image.

    These are *inputs* because the worker cannot learn them any other way: it
    can read its own image's ``RELEASE`` file, but not the file's digest, and
    the ``wf_`` id exists only once the API has registered the text."""
    declared = declared_workflow_inputs(cwl)
    if not declared.intersection(PROVENANCE_INPUTS):
        return {}
    name = tool_image_of(cwl)
    receipt = read_committed_receipt(Path(cwl_path).parent) if cwl_path else None
    values: dict[str, str | None] = {
        "workflow_id": workflow_id or None,
        "tool_image": name,
        "tool_image_digest": tool_image_digest_for(name, receipt),
    }
    return {k: v for k, v in values.items() if k in declared}
