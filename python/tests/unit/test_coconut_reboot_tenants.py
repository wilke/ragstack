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
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


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
    hits = [line.strip() for line in code.splitlines()
            if name in line and f"ragstack-{name}" not in line]
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
    hits = [line.strip() for line in code.splitlines() if re.search(rf"\b{port}\b", line)]
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
    up_uses = [line.strip() for line in code.splitlines() if "tenant_up_script" in line and "()" not in line]
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


# The heap drift these two tests are about: the row records what the store is
# really running with, ``bin/up.sh`` records what the tenant was provisioned
# with, and they disagree. Planning up.sh's value is the silent halving.
_ROW_HEAP = "1g"
_STALE_HEAP = "512m"


def _heap_registry(tmp_path: Path, owner: str) -> Path:
    """A one-tenant registry with an exclusively-owned elasticsearch leg.

    ``solo`` owns its elasticsearch outright, its row records ``heap: 1g``, and
    its provision-time ``bin/up.sh`` carries a stale ``512m`` — the exact drift
    the ``tenant_up_script`` comment in ``restore.sh`` documents. ``owner``
    selects the branch under test: anything but ``svcbvbrc`` is a tenant this
    account still supervises by hand, ``svcbvbrc`` is one handed over to the
    control plane.

    Everything is synthetic on purpose. This used to read the LIVE registry and
    assert on whatever ``dev``'s row said, so it broke the day ``dev`` was handed
    over to the control plane and ``restore.sh`` — correctly — stopped planning
    its heap at all. A unit test coupled to fleet state re-breaks on every
    handover; the subject here is the script's derivation, not the fleet.

    Ports are in the 258xx range and the instance name is not one any host runs,
    so a dry run that somehow stopped being dry could not collide with a live
    service, and ``start_instance`` cannot short-circuit on an instance that
    happens to be up (it prints "already running — skipping" and no command).
    """
    data = tmp_path / "data" / "solo"
    (data / "bin").mkdir(parents=True)
    up_sh = data / "bin" / "up.sh"
    up_sh.write_text(
        "#!/bin/bash\n"
        "# provision-time launcher — carries the heap the tenant was CREATED with\n"
        f'exec apptainer instance run --env ES_JAVA_OPTS="-Xms{_STALE_HEAP} -Xmx{_STALE_HEAP}" "$@"\n'
    )
    up_sh.chmod(0o755)
    # start_instance only checks that the image file EXISTS before printing the
    # plan; it is never executed under --dry-run.
    sif = tmp_path / "elasticsearch.sif"
    sif.write_text("")

    reg = {
        "schema_version": 1,
        "display_order": ["solo"],
        "tenants": {
            "solo": {
                "name": "solo", "manifest_name": "solo", "owner": owner, "state": "active",
                "data_dir": str(data),
                "worktree": str(tmp_path / "repos" / "solo"),
                "python_env": "/rag/envs/ragstack",
                "ports": {"index": 91, "base": 25840, "api": 25840,
                          "es_http": 25843, "es_transport": 25844},
                "api": {"bind": "127.0.0.1", "pidfile": "", "log": ""},
                "ui": {"mode": "static", "port": None, "base": "/ragstack/solo/ui/"},
                "stores": {
                    "qdrant": {"ownership": "shared", "instance": None},
                    "elasticsearch": {"ownership": "exclusive",
                                      "instance": "elasticsearch-unittest",
                                      "sif": str(sif),
                                      "heap": _ROW_HEAP},
                    "postgres": {"kind": "sqlite"},
                },
            },
        },
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(reg))
    return path


def _stores_dry_run(registry: Path, tmp_path: Path) -> str:
    env = dict(os.environ, REGISTRY=str(registry), RUN=str(tmp_path / "run"))
    out = subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run", "--tenant", "solo", "--only", "stores"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    # The dry run prints the argv through printf %q, so a value with a space in
    # it comes out escaped (`-Xms1g\ -Xmx1g`). Unescape before matching: the
    # assertion is about the VALUE, not about how the preview quotes it.
    return (out.stdout + out.stderr).replace("\\ ", " ")


