"""The coconut reboot runbook is REGISTRY-DRIVEN (``ops/coconut/``).

``restore.sh``, ``pre-reboot.sh`` and ``snapshot.sh`` used to carry literal
lists of tenants — which APIs to start and stop, which apptainer instances were
stores, which ports to probe — and a tenant missing from any one of them
silently did not come back after a reboot. That is how ``hackathon`` came to be
absent from all of them while attendees were using it.

Since PR-E the lists are gone: the three scripts read
``/rag/data/tenants/registry.json`` (the two bash ones with ``jq``, the Python
one with ``json.load``) and derive every tenant fact from the row. So the thing
worth asserting has changed shape. It is no longer "is every tenant in every
list" — it is:

* no tenant NAME and no tenant PORT is spelled in the executable text at all,
  so there is no list left to forget one from;
* the registry really is read, and its structure used (owner, ui.mode,
  api.bind, stores.*.instance) rather than merely opened;
* a row handed over to the control plane (``owner: svcbvbrc``) is skipped, so
  two starters never race for one port;
* ``--tenant NAME`` exists on both bash scripts and is name-validated, because
  it is the rollback half of a handover;
* a ``static`` UI never enters a Vite loop — it has no process, and waiting for
  one is a timeout for a tenant that is perfectly healthy.

These assertions are deliberately string/regex based: the subject is the text
of two bash scripts and one embedded Python program. The last two tests are the
exception — they RUN ``restore.sh --dry-run`` against a synthetic registry in a
temporary directory and read the plan it prints, which is the only way to prove
the derivation end to end.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[3] / "ops" / "coconut"

# The tenants that exist on coconut today. NONE of them may appear in the
# executable text of any of the three scripts: that is the whole point.
LIVE_TENANT_NAMES = ("lucid-next", "asm-next", "hackathon", "demo")
# The ports those tenants hold. `dev` is not in the name list above — it is an
# ordinary English word that appears in `ui.mode == dev`, in paths and in
# comments — so it is covered by its ports instead.
LIVE_TENANT_PORTS = (
    "24000", "24020", "24040", "24060", "24080",           # APIs
    "24041", "24043", "24081", "24083", "24085", "24003",  # per-tenant stores
    "5210", "5211", "5212", "8090",                        # Vite dev servers
)


@pytest.fixture(scope="module")
def restore() -> str:
    return (OPS / "restore.sh").read_text()


@pytest.fixture(scope="module")
def pre_reboot() -> str:
    return (OPS / "pre-reboot.sh").read_text()


@pytest.fixture(scope="module")
def snapshot() -> str:
    return (OPS / "snapshot.sh").read_text()


def _code(text: str) -> str:
    """The script minus its full-line comments — what actually executes."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# --------------------------------------------------------------------------- #
# No lists left to forget a tenant from
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh", "snapshot.sh"])
@pytest.mark.parametrize("name", LIVE_TENANT_NAMES)
def test_no_tenant_name_is_spelled_in_the_code(script, name):
    """A tenant name in the executable text is a list somebody has to maintain.

    GoWe's worker GROUPS are the documented exception. `ragstack-hackathon` is
    the name of a worker pool in GoWe's own configuration, not a registry row —
    a labelled submission never falls back to another group, so the count is
    asserted per group (#563) and the name has to be spelled. It is excluded by
    its `ragstack-` prefix rather than by name, so a second such group is
    covered too.
    """
    code = _code((OPS / script).read_text())
    hits = [l.strip() for l in code.splitlines()
            if name in l and f"ragstack-{name}" not in l]
    assert hits == [], (
        f"{script} names the tenant {name!r} in code — the registry is the list:\n  "
        + "\n  ".join(hits)
    )


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh", "snapshot.sh"])
@pytest.mark.parametrize("port", LIVE_TENANT_PORTS)
def test_no_tenant_port_is_spelled_in_the_code(script, port):
    """Ports are the other half of the same list. The shared stores' ports are
    allowed (they belong to no tenant) and are asserted separately below."""
    code = _code((OPS / script).read_text())
    hits = [l.strip() for l in code.splitlines() if re.search(rf"\b{port}\b", l)]
    assert hits == [], (
        f"{script} carries the tenant port {port} in code — it comes from the row:\n  "
        + "\n  ".join(hits)
    )


