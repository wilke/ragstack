"""Report-only inventory of physical stores versus the registries that claim them.

**This module never deletes anything and never will.** It exists because the
measurement behind #293 found 24 physical stores (~403 GB Qdrant, ~29 GB ES) that
no *running* registry claims — and established that "no registry claims it" is
not the same statement as "it is garbage". Two thirds of that residue is
production corpora whose API is merely stopped. So the output labels a store
``unclaimed-by-known-registries``, never ``orphan``, and reclamation is an
operator reading the report and issuing per-name deletes.

Three requirements fall out of that measurement, and they are the whole design:

1. **Key on ``(backend_url, name)``, never name alone.** There are live
   cross-instance name collisions, and one of each pair is production.
2. **Reconcile against the union of ALL deployments' registries, including
   stopped ones**, read from config files rather than live APIs. A deployment
   that is down still owns its data.
3. **Report the two legs separately and never pair them by name.** A collection's
   vector leg and text leg may have different names and live on different
   servers; both are true on this host today. Any name-based pairing invents
   half-orphans.

A claim comes from two places, and missing either produces a false "unclaimed":

* every spec in the deployment's collection registry, and
* the deployment's own settings-derived (or pinned) default store, which has no
  registry row at all. The largest production corpora on this host are claimed
  only this way.

To keep the second one from drifting, this module does not re-derive the default
name: it builds a real :class:`~ragstack.config.Settings` from the config file
and calls the same private helpers the API calls. Re-implementing that
derivation is precisely how a reporting tool starts telling operators that
production is unclaimed.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import unittest.mock
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

VECTOR = "vector"
TEXT = "text"

#: Status vocabulary. Deliberately none of these is "orphan" — see module docstring.
CLAIMED = "claimed"
#: Matched only by a registry whose backend URL could not be determined (a bare
#: collections file with no env beside it). The name matches; the instance is
#: unverified. Weaker evidence, reported as such rather than silently upgraded.
CLAIMED_NAME_ONLY = "claimed-name-only"
UNCLAIMED = "unclaimed-by-known-registries"
#: A registry entry whose physical store is absent from the probed backend. The
#: other direction of the same reconciliation, and a direct measure of ADR-0002
#: decision 5 breaking the other way.
MISSING = "claimed-but-absent"

#: Elasticsearch's own bookkeeping indices; never a RAGStack store.
_ES_SYSTEM_PREFIX = "."

#: Keys these config files legitimately carry for the launch script rather than
#: the app. Filtered out of the "unrecognised keys" warning so a real typo — the
#: thing that warning exists to catch — is not buried in expected noise.
_LAUNCHER_KEYS = frozenset({"PORT", "HF_HOME", "RAG_DATA", "RAG_ENV", "RAG_IMAGES", "RAG_REPO"})


# ---------------------------------------------------------------------------
# config discovery
# ---------------------------------------------------------------------------


def parse_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a shell-sourceable ``KEY=value`` config file.

    Uses shell quoting rules via :mod:`shlex` because these files *are* sourced
    by the launch scripts, and a parser that disagreed with bash would describe a
    deployment that does not exist. Note the corollary that bit us once already:
    a JSON value must be single-quoted in these files, or the shell strips the
    inner double quotes and the API fails to start.
    """
    out: dict[str, str] = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:  # unbalanced quotes — keep the raw text, flag nothing
            parts = [value]
        out[key] = parts[0] if parts else ""
    return out