@requires_shell
def test_a_supervised_tenants_dry_run_plans_the_rows_heap_not_up_shs(tmp_path):
    """A tenant this account still supervises: the plan comes from the row.

    The row says 1g and ``bin/up.sh`` says 512m. The dry run must plan the row's
    value — planning up.sh's would quietly halve the heap of a store that came
    back after a reboot.
    """
    text = _stores_dry_run(_heap_registry(tmp_path, owner="wilke"), tmp_path)
    assert f"-Xms{_ROW_HEAP} -Xmx{_ROW_HEAP}" in text, (
        f"the dry run does not plan the row's heap ({_ROW_HEAP}):\n{text}"
    )
    # And the provision-time value is nowhere near it. up.sh is not even invoked
    # for this leg: it is the postgres launcher only (it holds that role's
    # generated password), and using it for elasticsearch is what would bring
    # the stale value back.
    assert re.search(rf"-Xmx{_STALE_HEAP}\b", text) is None, (
        f"the dry run planned up.sh's stale heap {_STALE_HEAP} "
        f"instead of the row's {_ROW_HEAP}:\n{text}"
    )
    assert "bin/up.sh" not in text, (
        f"the elasticsearch leg was planned through the provision-time up.sh:\n{text}"
    )
    # The instance, the image and the ports come from the row too.
    assert "elasticsearch-unittest" in text, text
    assert "25843" in text and "25844" in text, text


@requires_shell
def test_a_handed_over_tenants_dry_run_plans_no_heap_at_all(tmp_path):
    """The same row, owned by the control plane: nothing of it is planned.

    ``owner: svcbvbrc`` means svcbvbrc's ``@reboot`` line starts it. Planning its
    store here would put two starters on one port — so the heap, the instance and
    the ports must all be absent, and the run must still succeed.
    """
    text = _stores_dry_run(_heap_registry(tmp_path, owner="svcbvbrc"), tmp_path)
    assert "handed over" in text, text
    assert f"-Xmx{_ROW_HEAP}" not in text, (
        f"a handed-over tenant's heap was planned:\n{text}"
    )
    assert "elasticsearch-unittest" not in text, (
        f"a handed-over tenant's store instance was planned:\n{text}"
    )
    assert "25843" not in text and "25844" not in text, (
        f"a handed-over tenant's store ports were planned:\n{text}"
    )


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


@requires_shell
def test_a_tenant_mid_handover_is_skipped_by_a_fleet_run_and_started_by_name(tmp_path):
    """``state: handover`` is the window between the release and the take: the
    owner has stopped the tenant and the service account has not yet started it.

    A fleet-wide boot must not start it — that would put a second copy on the
    port the take is about to bind — and an operator who NAMES it must get it
    back, because ``restore.sh --tenant <n>`` is the documented way out of a
    handover that did not convince.
    """
    registry = _synthetic_registry(tmp_path)
    doc = json.loads(registry.read_text())
    doc["tenants"]["alpha"]["state"] = "handover"
    registry.write_text(json.dumps(doc))
    env = dict(os.environ, REGISTRY=str(registry), RUN=str(tmp_path / "run"))

    fleet = subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    text = fleet.stdout + fleet.stderr
    assert fleet.returncode == 0, text
    assert "mid-handover" in text, text
    assert "NOT started by a fleet-wide run" in text, text
    assert "uvicorn" not in text.split("mid-handover")[-1].split("alpha")[0], text

    named = subprocess.run(
        ["bash", str(OPS / "restore.sh"), "--dry-run", "--tenant", "alpha"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    named_text = named.stdout + named.stderr
    assert named.returncode == 0, named_text
    assert "starting it anyway because you named it" in named_text, named_text
    # And it really does plan the tenant rather than only talking about it.
    assert "25800" in named_text, named_text
    assert "--abandon" in named_text, "the way back does not say how to make the row agree"