def test_the_shared_stores_are_still_literal(restore, pre_reboot):
    """The furniture is NOT registry-driven and must not become so by accident:
    the shared qdrant/elasticsearch/postgres/redis/neo4j belong to no tenant, and
    a `--tenant` restore must leave them exactly where it found them."""
    code = _code(restore)
    for port in ("6333", "6343", "9200"):
        assert port in code, f"restore.sh no longer starts or waits for the shared store on :{port}"
    assert "the shared stores are not this tenant's and are left alone" in code
    assert "neo4j-dev" in _code(pre_reboot), \
        "neo4j-dev is in no registry row and must stay literal until something records it"


# --------------------------------------------------------------------------- #
# The registry is really read, and its structure used
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh"])
def test_the_bash_scripts_read_the_registry_with_jq(script):
    code = _code((OPS / script).read_text())
    assert "REGISTRY=${REGISTRY:-" in code, f"{script}: the registry path is not overridable"
    assert "JQ=${JQ:-/usr/bin/jq}" in code, f"{script}: jq is not resolved to an absolute path"
    assert "reg_ready" in code and "reg_rows" in code, f"{script}: no registry helpers"
    # display_order is the order the gateway advertises; a script that ignored
    # it would start the tenants in whatever order jq's `keys` returned.
    assert "display_order" in code, f"{script}: the tenant order is not the registry's"


def test_snapshot_reads_the_registry_in_python(snapshot):
    code = _code(snapshot)
    assert "json.load(fh)" in code and "REGISTRY" in code
    assert "def reg_tenants()" in code and "def reg_store_ports(" in code
    # A snapshot must not fail for want of a registry: it is the thing verify.sh
    # diffs against, and half a baseline is better than none.
    assert "the per-tenant sections will be empty" in code


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh"])
def test_a_handed_over_tenant_is_skipped(script):
    """`owner: svcbvbrc` means the control plane starts it from its own @reboot
    line. Two starters for one tenant is two servers on one port."""
    text = (OPS / script).read_text()
    code = _code(text)
    assert "svcbvbrc" in code, f"{script} does not look at the row's owner"
    assert "== svcbvbrc" in code, f"{script} does not skip a row owned by the service account"
    assert "fleet start --all" in text or "tenant stop" in text, \
        f"{script} does not say WHAT starts or stops a handed-over tenant instead"


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh"])
def test_tenant_scoped_runs_exist_and_validate_the_name(script):
    """`--tenant NAME` is the rollback half of a handover (PR-E: release → take
    → restore.sh --tenant <n>)."""
    code = _code((OPS / script).read_text())
    assert "--tenant)" in code, f"{script} has no --tenant flag"
    assert "^[a-z][a-z0-9-]{0,31}$" in code, \
        f"{script} does not validate --tenant against the tenant-name grammar"


def test_the_api_launch_is_derived_from_the_row(restore):
    """Bind, port, pidfile, log, worktree and python env all come from the row —
    api.bind especially: it is where `tenant set-bind` writes, and a script that
    kept `--host 0.0.0.0` would undo that at the next reboot."""
    code = _code(restore)
    for field in ("'.api.bind'", "'.ports.api'", "'.api.pidfile'", "'.api.log'",
                  "'.worktree'", "'.python_env'", "'.data_dir'"):
        assert field in code, f"restore.sh does not read {field} from the registry row"
    assert '--host "$bind"' in code, "restore.sh does not launch uvicorn with the row's bind"
    api_section = code.split("== tenant APIs")[-1].split("== tenant UIs")[0]
    assert "0.0.0.0" not in api_section, "the API launch still carries a hard-coded bind"
    # ADR-0007: /v1/version must report the artifact this launch actually runs.
    assert "RAGSTACK_GIT_TAG" in code and "RAGSTACK_GIT_SHA" in code
    assert "describe --tags --always" in code and "rev-parse HEAD" in code
    assert "safe.directory" in code, \
        "git must be called with -c safe.directory=* — tenant worktrees can be owned elsewhere"


