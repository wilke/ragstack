"""Tests for apptainer/new-tenant.sh (ADR-0005 tenant provisioning, #247).

Everything here exercises the --dry-run surface (plus the apptainer-free
sqlite real path), so the suite stays offline and CI-safe: dry-run prints the
complete plan and touches nothing — no mkdir, no manifest write, and no
``apptainer`` invocation (CI runners lack the binary).

What can only be verified manually on the deploy host: actual instance
startup, ES health on the allocated port, DSN reachability, persistence
across down/up, and vm.max_map_count sufficiency.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "apptainer" / "new-tenant.sh"


def run_script(args: list[str], rag_data: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RAG_DATA"] = str(rag_data)
    env["RAG_IMAGES"] = str(rag_data / "images")
    env.pop("TENANT_PORT_BASE", None)
    env.pop("TENANT_PORT_STRIDE", None)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def dry_run(name: str, rag_data: Path, *extra: str) -> str:
    proc = run_script([name, "--dry-run", *extra], rag_data)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# --------------------------------------------------------------------------
# Script hygiene
# --------------------------------------------------------------------------


def test_bash_syntax_clean():
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean():
    proc = subprocess.run(
        ["shellcheck", "--severity=warning", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# Name validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["Acme", "a:b", "-acme", "a_b", "acme!", "1acme", "qdrant", "elasticsearch",
     "postgres", "tenants", "public", "a" * 40],
)
def test_invalid_name_rejected(tmp_path, bad):
    proc = run_script([bad, "--dry-run"], tmp_path)
    assert proc.returncode != 0
    assert "ERROR" in proc.stderr


def test_missing_name_rejected(tmp_path):
    proc = run_script(["--dry-run"], tmp_path)
    assert proc.returncode != 0


# --------------------------------------------------------------------------
# Dry-run: complete plan, nothing touched
# --------------------------------------------------------------------------


def test_dry_run_touches_nothing(tmp_path):
    dry_run("acme", tmp_path)
    assert list(tmp_path.iterdir()) == []  # no tenants/, no manifest, nothing


def test_dry_run_plan_enumerates_writable_paths(tmp_path):
    plan = dry_run("acme", tmp_path)
    tdir = f"{tmp_path}/tenants/acme"
    # Every writable path from the persistence-model catalog must be a bind.
    for d in [
        f"{tdir}/qdrant/storage",
        f"{tdir}/qdrant/snapshots",       # snapshot/tmp lock path
        f"{tdir}/elasticsearch/data",
        f"{tdir}/elasticsearch/logs",     # ES writes logs in-image otherwise
        f"{tdir}/elasticsearch/config",   # auto-keystore + autoconfig certs
        f"{tdir}/state",                  # sqlite ACL/registry/jobs
        f"{tdir}/ingest",
        f"{tdir}/manifests",
    ]:
        assert d in plan, f"missing {d} in plan"
    assert "--writable-tmpfs" not in plan
    # binds, not just mkdir lines
    assert '--bind "$TDIR/qdrant/snapshots:/qdrant/snapshots"' in plan
    assert (
        '--bind "$TDIR/elasticsearch/config:/usr/share/elasticsearch/config"' in plan
    )


def test_dry_run_plan_ports_stable_and_offsets(tmp_path):
    plan = dry_run("acme", tmp_path)
    assert "api:            24000" in plan
    assert "qdrant http:    24001" in plan
    assert "qdrant grpc:    24002" in plan
    assert "es http:        24003" in plan
    assert "es transport:   24004" in plan
    assert "new allocation, index 0" in plan
    # env file points at the tenant's own instances
    assert "QDRANT_URL=http://localhost:24001" in plan
    assert "ELASTICSEARCH_URL=http://localhost:24003" in plan
    assert "PORT=24000" in plan


def test_dry_run_plan_env_file_keys(tmp_path):
    plan = dry_run("acme", tmp_path)
    for token in [
        "API_KEYS='[\"<GENERATED:API_KEY_USER>\",\"<GENERATED:API_KEY_ADMIN>\"]'",
        'API_KEY_TENANTS=\'{"<GENERATED:API_KEY_USER>":"acme"',
        "DEFAULT_ROLE=user",
        "IDENTITY_PROVIDER=none",
        "MAX_COLLECTIONS=100",
        "USER_STORE_BACKEND=sqlite",
        f"USER_STORE_PATH={tmp_path}/tenants/acme/state/ragstack_users.db",
        "ACL_BACKFILL_OWNER=legacy:admin",
        "VECTOR_BACKEND=qdrant",
        "TEXT_BACKEND=elasticsearch",
        "JOB_STORE_BACKEND=sqlite",
        "COLLECTION_STORE_BACKEND=sqlite",
        "EMBEDDING_API=openai",
        "EMBEDDING_ENDPOINTS='[",  # shared fleet, JSON single-quoted
        "REQUIRE_DURABLE_BACKENDS=true",
        f"INGEST_ROOT={tmp_path}/tenants/acme/ingest",
        "MAX_DOCUMENT_BYTES=",
        "LOG_LEVEL=info",
    ]:
        assert token in plan, f"missing env token: {token}"


def test_dry_run_es_uses_native_E_args_not_env(tmp_path):
    plan = dry_run("acme", tmp_path)
    # Dotted keys must be -E CLI args after the entrypoint (apptainer --env is
    # shell-sourced and cannot carry dots), and tini must be bypassed.
    assert "-Ediscovery.type=single-node" in plan
    assert "-Expack.security.enabled=false" in plan
    assert "-Ehttp.port=24003" in plan
    assert "-Etransport.port=24004" in plan
    assert "/usr/local/bin/docker-entrypoint.sh eswrapper" in plan
    assert "--env discovery.type" not in plan
    assert "--env xpack" not in plan
    assert "tini" not in [
        tok for line in plan.splitlines() if line.strip().startswith("start ")
        for tok in line.split()
    ]
    # heap via ES_JAVA_OPTS (no dots — env is fine)
    assert 'ES_JAVA_OPTS="-Xms512m -Xmx512m"' in plan


def test_dry_run_qdrant_recipe(tmp_path):
    plan = dry_run("acme", tmp_path)
    # no --cwd in apptainer: CMD wrapped in a cd shell
    assert "/bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'" in plan
    assert "--env QDRANT__SERVICE__HTTP_PORT=24001" in plan
    assert "--env QDRANT__SERVICE__GRPC_PORT=24002" in plan
    # per-tenant instance names, suffixed
    assert "start qdrant-acme" in plan
    assert "start elasticsearch-acme" in plan


def test_dry_run_reuses_shared_sifs(tmp_path):
    plan = dry_run("acme", tmp_path)
    assert f"{tmp_path}/images/qdrant.sif" in plan
    assert f"{tmp_path}/images/elasticsearch.sif" in plan
    assert "apptainer/pull.sh" in plan  # told to pull, never pulls its own


def test_dry_run_idempotent_byte_identical(tmp_path):
    assert dry_run("acme", tmp_path) == dry_run("acme", tmp_path)


# --------------------------------------------------------------------------
# Port allocation: deterministic, manifest-driven, collision-free
# --------------------------------------------------------------------------


def seed_manifest(tmp_path: Path, rows: list[tuple[str, int, int]]) -> None:
    tenants = tmp_path / "tenants"
    tenants.mkdir(parents=True, exist_ok=True)
    lines = ["# tenant\tindex\tbase_port"]
    lines += [f"{n}\t{i}\t{b}" for n, i, b in rows]
    (tenants / "manifest.tsv").write_text("\n".join(lines) + "\n")


def test_existing_manifest_row_reused_verbatim(tmp_path):
    seed_manifest(tmp_path, [("acme", 3, 24060)])
    plan = dry_run("acme", tmp_path)
    assert "from manifest, index 3" in plan
    assert "api:            24060" in plan
    assert "qdrant http:    24061" in plan
    assert "es http:        24063" in plan


def test_new_tenant_gets_next_free_disjoint_block(tmp_path):
    seed_manifest(tmp_path, [("acme", 0, 24000), ("beta", 3, 24060)])
    plan = dry_run("gamma", tmp_path)
    assert "new allocation, index 4" in plan
    assert "api:            24080" in plan
    # no port from the existing blocks leaks into this plan
    for port in ("24000", "24003", "24060", "24063"):
        assert f"http://localhost:{port}" not in plan


def test_manifest_collision_detected(tmp_path):
    # A corrupt/hand-edited manifest whose row collides with another block
    # must be refused, not silently provisioned.
    seed_manifest(tmp_path, [("acme", 0, 24000), ("evil", 1, 24010)])
    proc = run_script(["evil", "--dry-run"], tmp_path)
    assert proc.returncode != 0
    assert "collides" in proc.stderr


# --------------------------------------------------------------------------
# Postgres mode (--postgres <admin-dsn>)
# --------------------------------------------------------------------------


def test_postgres_mode_plan(tmp_path):
    plan = dry_run(
        "acme", tmp_path, "--postgres", "postgresql://admin:pw@dbhost:5433/postgres"
    )
    assert "acl/registry store: postgres" in plan
    assert "postgres provisioning (server dbhost:5433" in plan
    # guarded create statements (idempotent re-run)
    assert "SELECT 1 FROM pg_roles WHERE rolname='acme'" in plan
    assert 'CREATE ROLE \\"acme\\" LOGIN PASSWORD' in plan
    assert 'CREATE DATABASE \\"acme\\" OWNER \\"acme\\"' in plan
    # env swings to per-tenant DATABASE in the provided server (ADR-0004 amendment)
    assert "USER_STORE_BACKEND=postgres" in plan
    assert (
        "USER_STORE_DSN=postgresql://acme:<GENERATED:PG_PASSWORD>@dbhost:5433/acme"
        in plan
    )
    assert (
        "POSTGRES_DSN=postgresql+asyncpg://acme:<GENERATED:PG_PASSWORD>@dbhost:5433/acme"
        in plan
    )
    assert "COLLECTION_STORE_BACKEND=postgres" in plan
    # secrets stay placeholders in dry-run (byte-reproducible output)
    assert "<GENERATED:PG_PASSWORD>" in plan


def test_postgres_dry_run_idempotent(tmp_path):
    args = ("--postgres", "postgresql://admin:pw@dbhost:5433/postgres")
    assert dry_run("acme", tmp_path, *args) == dry_run("acme", tmp_path, *args)


# --------------------------------------------------------------------------
# Real (sqlite) provisioning — apptainer-free, so it can run offline
# --------------------------------------------------------------------------


def test_real_run_sqlite_idempotent(tmp_path):
    p1 = run_script(["acme"], tmp_path)
    assert p1.returncode == 0, p1.stderr
    tdir = tmp_path / "tenants" / "acme"
    env_file = tdir / "config" / "tenant.env"
    manifest = tmp_path / "tenants" / "manifest.tsv"
    assert env_file.is_file()
    assert (tdir / "bin" / "up.sh").is_file()
    assert (tdir / "bin" / "down.sh").is_file()
    assert (tdir / "qdrant" / "storage").is_dir()
    assert (tdir / "elasticsearch" / "config").is_dir()
    assert "acme\t0\t24000" in manifest.read_text()

    env_before = env_file.read_text()
    assert "<GENERATED:" not in env_before  # real secrets stamped
    assert "REQUIRE_DURABLE_BACKENDS=true" in env_before

    p2 = run_script(["acme"], tmp_path)
    assert p2.returncode == 0, p2.stderr
    assert "reusing index 0, base 24000" in p2.stdout
    assert p2.stdout.count("unchanged") >= 4  # secrets, env, up.sh, down.sh
    # secrets never rotated; env byte-identical
    assert env_file.read_text() == env_before
    assert manifest.read_text().count("acme") == 1  # no duplicate row


def test_real_run_keeps_operator_edited_env(tmp_path):
    run_script(["acme"], tmp_path)
    env_file = tmp_path / "tenants" / "acme" / "config" / "tenant.env"
    edited = env_file.read_text() + "# operator edit\n"
    env_file.write_text(edited)

    p = run_script(["acme"], tmp_path)
    assert p.returncode == 0
    assert env_file.read_text() == edited  # kept
    assert "KEEPING" in p.stderr

    p = run_script(["acme", "--force"], tmp_path)
    assert p.returncode == 0
    assert "# operator edit" not in env_file.read_text()  # overwritten


def test_concurrent_provisioning_allocates_disjoint_blocks(tmp_path):
    """Two racing runs must not compute the same max+1 index (flock guards the
    manifest read-modify-write) and neither manifest row may be lost."""
    env = os.environ.copy()
    env["RAG_DATA"] = str(tmp_path)
    env["RAG_IMAGES"] = str(tmp_path / "images")
    env.pop("TENANT_PORT_BASE", None)
    env.pop("TENANT_PORT_STRIDE", None)
    procs = [
        subprocess.Popen(
            ["bash", str(SCRIPT), name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        for name in ("alpha", "beta")
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err

    manifest = tmp_path / "tenants" / "manifest.tsv"
    rows = {
        line.split("\t")[0]: int(line.split("\t")[2])
        for line in manifest.read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert set(rows) == {"alpha", "beta"}  # no lost update
    assert len(set(rows.values())) == 2  # distinct base ports
    urls = set()
    for name in ("alpha", "beta"):
        env_text = (tmp_path / "tenants" / name / "config" / "tenant.env").read_text()
        urls.update(
            line for line in env_text.splitlines() if line.startswith("QDRANT_URL=")
        )
    assert len(urls) == 2  # each tenant dials its own Qdrant


def test_lost_manifest_row_dies_instead_of_port_split_brain(tmp_path):
    """If the manifest row vanished and a re-run allocates a DIFFERENT block,
    the script must die (tenant.env would keep the old ports while up.sh got
    the new ones), not silently split the tenant across two port blocks."""
    run_script(["acme"], tmp_path)
    manifest = tmp_path / "tenants" / "manifest.tsv"
    # lose acme's row, then let another tenant claim acme's recomputed block
    manifest.write_text(
        "".join(
            line
            for line in manifest.read_text().splitlines(keepends=True)
            if not line.startswith("acme\t")
        )
    )
    run_script(["beta"], tmp_path)

    env_file = tmp_path / "tenants" / "acme" / "config" / "tenant.env"
    up_sh = tmp_path / "tenants" / "acme" / "bin" / "up.sh"
    env_before = env_file.read_text()
    up_before = up_sh.read_text()

    p = run_script(["acme"], tmp_path)
    assert p.returncode != 0
    assert "split-brain" in p.stderr
    # nothing rewritten: env and wrappers still agree on the old block
    assert env_file.read_text() == env_before
    assert up_sh.read_text() == up_before


def test_es_heap_persists_across_flagless_rerun(tmp_path):
    """--es-heap is persisted in provision.env; a later flagless re-run must
    not silently revert up.sh to the 512m default."""
    p = run_script(["acme", "--es-heap", "8g"], tmp_path)
    assert p.returncode == 0, p.stderr
    up_sh = tmp_path / "tenants" / "acme" / "bin" / "up.sh"
    assert '-Xms8g -Xmx8g' in up_sh.read_text()
    provision = tmp_path / "tenants" / "acme" / "config" / "provision.env"
    assert "TENANT_ES_HEAP=8g" in provision.read_text()

    p = run_script(["acme"], tmp_path)  # no flags
    assert p.returncode == 0, p.stderr
    assert '-Xms8g -Xmx8g' in up_sh.read_text()  # kept, not 512m
    # a new explicit flag still updates it
    p = run_script(["acme", "--es-heap", "2g"], tmp_path)
    assert p.returncode == 0, p.stderr
    assert '-Xms2g -Xmx2g' in up_sh.read_text()


def test_half_provisioned_tenant_is_completed(tmp_path):
    run_script(["acme"], tmp_path)
    tdir = tmp_path / "tenants" / "acme"
    env_file = tdir / "config" / "tenant.env"
    env_before = env_file.read_text()
    # simulate a half-provisioned tenant: wrappers and a data dir went missing
    (tdir / "bin" / "up.sh").unlink()
    shutil.rmtree(tdir / "qdrant")

    p = run_script(["acme"], tmp_path)
    assert p.returncode == 0, p.stderr
    assert (tdir / "bin" / "up.sh").is_file()
    assert (tdir / "qdrant" / "storage").is_dir()
    assert (tdir / "qdrant" / "snapshots").is_dir()
    # existing pieces untouched (same ports, same secrets)
    assert env_file.read_text() == env_before


# --------------------------------------------------------------------------- #
# Ops-review regressions (independent review of PR #254).
# --------------------------------------------------------------------------- #


def test_keep_mode_diff_redacts_live_secrets(tmp_path):
    """A re-run whose tenant.env was hand-edited prints a diff so the operator
    can see what differs — but a hunk near the key block used to print live
    API_KEYS material to stderr, which `... | tee provision.log` then wrote to
    disk (the same failure class as the prior 0644-env-with-live-keys
    incident). The diff must redact every secret-shaped assignment."""
    proc = run_script(["acme"], tmp_path)
    if proc.returncode != 0:
        pytest.skip(f"real provisioning unavailable here: {proc.stderr[-200:]}")
    env_file = tmp_path / "tenants" / "acme" / "config" / "tenant.env"
    original = env_file.read_text(encoding="utf-8")
    assert "API_KEYS=" in original
    # Edit a line near the key block so the diff hunk spans the secrets.
    env_file.write_text(
        original.replace("DEFAULT_ROLE=user", "DEFAULT_ROLE=admin"), encoding="utf-8"
    )
    rerun = run_script(["acme"], tmp_path)
    assert rerun.returncode == 0, rerun.stderr
    assert "KEEPING the existing file" in rerun.stderr
    # The real key values must not appear anywhere in the operator-visible output.
    for line in original.splitlines():
        if line.startswith("API_KEYS="):
            secret = line.split("=", 1)[1].strip()
            assert len(secret) > 8
            assert secret not in rerun.stderr, "live API key leaked into the diff"
            assert secret not in rerun.stdout
    assert "<REDACTED>" in rerun.stderr


def test_allocation_refuses_a_port_the_manifest_does_not_know_is_taken(tmp_path):
    """The manifest only tracks tenants provisioned under THIS RAG_DATA. Run
    from a checkout whose RAG_DATA differs from the deployment's and a fresh
    manifest hands out a block a live tenant already holds — the stamped
    tenant.env would then dial another tenant's stores. Ports must be probed
    for real, not merely looked up in the manifest."""
    import socket

    if not shutil.which("ss") and not shutil.which("netstat"):
        pytest.skip("no ss/netstat available to probe ports")
    # Occupy the API port of the first block (base 24000 + 0).
    sock = socket.socket()
    try:
        try:
            sock.bind(("127.0.0.1", 24000))
        except OSError:
            pytest.skip("port 24000 not bindable in this environment")
        sock.listen(1)
        proc = run_script(["acme", "--dry-run"], tmp_path)
        assert proc.returncode != 0, "must refuse a block whose port is already in use"
        assert "already in use" in proc.stderr
    finally:
        sock.close()
