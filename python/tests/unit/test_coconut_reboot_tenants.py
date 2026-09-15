"""The coconut reboot runbook's tenant lists (``ops/coconut/``).

``restore.sh`` and ``pre-reboot.sh`` carry LITERAL lists of tenants: which APIs
to start and stop, which apptainer instances are stores. The control plane's
registry (``ragstack-ctl tenant list``) is the source of truth for what exists,
but until PR-E teaches these scripts to read it, a tenant that is not spelled
out in every list simply does not come back after a reboot — which is how
``hackathon`` (added to the scripts 2026-09-15) was missing from all of them
while attendees were using it.

These assertions are deliberately string/regex based: the subject is the text of
two bash scripts, and the failure they must catch is a list that was edited in
one place and not the other. They do not run anything.

The UI assertion is the other half: ``hackathon``'s UI is a static build nginx
serves from ``ui/dist``, so it must stay OUT of the Vite dev-server loops —
putting it in would make ``restore.sh`` wait out its full timeout on a port
nobody will ever own, and ``pre-reboot.sh`` hunt for a vite process that does
not exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[3] / "ops" / "coconut"

# Every tenant the runbook must know, with the API port it is reached on.
# Extend this when a tenant is added — the point is that the scripts fail here
# before they fail on the host at 3 a.m.
TENANTS = {
    "lucid-next": "24000",
    "asm-next": "24020",
    "dev": "24040",
    "demo": "24060",
    "hackathon": "24080",
}
# Tenants whose UI is a Vite dev server this runbook starts/stops. A tenant
# absent here (hackathon) is served statically by nginx.
VITE_UIS = {"demo", "lucid-next", "asm-next", "dev"}
HACKATHON_STORES = ("qdrant-hackathon", "elasticsearch-hackathon", "postgres-hackathon")


@pytest.fixture(scope="module")
def restore() -> str:
    return (OPS / "restore.sh").read_text()


@pytest.fixture(scope="module")
def pre_reboot() -> str:
    return (OPS / "pre-reboot.sh").read_text()


def _code(text: str) -> str:
    """The script minus its full-line comments — what actually executes."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# An api loop's specs are `"<tenant> <data-dir> <5-digit port>"`. The three-token
# shape is what separates them from the stores loop (`"<instance> <port>"`) and the
# UI loops (`"<tenant> <4-digit port> [dir]"`).
_API_LOOP = re.compile(r'^\s*for spec in\s+(?:"[^"\s]+ [^"\s]+ \d{5}"\s*)+;\s*do\s*$', re.M)


def _api_loops(text: str) -> list[str]:
    """Every ``for spec in "<name> <dir> <port>" …`` loop header, code only."""
    return [m.group(0) for m in _API_LOOP.finditer(_code(text))]


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh"])
@pytest.mark.parametrize("tenant,port", sorted(TENANTS.items()))
def test_every_tenant_api_is_in_every_api_loop(script, tenant, port):
    """restore.sh has two api loops (launch, then wait+record pid) and
    pre-reboot.sh one (stop). A tenant in one but not the other is the bug this
    catches: started and never stopped, or stopped and never restarted."""
    loops = _api_loops((OPS / script).read_text())
    assert loops, f"{script}: no `for spec in …` tenant loop found — did it get restructured?"
    for loop in loops:
        assert f"{tenant} " in loop and port in loop, (
            f"{script}: tenant {tenant} :{port} missing from loop:\n  {loop.strip()}"
        )


def test_restore_has_both_api_loops(restore):
    """Guards the loop above: if a refactor collapses restore.sh's two api
    loops into one the parametrised test would still pass vacuously."""
    assert len(_api_loops(restore)) == 2


def test_hackathon_stores_are_started_by_restore(restore):
    """The three dedicated stores come up through the tenant's own generated
    ``bin/up.sh`` (it owns the binds, the ES ``-E`` args and the pg password),
    and each one is then waited for: two HTTP probes and pg_isready on :24085,
    which the API needs because hackathon's users/ACL/collections/jobs live
    there."""
    code = _code(restore)
    assert "/tenants/hackathon/bin/up.sh" in code or "$HACK/bin/up.sh" in code
    for name in HACKATHON_STORES:
        assert name in code, f"restore.sh never mentions {name}"
    assert "24081/collections" in code, "no readiness wait for qdrant-hackathon :24081"
    assert re.search(r"wait_if_started elasticsearch-hackathon .*24083", code), \
        "no readiness wait for elasticsearch-hackathon :24083"
    assert re.search(r"pg_isready .*-p 24085", code), \
        "no pg_isready check for postgres-hackathon :24085"


def test_hackathon_stores_are_stopped_before_the_shared_ones(pre_reboot):
    """pre-reboot.sh's final check claims 'no apptainer instances left'. That is
    only true if hackathon's three are stopped too — and they must go before the
    shared stores, consumers before providers."""
    code = _code(pre_reboot)
    line = next((l for l in code.splitlines()
                 if "stop_instance" in l and "qdrant-dev" in l), None)
    assert line, "pre-reboot.sh: the step-7 store list is gone or was restructured"
    for name in HACKATHON_STORES:
        assert name in line, f"pre-reboot.sh step 7 does not stop {name}"
    assert line.index("qdrant-hackathon") < line.index("postgres"), \
        "hackathon's stores must be stopped before the shared prod stores"


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh"])
def test_hackathon_is_not_in_the_vite_ui_loops(script):
    """hackathon's UI is static (nginx serves ui/dist); there is no dev server."""
    code = _code((OPS / script).read_text())
    for line in code.splitlines():
        if "for spec in" not in line or not re.search(r"\b(5210|5211|5212|8090)\b", line):
            continue
        assert "hackathon" not in line, (
            f"{script}: hackathon is in a Vite UI loop, but its UI is a static build:\n  {line.strip()}"
        )
        for t in VITE_UIS:
            assert t in line, f"{script}: UI loop lost {t}:\n  {line.strip()}"


def test_tenant_apis_are_launched_with_their_worktree_identity(restore):
    """``/v1/version`` must report the artifact a launch actually runs (ADR-0007).
    restore.sh derives the tag and sha from the TENANT worktree — before this,
    hackathon reported them only because an operator had exported them by hand."""
    code = _code(restore)
    assert "RAGSTACK_GIT_TAG" in code and "RAGSTACK_GIT_SHA" in code
    assert "describe --tags --always" in code and "rev-parse HEAD" in code
    assert "safe.directory" in code, \
        "git must be called with -c safe.directory=* — tenant worktrees can be owned elsewhere"
    assert re.search(r"export .*RAGSTACK_GIT_TAG=.*RAGSTACK_GIT_SHA=", code), \
        "the values are computed but never exported into the uvicorn environment"


def test_the_hackathon_postgres_password_is_not_inlined(restore, pre_reboot):
    """Calling the tenant's generated ``up.sh`` is precisely how its randomly
    generated postgres password stays out of this repo. Re-typing that instance
    definition here would check a live credential into git."""
    for name, text in (("restore.sh", restore), ("pre-reboot.sh", pre_reboot)):
        for m in re.finditer(r"POSTGRES_PASSWORD=(\S+)", text):
            assert not re.fullmatch(r"[0-9a-f]{16,}", m.group(1)), (
                f"{name} appears to inline a generated postgres password"
            )