def test_the_store_instances_come_from_the_row(restore, pre_reboot):
    for script, text in (("restore.sh", restore), ("pre-reboot.sh", pre_reboot)):
        code = _code(text)
        for field in ("'.stores.qdrant.instance'", "'.stores.elasticsearch.instance'",
                      "'.stores.postgres.kind'"):
            assert field in code, f"{script} does not read {field}"
        assert "exclusive" in code, \
            f"{script} does not distinguish an exclusive leg from a shared one"
    code = _code(restore)
    # The heap, the image and the ports come from the ROW, not from the
    # tenant's own up.sh — see test_qdrant_and_es_are_rebuilt_from_the_row.
    for field in ("'.stores.elasticsearch.heap'", "'.stores.elasticsearch.sif'",
                  "'.stores.qdrant.sif'", "'.ports.es_transport'",
                  # read inside the jq expression that builds the env object
                  "$t.ports.qdrant_grpc"):
        assert field in code, f"restore.sh does not read {field} from the registry row"
    assert "extra_env" in code, \
        "restore.sh ignores stores.*.extra_env, which is the environment the store is really running with"


def test_qdrant_and_es_are_rebuilt_from_the_row_not_from_up_sh(restore):
    """``up.sh`` is a PROVISION-TIME artefact and its values drift.

    dev's says ``ES_JAVA_OPTS=-Xms512m -Xmx512m``; the elasticsearch that has
    been serving dev for months runs with 1g, the registry records that
    (``stores.elasticsearch.heap``, ``extra_env``) and carries an
    ``es_heap_drift`` row saying the two disagree. A restore that ran up.sh
    would quietly halve the heap of a store that came back after a reboot.

    So up.sh is used for exactly one thing — the postgres leg, whose generated
    password must not appear in this repo — and qdrant and elasticsearch are
    rebuilt from the row.
    """
    code = _code(restore)
    assert "tenant_qdrant_up" in code and "tenant_es_up" in code, \
        "restore.sh has no per-kind reconstruction"
    # The launcher is reached from the postgres branch and nowhere else.
    up_uses = [l.strip() for l in code.splitlines() if "tenant_up_script" in l and "()" not in l]
    assert up_uses, "restore.sh never calls tenant_up_script"
    for line in up_uses:
        assert "postgres" in line or "up=$(tenant_up_script" in line, \
            f"up.sh is reached outside the postgres branch: {line}"
    assert "it holds the generated password" in code, \
        "restore.sh does not say WHY the postgres leg goes through the tenant's launcher"


@pytest.mark.parametrize("script", ["restore.sh", "pre-reboot.sh", "snapshot.sh"])
def test_a_static_ui_never_enters_a_vite_loop(script):
    """A `static` UI is a directory nginx serves: there is no dev server to
    start, stop or probe, and treating one as a port is a timeout for a tenant
    whose UI is perfectly healthy."""
    text = (OPS / script).read_text()
    code = _code(text)
    assert re.search(r'== dev \|\| \$mode == external|dev\|external\)|\("dev", "external"\)', code), \
        f"{script} does not select UIs by ui.mode dev|external"
    # The selection is what matters; saying WHY in the file is what keeps the
    # next editor from "fixing" it by adding every tenant back.
    assert "static" in text, f"{script} says nothing about a static UI"


def test_the_postgres_password_is_never_inlined(restore, pre_reboot):
    """Calling the tenant's generated ``up.sh`` is precisely how its randomly
    generated postgres password stays out of this repo; a reconstruction that
    invented one would put a live credential in git."""
    for name, text in (("restore.sh", restore), ("pre-reboot.sh", pre_reboot)):
        for m in re.finditer(r"POSTGRES_PASSWORD=(\S+)", text):
            assert not re.fullmatch(r"[0-9a-f]{16,}", m.group(1)), (
                f"{name} appears to inline a generated postgres password"
            )
    # And the reconstruction path says so out loud rather than defaulting.
    assert "needs $data/bin/up.sh" in _code(restore), \
        "restore.sh does not refuse to reconstruct a dedicated postgres without its launcher"


# --------------------------------------------------------------------------- #
# The end-to-end proof: run the dry run and read the plan
# --------------------------------------------------------------------------- #

requires_shell = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("jq") and shutil.which("ss")),
    reason="needs bash, jq and ss to run the script's own dry run",
)


