"""Guard: no credential-shaped *default* may be committed to this repo.

This repo is public, and it has already shipped a live bearer key for the vLLM
embedding endpoints as a fallback default — ``EMBED_API_KEY`` in the worker
launcher and ``REF_KEY`` in two report harnesses. The value itself is handled by
rotation; what this test prevents is the *shape* coming back, because the next
one lands in a public history the moment someone writes it down.

The shape is: **a secret-named variable with a committed literal fallback.**

    * shell   — ``${VAR:-<literal>}`` / ``${VAR-<literal>}``
    * python  — ``os.environ.get("VAR", "<literal>")`` / ``os.getenv(...)``

where ``VAR`` contains ``KEY``, ``TOKEN`` or ``SECRET``. Required-with-no-default
is the sanctioned alternative (``${VAR:?message}``, or an explicit ``KeyError`` /
``SystemExit``) — the same treatment ``QDRANT_URL`` gets in STATUS.md.

**Exemptions, and why each is principled rather than convenient:**

* A name ending ``_FILE`` / ``_PATH`` / ``_DIR`` names a *location*, not a value.
  ``WORKER_SECRET_FILE`` in ``deploy/start-ragstack-workers.sh`` is the live
  example: the path is public, the file's contents are not.
* An *empty* default (``:-`` with nothing after it) commits nothing.
* A default that is visibly a placeholder (``CHANGE-ME…``, ``<…>``, ``…``,
  ``example``/``dummy``/``fake``/``not-real``) commits nothing usable.

Out of scope on purpose: ``PASSWORD``-named variables. The task that added this
guard scoped it to KEY/TOKEN/SECRET, and widening it here would pull unrelated
service-bootstrap defaults into an unrelated PR.

**This test must not be vacuous.** Two structural defences, because a sweep that
finds nothing is indistinguishable from a sweep that looks at nothing:

1. Every branch of :func:`_findings` has a *positive control* — a synthetic
   string with a planted violation that the detector must flag
   (``test_detector_fires_on_planted_*``). Delete or weaken any branch and a
   control goes red. The controls are assembled from concatenated fragments so
   that this file's own source never contains a matchable pattern; that is also
   why the sweep needs no self-exclusion.
2. The sweep asserts on its own input: ``git ls-files`` failing is a failure,
   not a skip, and the file list must actually contain the launcher this guard
   exists for.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

#: Repo root — ``python/tests/unit/x.py`` → ``<repo>``.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The file whose committed default is the reason this guard exists. Its presence
#: in the scanned set is the sweep's proof that it scanned anything at all.
ANCHOR = "deploy/start-ragstack-workers.sh"

#: The key that was committed here before this guard existed. Split so that
#: adding the guard does not itself re-commit a greppable copy of the value.
_RETIRED_KEY = "BRC" + "Mistral"

#: Any env-var-shaped name. Case-sensitive: env vars are conventionally
#: upper-case, and matching case-insensitively would flag every ``monkey`` in
#: the tree. Which of these names is a *credential* is decided by
#: :func:`_is_secret_name`, not by the regex.
_NAME = r"[A-Z][A-Z0-9_]*"

#: ``${NAME:-value}`` and ``${NAME-value}``, with the value optionally quoted —
#: ``${API_KEY:-"<literal>"}`` is the most natural way to write one and an earlier
#: draft of this guard missed it entirely. The value class still excludes ``$``
#: and backticks, so indirection (``${API_KEY:-$OTHER}``) is not a literal.
_SHELL_DEFAULT = re.compile(r"\$\{(" + _NAME + r"):?-[\"']?([^}$\"'`]*)[\"']?\}")

#: ``os.environ.get("NAME", "value")`` / ``os.getenv("NAME", "value")``.
_PY_DEFAULT = re.compile(
    r"os\.(?:environ\.get|getenv)\(\s*[\"'](" + _NAME + r")[\"']\s*,\s*[\"']([^\"']*)[\"']"
)

#: Underscore-delimited components that make a name a credential. Matched as
#: whole components, not substrings: ``TOKENIZER_DIR`` and ``KEYWORDS`` are not
#: credentials, and substring matching flagged both kinds when this was written.
_SECRET_WORDS = frozenset({"KEY", "KEYS", "APIKEY", "TOKEN", "TOKENS", "SECRET", "SECRETS"})

#: Names that denote where a secret lives rather than what it is.
_LOCATION_SUFFIXES = ("_FILE", "_PATH", "_DIR")

#: Defaults that are obviously not a working credential.
_PLACEHOLDER = re.compile(
    r"^(?:$|<|\.\.\.)|change[-_ ]?me|placeholder|replace[-_ ]?me|example|dummy|fake|not[-_]real",
    re.IGNORECASE,
)

#: A purely *decimal* default is a quantity, not a credential — ``MAX_TOKENS``
#: counts tokens in the LLM sense, which is the collision this rules out. Kept
#: deliberately narrow: widening it to hex would exempt a very common key shape,
#: which is what ``test_detector_fires_on_planted_hex_default`` pins.
_NUMERIC = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_WORDS & set(name.split("_")))


def _is_exempt(name: str, default: str) -> bool:
    """True when ``name``'s literal ``default`` commits nothing sensitive."""
    if not _is_secret_name(name):
        return True
    if name.endswith(_LOCATION_SUFFIXES):
        return True
    if not default.strip():
        return True
    if _NUMERIC.match(default.strip()):
        return True
    return bool(_PLACEHOLDER.search(default))