def settings_from_env(values: dict[str, str]) -> tuple[Any, list[str]]:
    """Build a :class:`Settings` from parsed config values.

    The values are installed as *the whole environment* and ``Settings()`` is
    constructed the way the API constructs it, rather than passed as init
    kwargs. Two reasons, both load-bearing:

    * **Fidelity.** Complex fields (``qdrant_collection_routes`` is a ``dict``)
      are JSON-decoded by pydantic-settings' env source and *not* by init
      kwargs, so the kwargs route rejects a config the API accepts — and a
      deployment this tool cannot describe is a deployment whose stores all
      report unclaimed.
    * **Isolation.** ``clear=True`` plus ``_env_file=None`` keeps the operator's
      own shell and working directory out of another deployment's description.

    Returns the settings plus the keys that are not settings fields — reported
    rather than dropped, since a typo'd key is a deployment that is not
    configured the way its operator believes.
    """
    from ragstack.config import Settings

    fields = set(Settings.model_fields)
    unknown = sorted(
        k for k in values if k.lower() not in fields and k not in _LAUNCHER_KEYS
    )
    env = {k: v for k, v in values.items() if k.lower() in fields}
    # `_env_file` is a pydantic-settings runtime kwarg that mypy cannot see on a
    # generated __init__; the Any alias keeps the call untyped rather than
    # scattering an ignore comment.
    factory: Any = Settings
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        return factory(_env_file=None), unknown


@dataclass(frozen=True)
class StoreKey:
    """The only safe identity for a physical store."""

    backend: str  # canonical URL; "" = unknown instance (name-only claim)
    name: str

    def __str__(self) -> str:
        return f"{self.backend or '<unknown>'}::{self.name}"


@dataclass(frozen=True)
class Claim:
    key: StoreKey
    leg: str  # VECTOR | TEXT
    deployment: str
    source: str  # "registry:<id>" | "settings-default"


@dataclass
class Deployment:
    """One configuration — running or not — and everything it claims."""

    name: str
    config_path: str
    qdrant_url: str = ""
    es_url: str = ""
    claims: list[Claim] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)
    #: Credentials harvested from the config so probes can reach a secured
    #: backend. Never rendered — see :func:`render_text` / :func:`to_dict`.
    qdrant_api_key: str = ""
    es_api_key: str = ""


@dataclass
class PhysicalStore:
    key: StoreKey
    leg: str
    count: int | None = None  # points (Qdrant) / docs (ES)
    size_bytes: int | None = None
    created_at: str = ""
    status: str = ""  # backend-reported health, e.g. "green" / "yellow"


@dataclass
class Row:
    """One reconciled line of the report."""

    key: StoreKey
    leg: str
    status: str
    store: PhysicalStore | None
    claims: list[Claim] = field(default_factory=list)

    @property
    def claimed_by(self) -> list[str]:
        return sorted({f"{c.deployment}[{c.source}]" for c in self.claims})


