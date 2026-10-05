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
#: seeds between registration and submission — on a STAMPED tree only — and
#: the worker's pack step writes into ``manifest.json``/the receipt.
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
    restricted to the names the workflow declares — and **only when the text
    names a stamped image** (``ragstack-tools-<version>-b<N>.sif``). On an
    unstamped tree this returns ``{}`` and nothing is seeded: the
    ``["null", string]`` inputs stay null and the tools' flags are omitted
    (GoWe skips null inputs before any prefix; so does cwltool per CWL v1.2).

    Why the gate: the stamp IS the declaration that the named image supports
    the workflow (ADR-0010 decision 4). An unstamped tree names
    ``ragstack-worker.sif``, whichever build a worker's ``--image-dir``
    resolves it to — and the deployed builds predate these flags
    (``--workflow-id`` is ``unrecognized arguments``, argparse exit 2, on
    every ingest / extract / replay task). Provenance begins with the first
    stamped release, whose image is by construction built from a tree that
    carries the scripts that take the flags. ``tool_image`` is then the
    stamped name; the digest comes from the committed receipt beside
    ``cwl_path`` and is ``None`` when there is none or it names a different
    image.

    These are *inputs* because the worker cannot learn them any other way: it
    can read its own image's ``RELEASE`` file, but not the file's digest, and
    the ``wf_`` id exists only once the API has registered the text."""
    declared = declared_workflow_inputs(cwl)
    if not declared.intersection(PROVENANCE_INPUTS):
        return {}
    name = tool_image_of(cwl)
    if name is None or STAMPED_IMAGE_RE.match(name) is None:
        log.debug("tool image: %s is not a stamped image name; seeding no provenance "
                  "inputs (provenance begins with the first stamped release)", name)
        return {}
    receipt = read_committed_receipt(Path(cwl_path).parent) if cwl_path else None
    values: dict[str, str | None] = {
        "workflow_id": workflow_id or None,
        "tool_image": name,
        "tool_image_digest": tool_image_digest_for(name, receipt),
    }
    return {k: v for k, v in values.items() if k in declared}


# --------------------------------------------------------------------------- #
# The identity check (ADR-0010 decision 7, #655 step 4): render + boot
# --------------------------------------------------------------------------- #
#
# GoWe resolves a relative ``dockerPull`` as ``filepath.Join(imageDir, name)``
# at task execution and never looks at ``dockerImageId``; nothing in the engine
# compares the file to anything. So "the image behind this name is the build
# the release stamped" is enforced ONLY here: the named file exists in a store
# the workers resolve, the receipt beside it names it, the file's sha256 equals
# the receipt's, and the image's own labels (``apptainer inspect --labels``)
# equal the receipt. Identity, not compatibility — the release declared the
# pairing when it stamped the name (decision 4).

#: The receipt beside a built image: ``<name>.receipt.json`` (what
#: ``apptainer/build-tools-image.sh`` writes, step 5).
IMAGE_RECEIPT_SUFFIX = ".receipt.json"

#: The label keys the build stamps and the receipt mirrors, with the receipt
#: field each one must equal.
LABEL_FIELDS = (
    ("org.ragstack.version", "version"),
    ("org.ragstack.commit", "commit"),
    ("org.ragstack.build", "build"),
)

#: ``apptainer inspect`` on a 250 MB SIF is a header read, but a wedged
#: squashfs mount has hung it before; bound it.
APPTAINER_INSPECT_TIMEOUT_S = 60.0

# sha256 cache: (path, size, mtime_ns) -> hex. Boot verifies the same image for
# three registrars; hashing 250 MB once is fine, three times is not.
_SHA256_CACHE: dict[tuple[str, int, int], str] = {}


def file_sha256(path: str | os.PathLike[str]) -> str:
    """Streamed sha256 of ``path``, cached in-process by ``(path, size, mtime)``."""
    import hashlib

    p = Path(path)
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    hit = _SHA256_CACHE.get(key)
    if hit is not None:
        return hit
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    digest = h.hexdigest()
    _SHA256_CACHE[key] = digest
    return digest


def parse_image_dirs(value: str | None) -> list[Path]:
    """``GOWE_IMAGE_DIRS`` — comma-separated, blanks dropped, order kept
    (first hit wins in :func:`verify_named_image`, as it does for a worker
    group that resolves one ``--image-dir``)."""
    return [Path(d.strip()).expanduser() for d in (value or "").split(",") if d.strip()]


class ImageVerdict:
    """What the check found for one ``dockerPull`` name.

    ``state`` is one of ``"ok"`` (every check passed), ``"problem"`` (at least
    one entry in ``problems``), ``"unstamped"`` (the bare default name —
    nothing to verify, not a failure) or ``"unchecked"`` (no store dirs were
    given: the API cannot see any store, and the ADR refuses only where it
    can). ``warnings`` are findings that do not fail the check — the labels
    could not be read because ``apptainer`` is not on this host, for one.
    """

    def __init__(self, name: str, dirs: list[Path]) -> None:
        self.name = name
        self.dirs = [str(d) for d in dirs]
        self.path: str | None = None
        self.exists = False
        self.found_in: list[str] = []
        self.receipt_found = False
        self.receipt: dict[str, Any] | None = None
        self.sha256: str | None = None
        self.sha256_ok: bool | None = None
        self.labels: dict[str, str] | None = None
        self.labels_checked = False
        self.labels_ok: bool | None = None
        self.committed_receipt_ok: bool | None = None
        self.state = "problem"
        self.problems: list[str] = []
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "dirs": self.dirs,
            "path": self.path,
            "exists": self.exists,
            "found_in": self.found_in,
            "receipt_found": self.receipt_found,
            "receipt": self.receipt,
            "sha256": self.sha256,
            "sha256_ok": self.sha256_ok,
            "labels": self.labels,
            "labels_checked": self.labels_checked,
            "labels_ok": self.labels_ok,
            "committed_receipt_ok": self.committed_receipt_ok,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
        }

    def summary(self) -> str:
        """One paragraph for a boot refusal or a render line."""
        where = self.path or ("not found in " + ", ".join(self.dirs) if self.dirs else "no store dirs")
        lines = [f"{self.name}: {self.state} ({where})"]
        lines += [f"  problem: {p}" for p in self.problems]
        lines += [f"  warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def _read_image_labels(path: Path, apptainer: str = "apptainer") -> dict[str, str] | None:
    """``apptainer inspect --json --labels <path>`` → the labels dict.

    Returns ``None`` (the caller records a WARNING, not a problem) only when
    no ``apptainer`` is on ``PATH``: a host that cannot inspect the image
    cannot read its labels, and refusing there would refuse every API host
    without the runtime — ops hosts verify from a worker host instead. Any
    other failure raises ``RuntimeError``: an inspect that fails on a file
    apptainer CAN see means the file is not a SIF.
    """
    import shutil
    import subprocess

    exe = shutil.which(apptainer)
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "inspect", "--json", "--labels", str(path)],
            capture_output=True, text=True, timeout=APPTAINER_INSPECT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"{apptainer} inspect {path}: {e}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(
            f"{apptainer} inspect {path} exited {proc.returncode}: {err[-1] if err else 'no output'}"
        )
    try:
        labels = json.loads(proc.stdout)["data"]["attributes"]["labels"]
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"{apptainer} inspect {path}: unexpected output ({e})") from e
    if not isinstance(labels, dict):
        raise RuntimeError(f"{apptainer} inspect {path}: labels are not an object")
    return {str(k): str(v) for k, v in labels.items()}


def verify_named_image(
    name: str,
    store_dirs: list[Path] | list[str],
    *,
    committed_receipt: dict[str, Any] | None = None,
    apptainer: str = "apptainer",
) -> ImageVerdict:
    """The identity check for one ``dockerPull`` name against the image store(s).

    * ``name`` is :data:`DEFAULT_TOOL_IMAGE` → ``state="unstamped"``: an
      unstamped tree names whatever the worker group's ``--image-dir`` symlink
      points at, and there is no receipt to hold it to. Not a failure.
    * ``store_dirs`` empty → ``state="unchecked"`` with a warning: the API
      cannot see any store. The ADR refuses only where it can see one.
    * Otherwise the file is looked up in ``store_dirs`` in order (first hit
      wins; every dir that has it is listed in ``found_in``), the receipt
      beside it is read, the file's sha256 is compared to the receipt's, the
      image's labels are compared to the receipt (``apptainer`` missing →
      ``labels_checked=False`` and a warning), and — when ``committed_receipt``
      (``cwl/tool-image.receipt.json``, the one stamping wrote) is given — it
      must agree with the receipt beside the image. Every disagreement is a
      ``problem``; ``ok`` is ``not problems``.
    """
    dirs = [Path(d) for d in store_dirs]
    v = ImageVerdict(name, dirs)
    if name == DEFAULT_TOOL_IMAGE:
        v.state = "unstamped"
        return v
    if STAMPED_IMAGE_RE.match(name) is None:
        v.problems.append(
            f"{name!r} is neither {DEFAULT_TOOL_IMAGE} nor a stamped "
            "ragstack-tools-<version>-b<N>.sif name"
        )
        return v
    if not dirs:
        v.state = "unchecked"
        v.warnings.append(
            "no image store dirs given (GOWE_IMAGE_DIRS is unset): this host cannot see the "
            f"store, so {name} was not verified — run `ragstack-ctl gowe render <tenant>` "
            "from a host that can"
        )
        return v

    for d in dirs:
        candidate = d / name
        if candidate.is_file():
            v.found_in.append(str(d))
            if v.path is None:
                v.path = str(candidate)
    if v.path is None:
        v.problems.append(f"{name} not found in: " + ", ".join(v.dirs))
        return v
    v.exists = True
    if len(v.found_in) > 1:
        v.warnings.append(
            f"{name} is in {len(v.found_in)} dirs ({', '.join(v.found_in)}); "
            f"verified the first, {v.found_in[0]}, which is what a worker on that dir resolves"
        )
    path = Path(v.path)

    # The receipt beside the image.
    receipt_path = Path(str(path) + IMAGE_RECEIPT_SUFFIX)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        v.problems.append(f"no receipt beside the image: {receipt_path} is missing")
        receipt = None
    except (OSError, ValueError) as e:
        v.problems.append(f"receipt {receipt_path} unreadable: {e}")
        receipt = None
    if receipt is not None:
        v.receipt_found = True
        v.receipt = receipt
        if str(receipt.get("name") or "") != name:
            v.problems.append(
                f"receipt {receipt_path} names {receipt.get('name')!r}, not {name}"
            )

    # The digest.
    try:
        v.sha256 = file_sha256(path)
    except OSError as e:
        v.problems.append(f"cannot hash {path}: {e}")
    if v.sha256 is not None and receipt is not None:
        want = str(receipt.get("sha256") or "")
        if SHA256_RE.match(want) is None:
            v.sha256_ok = False
            v.problems.append(f"receipt {receipt_path} has no 64-hex sha256 (got {want!r})")
        elif want != v.sha256:
            v.sha256_ok = False
            v.problems.append(
                f"sha256 mismatch: file {path} is {v.sha256}, receipt says {want} — the bytes "
                "under this name are not the build the receipt describes"
            )
        else:
            v.sha256_ok = True

    # The labels.
    try:
        labels = _read_image_labels(path, apptainer)
    except RuntimeError as e:
        labels = None
        v.labels_checked = True
        v.labels_ok = False
        v.problems.append(f"labels unreadable: {e}")
    if labels is None and not v.labels_checked:
        v.warnings.append(
            f"labels not verified: no {apptainer!r} on PATH on this host; the sha256 "
            "comparison still holds the file to its receipt"
        )
    elif labels is not None:
        v.labels = labels
        v.labels_checked = True
        v.labels_ok = True
        if receipt is not None:
            for label, field in LABEL_FIELDS:
                got, want = labels.get(label), str(receipt.get(field) if receipt.get(field) is not None else "")
                if got != want:
                    v.labels_ok = False
                    v.problems.append(
                        f"label {label}: image says {got!r}, receipt says {want!r}"
                    )

    # The committed receipt (what the stamping saw) vs the one beside the image.
    if committed_receipt is not None and receipt is not None:
        v.committed_receipt_ok = True
        for field in ("name", "sha256", "version", "commit", "build"):
            a, b = committed_receipt.get(field), receipt.get(field)
            if a is None and b is None:
                continue
            if str(a) != str(b):
                v.committed_receipt_ok = False
                v.problems.append(
                    f"committed receipt ({RECEIPT_BASENAME}) {field}={a!r} but the receipt "
                    f"beside the image says {b!r}: the store holds a different build than "
                    "the release stamped"
                )

    v.state = "ok" if not v.problems else "problem"
    return v


def verify_cwl_file(
    cwl_path: str | os.PathLike[str],
    store_dirs: list[Path] | list[str],
    *,
    apptainer: str = "apptainer",
) -> dict[str, Any]:
    """The render record for one registered CWL: its text sha256 (what GoWe
    content-hashes to mint the ``wf_`` id — over the exact bytes the API
    POSTs, i.e. the file), its ``dockerPull`` name and the verdict for it
    against ``store_dirs`` and the committed receipt beside the file."""
    import hashlib

    p = Path(cwl_path)
    text = p.read_text(encoding="utf-8")
    name = tool_image_of(text)
    record: dict[str, Any] = {
        "cwl": str(p),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "tool_image": name,
        "verdict": None,
    }
    if name is None:
        pulls = {value for _n, field, value in image_sites(text) if field == "dockerPull"}
        v = ImageVerdict("", [Path(d) for d in store_dirs])
        if pulls:
            v.problems.append(f"{p} names {len(pulls)} distinct images: {sorted(pulls)}")
        else:
            v.state = "unstamped"
            v.warnings.append(f"{p} names no image (no dockerPull site)")
        record["verdict"] = v.to_dict()
        return record
    committed = read_committed_receipt(p.parent)
    v = verify_named_image(name, store_dirs, committed_receipt=committed, apptainer=apptainer)
    record["verdict"] = v.to_dict()
    return record


def main(argv: list[str] | None = None) -> int:
    """``python -m ragstack.tool_image verify (--name N | --cwl PATH…) --dirs A,B [--json]``

    The ONE implementation of the identity check, for ``ragstack-ctl gowe
    render`` to shell to: exit 0 when nothing is wrong (ok, unstamped or
    unchecked), 1 on any problem, 2 on usage.
    """
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m ragstack.tool_image")
    sub = ap.add_subparsers(dest="cmd", required=True)
    vp = sub.add_parser("verify", help="verify a dockerPull name, or every image a CWL names")
    vp.add_argument("--name", help="a dockerPull name (ragstack-tools-<version>-b<N>.sif)")
    vp.add_argument("--cwl", action="append", default=[],
                    help="a CWL file; its dockerPull is verified against the committed "
                         "receipt beside it (repeatable)")
    vp.add_argument("--dirs", default="",
                    help="comma-separated image store dirs (GOWE_IMAGE_DIRS); first hit wins")
    vp.add_argument("--committed-receipt", type=Path,
                    help=f"with --name: a {RECEIPT_BASENAME} to hold the store's receipt to")
    vp.add_argument("--apptainer", default="apptainer", help="the apptainer executable")
    vp.add_argument("--json", action="store_true", help="JSON records instead of text")
    args = ap.parse_args(argv)
    if bool(args.name) == bool(args.cwl):
        ap.error("give exactly one of --name or --cwl")
    dirs = parse_image_dirs(args.dirs)
    records: list[dict[str, Any]] = []
    if args.name:
        committed = None
        if args.committed_receipt:
            committed = json.loads(args.committed_receipt.read_text(encoding="utf-8"))
        v = verify_named_image(args.name, dirs, committed_receipt=committed, apptainer=args.apptainer)
        records.append({"cwl": None, "text_sha256": None, "tool_image": args.name,
                        "verdict": v.to_dict()})
    else:
        for cwl in args.cwl:
            try:
                records.append(verify_cwl_file(cwl, dirs, apptainer=args.apptainer))
            except OSError as e:
                v = ImageVerdict("", dirs)
                v.problems.append(f"{cwl}: unreadable: {e}")
                records.append({"cwl": cwl, "text_sha256": None, "tool_image": None,
                                "verdict": v.to_dict()})
    failed = any(r["verdict"]["problems"] for r in records)
    if args.json:
        print(json.dumps({"ok": not failed, "records": records}, indent=2))
    else:
        for r in records:
            vd = r["verdict"]
            if r["cwl"]:
                print(f"{r['cwl']}")
                print(f"  text sha256 (GoWe would content-hash this): {r['text_sha256']}")
            print(f"  dockerPull: {r['tool_image'] or '(none)'} -> {vd['state']}"
                  + (f" at {vd['path']}" if vd["path"] else ""))
            for p in vd["problems"]:
                print(f"    problem: {p}")
            for w in vd["warnings"]:
                print(f"    warning: {w}")
        print("FAIL" if failed else "ok", file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess in tests
    import sys

    sys.exit(main())