def _synthetic_registry(tmp_path: Path) -> Path:
    """A two-tenant registry: one this account owns, one handed over.

    The ports are in the 258xx range on purpose — nothing on any host runs
    there, so a dry run that somehow stopped being dry could not collide with a
    live service.
    """
    reg = {
        "schema_version": 1,
        "display_order": ["alpha", "beta"],
        "tenants": {
            "alpha": {
                "name": "alpha", "manifest_name": "alpha", "owner": "wilke", "state": "active",
                "data_dir": str(tmp_path / "data" / "alpha"),
                "worktree": str(tmp_path / "repos" / "alpha"),
                "python_env": "/rag/envs/ragstack",
                "ports": {"index": 90, "base": 25800, "api": 25800, "qdrant_http": 25801,
                          "qdrant_grpc": 25802, "es_http": 25803, "es_transport": 25804,
                          "pg": 25805},
                "api": {"bind": "127.0.0.1",
                        "pidfile": str(tmp_path / "data" / "alpha" / "api-alpha.pid"),
                        "log": str(tmp_path / "data" / "alpha" / "logs" / "api-alpha.log")},
                "ui": {"mode": "external", "port": 25810, "base": "/ragstack/alpha/ui/"},
                "stores": {
                    "qdrant": {"ownership": "exclusive", "instance": "qdrant-alpha",
                               "sif": "/rag/apptainer/images/qdrant.sif"},
                    "elasticsearch": {"ownership": "shared", "instance": None},
                    "postgres": {"kind": "sqlite"},
                },
            },
            "beta": {
                "name": "beta", "manifest_name": "beta", "owner": "svcbvbrc", "state": "active",
                "data_dir": str(tmp_path / "data" / "beta"),
                "worktree": str(tmp_path / "repos" / "beta"),
                "ports": {"api": 25820},
                "api": {"bind": "127.0.0.1", "pidfile": "", "log": ""},
                "ui": {"mode": "static", "port": None, "base": "/ragstack/beta/ui/"},
                "stores": {"qdrant": {"ownership": "shared"},
                           "elasticsearch": {"ownership": "shared"},
                           "postgres": {"kind": "sqlite"}},
            },
        },
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(reg))
    (tmp_path / "data" / "alpha" / "config").mkdir(parents=True)
    (tmp_path / "data" / "alpha" / "config" / "tenant.env").write_text("LOG_LEVEL=INFO\n")
    return path


def _dry_run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    registry = _synthetic_registry(tmp_path)
    env = dict(os.environ, REGISTRY=str(registry), RUN=str(tmp_path / "run"))
    return subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run", *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


@requires_shell
def test_the_dry_run_derives_the_plan_from_the_registry(tmp_path):
    """The proof the string assertions cannot give: a dry run against a registry
    this test wrote produces a plan naming that registry's tenants, ports and
    binds — and skips the handed-over one."""
    out = _dry_run(tmp_path, "--only", "apis,uis")
    text = out.stdout + out.stderr
    assert "alpha" in text, f"the dry run never mentions the registry's tenant:\n{text}"
    assert "25800" in text, f"the API port did not come from the row:\n{text}"
    assert "--host 127.0.0.1" in text, f"the bind did not come from api.bind:\n{text}"
    assert "25810" in text, f"the external UI's port did not come from the row:\n{text}"
    # The handed-over tenant is announced and then left alone.
    assert "handed over to the control plane" in text
    assert "25820" not in text, f"a handed-over tenant's API was planned:\n{text}"


@requires_shell
def test_a_tenant_scoped_dry_run_touches_one_tenant(tmp_path):
    out = _dry_run(tmp_path, "--tenant", "alpha")
    text = out.stdout + out.stderr
    assert "the shared stores are not this tenant's" in text
    assert "25800" in text
    assert "beta" not in text, f"--tenant alpha planned something for beta:\n{text}"