def canonical_url(url: str) -> str:
    """Canonical form of a backend URL, for use inside a :class:`StoreKey`.

    Loopback spellings are folded together (``localhost`` / ``127.0.0.1`` /
    ``::1``) and the default port is made explicit. Without this, one instance
    written two ways in two config files reads as two instances, and every store
    on it reports unclaimed — the single most dangerous failure mode this report
    has, since its output is an input to deletion.
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        host = "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{host}:{port}"


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------


@contextmanager
def _patched_settings(s: Any) -> Iterator[Any]:
    """Point ``ragstack.api.deps``' module-global settings at ``s``.

    Deliberate coupling: the settings-derived default store name and its ES
    counterpart are non-trivial (an explicit pin overrides the derivation, and
    the ES leg follows the pin unless overridden), and a second copy of that
    logic here would drift into reporting production as unclaimed. Calling the
    serving code is what makes the report correct by construction.
    """
    from ragstack.api import deps

    original = deps.settings
    deps.settings = s
    try:
        yield deps
    finally:
        deps.settings = original


def default_store_names(s: Any) -> tuple[str, str, str]:
    """``(qdrant_url_for_default, vector_name, text_name)`` for a settings object."""
    with _patched_settings(s) as deps:
        vector = deps._derived_collection_name()
        return canonical_url(deps._qdrant_url_for(vector)), vector, deps._es_index_name()


def _registry_path(s: Any) -> str:
    """The on-disk registry this deployment reads, or "" for a non-file backend."""
    backend = (getattr(s, "collection_store_backend", "json") or "json").lower()
    if backend == "sqlite":
        return getattr(s, "collection_store_path", "") or ""
    if backend == "json" and not getattr(s, "collections_json", ""):
        return getattr(s, "collections_file", "") or ""
    return ""


def _registry_specs(s: Any) -> list[Any]:
    """Every spec in this deployment's configured collection store.

    Reads the durable backend the deployment actually uses (json file, sqlite or
    postgres), not the running API — a stopped deployment still owns its rows.

    A configured-but-absent registry file is raised rather than read as empty.
    The stores would still be there; only our knowledge of who owns them would
    be missing, and an empty registry is the one answer that turns every store
    on the instance into a delete candidate.
    """
    import asyncio

    from ragstack.collection_store import make_collection_store

    path = _registry_path(s)
    if path and not Path(path).exists():
        raise FileNotFoundError(path)

    store = make_collection_store(s)

    async def _run() -> list[Any]:
        try:
            return await store.list_specs()
        finally:
            await store.close()

    return asyncio.run(_run())


def claims_for(name: str, config_path: str, values: dict[str, str]) -> Deployment:
    """Everything one configuration claims, on both legs."""
    dep = Deployment(name=name, config_path=str(config_path))
    try:
        s, unknown = settings_from_env(values)
    except Exception as e:  # noqa: BLE001 — a broken config must not hide the rest
        dep.errors.append(f"config unusable: {type(e).__name__}: {e}")
        return dep
    dep.unknown_keys = unknown
    dep.qdrant_api_key = s.qdrant_api_key or ""
    dep.es_api_key = getattr(s, "elasticsearch_api_key", "") or ""
    dep.es_url = canonical_url(s.elasticsearch_url)

    try:
        qdrant_url, vector, text = default_store_names(s)
    except Exception as e:  # noqa: BLE001
        dep.errors.append(f"default store name: {type(e).__name__}: {e}")
        qdrant_url, vector, text = canonical_url(s.qdrant_url), "", ""
    dep.qdrant_url = qdrant_url or canonical_url(s.qdrant_url)

    if vector:
        dep.claims.append(
            Claim(StoreKey(dep.qdrant_url, vector), VECTOR, name, "settings-default")
        )
    if text and dep.es_url:
        dep.claims.append(
            Claim(StoreKey(dep.es_url, text), TEXT, name, "settings-default")
        )

    try:
        specs = _registry_specs(s)
    except Exception as e:  # noqa: BLE001
        dep.errors.append(f"registry unreadable: {type(e).__name__}: {e}")
        return dep

    for spec in specs:
        with _patched_settings(s) as deps:
            vec_url = canonical_url(deps._qdrant_url_for(spec.collection))
        dep.claims.append(
            Claim(StoreKey(vec_url, spec.collection), VECTOR, name, f"registry:{spec.id}")
        )
        if dep.es_url:
            dep.claims.append(
                Claim(StoreKey(dep.es_url, spec.es_index()), TEXT, name, f"registry:{spec.id}")
            )
    return dep


def claims_from_registry_file(name: str, path: str | os.PathLike[str]) -> Deployment:
    """A bare ``*.collections.json`` with no env beside it.

    Its backend is genuinely unknown, so its claims are recorded with an empty
    backend and match a store on *any* instance by name. That is weaker evidence
    and the reconciler grades it as such (:data:`CLAIMED_NAME_ONLY`) rather than
    either dropping it — which would report a live corpus as unclaimed — or
    silently accepting it as exact.
    """
    from ragstack.collection_store import parse_specs

    dep = Deployment(name=name, config_path=str(path))
    try:
        specs = parse_specs(Path(path).read_text())
    except Exception as e:  # noqa: BLE001
        dep.errors.append(f"registry unreadable: {type(e).__name__}: {e}")
        return dep
    for spec in specs:
        dep.claims.append(
            Claim(StoreKey("", spec.collection), VECTOR, name, f"registry:{spec.id}")
        )
        dep.claims.append(
            Claim(StoreKey("", spec.es_index()), TEXT, name, f"registry:{spec.id}")
        )
    return dep


def discover(
    *,
    config_dirs: Iterable[str | os.PathLike[str]] = (),
    tenant_dirs: Iterable[str | os.PathLike[str]] = (),
    env_files: Iterable[tuple[str, str]] = (),
    exclude: Iterable[str] = (),
) -> list[Deployment]:
    """Find every deployment whose registry should be reconciled.

    ``config_dirs`` holds the flat legacy layout (``<name>.env`` beside
    ``<name>.collections.json``); ``tenant_dirs`` holds the per-tenant layout
    (``<tenant>/config/tenant.env``); ``env_files`` is explicit ``(name, path)``
    for anything else. Collections files in ``config_dirs`` with no matching env
    are still picked up, as backend-unknown registries.

    ``exclude`` drops config files by stem — for a shared layout env that sits in
    the same directory but configures no API. Excluding is deliberately manual:
    a heuristic that guessed wrong would drop a real registry, and dropping a
    registry is how a live corpus comes to be reported as unclaimed.
    """
    skip = set(exclude)
    deployments: list[Deployment] = []
    seen_registries: set[str] = set()
    used_names: set[str] = set()

    def _unique(name: str, path: Path) -> str:
        """Two configs can legitimately carry one deployment's name — a tenant
        dir and the legacy env it was migrated from both being 'lucid'. The name
        appears next to every claim in the report, so make it distinguishing."""
        if name not in used_names:
            used_names.add(name)
            return name
        qualified = f"{name} ({'/'.join(path.parts[-3:-1])})"
        used_names.add(qualified)
        return qualified

    def _add_env(name: str, path: Path) -> None:
        if name in skip:
            log.info("skipping excluded config %s", path)
            return
        values = parse_env_file(path)
        dep = claims_for(_unique(name, path), str(path), values)
        for key in ("COLLECTIONS_FILE", "COLLECTION_STORE_PATH"):
            if values.get(key):
                seen_registries.add(str(Path(values[key]).resolve()))
        deployments.append(dep)

    for name, path in env_files:
        _add_env(name, Path(path))

    for d in tenant_dirs:
        for env in sorted(Path(d).glob("*/config/tenant.env")):
            _add_env(env.parent.parent.name, env)

    for d in config_dirs:
        base = Path(d)
        for env in sorted(base.glob("*.env")):
            _add_env(env.stem, env)
        for reg in sorted(base.glob("*.collections.json")):
            stem = reg.name.split(".")[0]
            if str(reg.resolve()) in seen_registries or stem in skip:
                continue
            deployments.append(
                claims_from_registry_file(f"{stem} (backend unknown)", reg)
            )
    return deployments


# ---------------------------------------------------------------------------
# probes — read-only HTTP, no writes of any kind
# ---------------------------------------------------------------------------


def probe_qdrant(
    url: str, *, api_key: str = "", client: httpx.Client | None = None,
    storage_path: str = "",
) -> list[PhysicalStore]:
    """List every collection on one Qdrant instance with its point count.

    Qdrant exposes no on-disk size through its API, so ``size_bytes`` is filled
    only when ``storage_path`` points at that instance's storage directory on
    this host. A number that cannot be measured is left ``None`` rather than
    estimated.
    """
    base = canonical_url(url)
    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    headers = {"api-key": api_key} if api_key else {}
    out: list[PhysicalStore] = []
    try:
        r = c.get(f"{url.rstrip('/')}/collections", headers=headers)
        r.raise_for_status()
        names = [x["name"] for x in r.json()["result"]["collections"]]
        for name in sorted(names):
            store = PhysicalStore(key=StoreKey(base, name), leg=VECTOR)
            try:
                d = c.get(f"{url.rstrip('/')}/collections/{name}", headers=headers)
                d.raise_for_status()
                res = d.json()["result"]
                store.count = res.get("points_count")
                store.status = str(res.get("status") or "")
            except Exception as e:  # noqa: BLE001 — one bad collection is not the fleet
                log.warning("qdrant %s: detail for %r failed: %s", base, name, e)
            if storage_path:
                store.size_bytes = _dir_size(Path(storage_path) / "collections" / name)
            out.append(store)
    finally:
        if owns:
            c.close()
    return out


def probe_elasticsearch(
    url: str, *, api_key: str = "",
    client: httpx.Client | None = None, include_system: bool = False,
) -> list[PhysicalStore]:
    """List every index on one ES cluster with doc count, size and creation date.

    ES *does* expose a trustworthy ``creation.date``, which is the one age signal
    on this host that is not a lie (Qdrant exposes none, and the directory
    birth-time proxy was reset for every store by the ``/rag`` layout migration).
    """
    base = canonical_url(url)
    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    out: list[PhysicalStore] = []
    try:
        r = c.get(
            f"{url.rstrip('/')}/_cat/indices",
            params={
                "format": "json",
                "bytes": "b",
                "h": "index,docs.count,store.size,creation.date.string,health",
            },
            headers={"Authorization": f"ApiKey {api_key}"} if api_key else {},
        )
        r.raise_for_status()
        for item in r.json():
            name = item.get("index", "")
            if not name or (name.startswith(_ES_SYSTEM_PREFIX) and not include_system):
                continue
            out.append(
                PhysicalStore(
                    key=StoreKey(base, name),
                    leg=TEXT,
                    count=_as_int(item.get("docs.count")),
                    size_bytes=_as_int(item.get("store.size")),
                    created_at=item.get("creation.date.string") or "",
                    status=item.get("health") or "",
                )
            )
    finally:
        if owns:
            c.close()
    return sorted(out, key=lambda s: s.key.name)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dir_size(path: Path) -> int | None:
    if not path.is_dir():
        return None
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------


def reconcile(stores: Iterable[PhysicalStore], deployments: Iterable[Deployment]) -> list[Row]:
    """Join probed stores against claims, in both directions.

    Both directions matter. A store no registry claims is the 403 GB question
    this report was written for; a registry entry with no store is the same
    invariant (ADR-0002 decision 5) breaking the other way, and it is free to
    detect once the data is in hand.
    """
    claims = [c for d in deployments for c in d.claims]
    exact: dict[tuple[str, str, str], list[Claim]] = {}
    by_name: dict[tuple[str, str], list[Claim]] = {}
    for c in claims:
        if c.key.backend:
            exact.setdefault((c.key.backend, c.key.name, c.leg), []).append(c)
        else:
            by_name.setdefault((c.key.name, c.leg), []).append(c)

    rows: list[Row] = []
    matched: set[tuple[str, str, str]] = set()
    for store in stores:
        ident = (store.key.backend, store.key.name, store.leg)
        hits = exact.get(ident, [])
        weak = by_name.get((store.key.name, store.leg), [])
        if hits:
            status = CLAIMED
        elif weak:
            status = CLAIMED_NAME_ONLY
        else:
            status = UNCLAIMED
        if hits:
            matched.add(ident)
        rows.append(Row(key=store.key, leg=store.leg, status=status,
                        store=store, claims=hits + weak))

    probed_backends = {s.key.backend for s in stores}
    present = {(s.key.backend, s.key.name, s.leg) for s in stores}
    for ident, cs in sorted(exact.items()):
        # Only assert absence for an instance we actually probed. A claim on an
        # unprobed backend is unknown, not missing — the distinction is the whole
        # reason this report refuses to conclude "orphan" from absence.
        if ident in present or ident[0] not in probed_backends:
            continue
        rows.append(Row(key=StoreKey(ident[0], ident[1]), leg=ident[2],
                        status=MISSING, store=None, claims=cs))

    order = {UNCLAIMED: 0, MISSING: 1, CLAIMED_NAME_ONLY: 2, CLAIMED: 3}
    rows.sort(key=lambda r: (order.get(r.status, 9), -(r.store.size_bytes or 0) if r.store else 0,
                             r.key.backend, r.leg, r.key.name))
    return rows


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

BANNER = (
    "unclaimed-by-known-registries means NO registry passed to this run claims "
    "the store. It does NOT mean orphan: a stopped deployment still owns its "
    "data, and a freshly provisioned tenant is empty by definition. Verify an "
    "owner exists before deleting anything."
)


def human_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= step
    return f"{value:.1f}TB"


def render_text(rows: list[Row], deployments: list[Deployment]) -> str:
    lines: list[str] = ["", "physical store inventory", "=" * 78, "", BANNER, ""]

    broken = [d for d in sorted(deployments, key=lambda x: x.name) if d.errors]
    if broken:
        lines += [
            f"!! {len(broken)} registr{'y' if len(broken) == 1 else 'ies'} could not be "
            "read. Every store they own is reported below as unclaimed. Fix these "
            "before acting on this report:",
        ]
        for d in broken:
            lines.append(f"   {d.name} ({d.config_path}): {'; '.join(d.errors)}")
        lines.append("")

    lines.append(f"registries reconciled against ({len(deployments)}):")
    for d in sorted(deployments, key=lambda x: x.name):
        legs = f"qdrant={d.qdrant_url or '?'} es={d.es_url or '?'}"
        lines.append(f"  {d.name:<28} {len(d.claims):>3} claims  {legs}  ({d.config_path})")
        for err in d.errors:
            lines.append(f"  {'':<28}  !! {err}")
        if d.unknown_keys:
            lines.append(f"  {'':<28}  ?? unrecognised keys: {', '.join(d.unknown_keys)}")
    lines.append("")

    header = f"{'status':<28} {'leg':<7} {'count':>12} {'size':>10}  store"
    lines += [header, "-" * len(header)]
    for r in rows:
        count = "-" if r.store is None or r.store.count is None else f"{r.store.count:,}"
        size = human_bytes(r.store.size_bytes if r.store else None)
        lines.append(f"{r.status:<28} {r.leg:<7} {count:>12} {size:>10}  {r.key}")
        if r.claims:
            lines.append(f"{'':<28} {'':<7} {'':>12} {'':>10}    claimed by: "
                         f"{', '.join(r.claimed_by)}")

    counts: dict[str, int] = {}
    unclaimed_bytes = 0
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status == UNCLAIMED and r.store and r.store.size_bytes:
            unclaimed_bytes += r.store.size_bytes
    lines += ["", "summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    if unclaimed_bytes:
        lines.append(f"unclaimed measured size: {human_bytes(unclaimed_bytes)}")
    lines.append("")
    return "\n".join(lines)


def to_dict(rows: list[Row], deployments: list[Deployment]) -> dict[str, Any]:
    """JSON-serialisable report. Carries no credentials — the probe auth read
    from the config files stays on the :class:`Deployment` objects and is never
    serialised here."""
    return {
        "schema": "ragstack.store_inventory/1",
        "note": BANNER,
        # Hoisted out of `deployments` so a consumer cannot act on the store list
        # without first seeing that part of the reconciliation input was missing.
        "unreadable_registries": [d.name for d in deployments if d.errors],
        "deployments": [
            {
                "name": d.name,
                "config_path": d.config_path,
                "qdrant_url": d.qdrant_url,
                "es_url": d.es_url,
                "claims": len(d.claims),
                "errors": d.errors,
                "unknown_keys": d.unknown_keys,
            }
            for d in sorted(deployments, key=lambda x: x.name)
        ],
        "stores": [
            {
                "backend": r.key.backend,
                "name": r.key.name,
                "leg": r.leg,
                "status": r.status,
                "count": r.store.count if r.store else None,
                "size_bytes": r.store.size_bytes if r.store else None,
                "created_at": r.store.created_at if r.store else "",
                "health": r.store.status if r.store else "",
                "claimed_by": r.claimed_by,
            }
            for r in rows
        ],
    }


def collect(
    deployments: list[Deployment],
    *,
    qdrant_storage: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> list[PhysicalStore]:
    """Probe every distinct backend named by ``deployments``, once each.

    Credentials are taken from whichever deployment supplies them for that
    backend — a read-only account configured on one tenant is enough to inventory
    the instance it shares.
    """
    storage = qdrant_storage or {}
    q_keys: dict[str, str] = {}
    es_keys: dict[str, str] = {}
    for d in deployments:
        if d.qdrant_url:
            q_keys.setdefault(d.qdrant_url, "")
            if d.qdrant_api_key:
                q_keys[d.qdrant_url] = d.qdrant_api_key
        if d.es_url:
            es_keys.setdefault(d.es_url, "")
            if d.es_api_key:
                es_keys[d.es_url] = d.es_api_key

    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    stores: list[PhysicalStore] = []
    try:
        for url, key in sorted(q_keys.items()):
            try:
                stores += probe_qdrant(url, api_key=key, client=c,
                                       storage_path=storage.get(url, ""))
            except Exception as e:  # noqa: BLE001 — an unreachable instance is reported, not fatal
                log.warning("qdrant %s unreachable: %s", url, e)
        for url, key in sorted(es_keys.items()):
            try:
                stores += probe_elasticsearch(url, api_key=key, client=c)
            except Exception as e:  # noqa: BLE001
                log.warning("elasticsearch %s unreachable: %s", url, e)
    finally:
        if owns:
            c.close()
    return stores


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="store_inventory",
        description="Report-only inventory of physical stores vs. the registries "
                    "that claim them. Never deletes anything.",
    )
    p.add_argument("--config-dir", action="append", default=[],
                   help="flat layout: <name>.env beside <name>.collections.json "
                        "(e.g. /rag/config). Repeatable.")
    p.add_argument("--tenants-dir", action="append", default=[],
                   help="per-tenant layout: <tenant>/config/tenant.env "
                        "(e.g. /rag/data/tenants). Repeatable.")
    p.add_argument("--env", action="append", default=[], metavar="NAME=PATH",
                   help="an explicit config file. Repeatable.")
    p.add_argument("--qdrant-storage", action="append", default=[], metavar="URL=PATH",
                   help="storage dir of a Qdrant instance on this host, to measure "
                        "on-disk size (Qdrant's API does not expose it). Repeatable.")
    p.add_argument("--exclude", action="append", default=[], metavar="NAME",
                   help="drop a config by stem (e.g. a shared layout env that "
                        "configures no API). Repeatable.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    env_files = []
    for item in args.env:
        name, _, path = item.partition("=")
        if not path:
            p.error(f"--env expects NAME=PATH, got {item!r}")
        env_files.append((name, path))

    storage = {}
    for item in args.qdrant_storage:
        url, _, path = item.partition("=")
        if not path:
            p.error(f"--qdrant-storage expects URL=PATH, got {item!r}")
        storage[canonical_url(url)] = path

    if not (args.config_dir or args.tenants_dir or env_files):
        p.error("nothing to reconcile against: pass at least one of "
                "--config-dir / --tenants-dir / --env. Reconciling against a "
                "subset of registries reports the rest as unclaimed.")

    deployments = discover(config_dirs=args.config_dir, tenant_dirs=args.tenants_dir,
                           env_files=env_files, exclude=args.exclude)
    stores = collect(deployments, qdrant_storage=storage)
    rows = reconcile(stores, deployments)

    if args.json:
        print(json.dumps(to_dict(rows, deployments), indent=2))
    else:
        print(render_text(rows, deployments))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