def _findings(text: str, where: str = "<synthetic>") -> list[str]:
    """Every committed-credential finding in ``text``, as operator-readable lines.

    Three branches, one per shape; each has a positive control below.
    """
    out: list[str] = []
    for name, default in _SHELL_DEFAULT.findall(text):
        if not _is_exempt(name, default):
            out.append(f"{where}: shell default for {name} — a committed credential")
    for name, default in _PY_DEFAULT.findall(text):
        if not _is_exempt(name, default):
            out.append(f"{where}: os.environ default for {name} — a committed credential")
    if _RETIRED_KEY in text:
        out.append(f"{where}: the retired embedding-endpoint key is back in the tree")
    return out


# --------------------------------------------------------------- the fixtures
# Assembled at run time from fragments so this module's own source contains no
# matchable pattern. Anything written out whole here would make the sweep below
# flag this file, and the usual fix for that — excluding the guard from its own
# sweep — is a hole a future violation could be parked in.
_O, _C = "$" + "{", "}"


def _shell_plant(var: str, sep: str, quote: str = "") -> str:
    return "REF=" + _O + var + sep + quote + "sk-planted-live-value" + quote + _C


def _py_plant(var: str, call: str) -> str:
    return "os." + call + '("' + var + '", "' + "planted-live-value" + '")'


@pytest.mark.parametrize("var", ["EMBED_API_KEY", "GH_TOKEN", "CLIENT_SECRET"])
@pytest.mark.parametrize("sep", [":-", "-"])
@pytest.mark.parametrize("quote", ["", '"', "'"])
def test_detector_fires_on_planted_shell_default(var: str, sep: str, quote: str) -> None:
    """Positive control, shell branch — every name class, both default forms, and
    the quoted spellings. The quoted form is the one a real violation is most
    likely to use, and the first draft of this regex did not match it."""
    text = "set -euo pipefail\n" + _shell_plant(var, sep, quote) + "\necho done\n"
    assert _findings(text), f"detector missed a planted shell default for {var} ({sep}{quote})"


def test_detector_fires_on_planted_hex_default() -> None:
    """Positive control for the *narrowness* of the numeric exemption.

    Hex is a very common key shape. Widening ``_NUMERIC`` to accept ``[0-9a-f]+``
    — a one-character edit that no other test here notices — would exempt it.
    """
    text = "REF=" + _O + "API_KEY:-" + "deadbeef0123456789abcdef" + _C
    assert _findings(text), "detector missed a planted hex-shaped key default"


@pytest.mark.parametrize("var", ["REF_KEY", "API_TOKEN", "APP_SECRET"])
@pytest.mark.parametrize("call", ["environ.get", "getenv"])
def test_detector_fires_on_planted_python_default(var: str, call: str) -> None:
    """Positive control, python branch — every name class and both call forms."""
    text = "import os\n" + "VALUE = " + _py_plant(var, call) + "\n"
    assert _findings(text), f"detector missed a planted os.{call} default for {var}"


def test_detector_fires_on_planted_retired_key() -> None:
    """Positive control, literal branch: the specific value must not come back."""
    text = "embedding_api_key: " + _RETIRED_KEY + "\n"
    assert _findings(text), "detector missed the retired key re-appearing verbatim"


@pytest.mark.parametrize(
    "text",
    [
        "WORKER_SECRET_FILE=" + _O + "WORKER_SECRET_FILE:-/scout/wf/secrets.env" + _C,
        "EMBED_API_KEY=" + _O + "EMBED_API_KEY:-" + _C,
        "KEY_PATH=" + _O + "KEY_PATH:-/etc/keys" + _C,
        "REF=" + _O + "EMBED_API_KEY:-CHANGE-ME-EMBED-API-KEY" + _C,
        "REF=" + _O + "BASE_PORT:-9001" + _C,
        "REF=" + _O + "MAX_TOKENS:-4096" + _C,
        "REF=" + _O + "TOKENIZER_DIR:-/opt/tok" + _C,
        "REF=" + _O + "MONKEY:-banana" + _C,
        "os.environ" + '.get("REF_KEY", "")',
        "os.environ" + '.get("MAX_TOKENS", "4096")',
        "os.environ" + '.get("REF_URLS", "http://localhost:9001")',
    ],
)
def test_detector_ignores_the_exempt_shapes(text: str) -> None:
    """Negative controls: the exemptions in the module docstring, pinned."""
    assert _findings(text) == [], f"detector false-positived on {text!r}"


# ------------------------------------------------------------------ the sweep
def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"git ls-files failed in {REPO_ROOT} ({proc.returncode}): {proc.stderr.strip()}. "
            "This guard scans the tracked tree; not being able to list it is a failure, "
            "not something to skip past."
        )
    return [p for p in proc.stdout.split("\0") if p]


def test_no_committed_credential_defaults_in_tracked_files() -> None:
    paths = _tracked_files()

    # The sweep's own non-vacuity check: it must have looked at a real tree, and
    # specifically at the launcher whose committed default motivated this guard.
    assert len(paths) > 100, f"implausibly small tracked-file set ({len(paths)})"
    assert ANCHOR in paths, f"{ANCHOR} missing from the scanned set — the sweep is not scanning"

    findings: list[str] = []
    scanned = 0
    for rel in paths:
        path = REPO_ROOT / rel
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: nothing to read a literal out of
        scanned += 1
        findings.extend(_findings(text, rel))

    assert scanned > 100, f"implausibly few readable files scanned ({scanned})"
    assert not findings, (
        "committed credential default(s) found — read the env var instead, with no "
        "fallback (`${VAR:?message}` in shell, an explicit failure in python):\n  "
        + "\n  ".join(findings)
    )