@requires_shell
def test_the_dev_dry_run_plans_the_live_heap_not_up_shs(tmp_path):
    """The regression this PR's review found, against the LIVE registry.

    dev's ``bin/up.sh`` says 512m and its row says 1g. The dry run must plan the
    row's value — and must not plan up.sh's, which would be the silent halving.

    The instance names are renamed in a scratch copy of the registry so that
    ``start_instance`` does not short-circuit on the instances that are actually
    running: a dry run of a live host prints "already running — skipping" and
    shows no command at all.
    """
    live = Path("/rag/data/tenants/registry.json")
    if not live.exists():
        pytest.skip("no live registry on this host")
    doc = json.loads(live.read_text())
    row = (doc.get("tenants") or {}).get("dev")
    if row is None:
        pytest.skip("no dev row on this host")
    heap = ((row.get("stores") or {}).get("elasticsearch") or {}).get("heap")
    if not heap:
        pytest.skip("the dev row records no elasticsearch heap")
    row["stores"]["qdrant"]["instance"] = "qdrant-conftest"
    row["stores"]["elasticsearch"]["instance"] = "elasticsearch-conftest"
    scratch = tmp_path / "registry.json"
    scratch.write_text(json.dumps({"schema_version": doc.get("schema_version", 1),
                                   "display_order": ["dev"], "tenants": {"dev": row}}))

    env = dict(os.environ, REGISTRY=str(scratch), RUN=str(tmp_path / "run"))
    out = subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run", "--tenant", "dev", "--only", "stores"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    # The dry run prints the argv through printf %q, so a value with a space in
    # it comes out escaped (`-Xms1g\ -Xmx1g`). Unescape before matching: the
    # assertion is about the VALUE, not about how the preview quotes it.
    text = (out.stdout + out.stderr).replace("\\ ", " ")
    assert f"-Xms{heap} -Xmx{heap}" in text, (
        f"the dry run does not plan the row's heap ({heap}):\n{text}"
    )
    # And the provision-time value is nowhere near it.
    up_sh = Path(row["data_dir"]) / "bin" / "up.sh"
    if up_sh.exists():
        stale = re.findall(r"-Xmx(\d+[mg])", up_sh.read_text())
        for value in stale:
            if value != heap:
                assert f"-Xmx{value}" not in text, (
                    f"the dry run planned up.sh's stale heap {value} instead of the row's {heap}:\n{text}"
                )
    # The ports and the image come from the row too.
    assert str(row["ports"]["es_http"]) in text and str(row["ports"]["es_transport"]) in text


@requires_shell
def test_a_handed_over_tenant_is_not_a_failure(tmp_path):
    """``--tenant`` naming a row the control plane owns is the system working:
    the script says so and exits 0. Only a name nobody knows is a failure."""
    registry = _synthetic_registry(tmp_path)
    env = dict(os.environ, REGISTRY=str(registry), RUN=str(tmp_path / "run"))
    out = subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run", "--tenant", "beta"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    text = out.stdout + out.stderr
    assert "handed over" in text, text
    assert out.returncode == 0, f"a handed-over tenant failed the run:\n{text}"

    stop = subprocess.run(
        ["bash", str(OPS / "pre-reboot.sh"), "--dry-run", "--tenant", "beta"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert "nothing for this script to stop" in stop.stdout + stop.stderr


@requires_shell
def test_pre_reboot_reports_a_refusal_as_a_failure(tmp_path):
    """A pre-reboot that reported success over a REFUSAL would be a reboot taken
    with a tenant still writing. An unknown --tenant is the cheapest way to
    reach a non-zero exit without signalling anything."""
    registry = _synthetic_registry(tmp_path)
    env = dict(os.environ, REGISTRY=str(registry), RUN=str(tmp_path / "run"))
    out = subprocess.run(
        ["bash", str(OPS / "pre-reboot.sh"), "--dry-run", "--tenant", "nosuch"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert out.returncode != 0, out.stdout + out.stderr
    assert "no registry row for 'nosuch'" in out.stdout + out.stderr
    # And the refusal path itself is wired: the API stop returns non-zero and
    # the tenant-scoped run propagates it.
    code = _code((OPS / "pre-reboot.sh").read_text())
    assert "stop_tenant_api \"$TENANT\" || trc=1" in code, \
        "a refused API stop does not reach the exit status"
    assert "exit $trc" in code, "the tenant-scoped path does not propagate its failures"


@requires_shell
def test_an_unknown_tenant_is_a_refusal_not_a_silent_no_op(tmp_path):
    out = _dry_run(tmp_path, "--tenant", "nosuch")
    assert "no registry row for 'nosuch'" in out.stdout + out.stderr
    assert out.returncode != 0, "a --tenant that matched nothing exited 0"
