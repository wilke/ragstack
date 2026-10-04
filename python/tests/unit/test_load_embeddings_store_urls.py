"""``load_embeddings.py`` has no default store URLs (#636, the #454 rule).

On the deployment host ``localhost:6333`` / ``:9200`` are the PRODUCTION stores,
and ``load_embeddings.py`` is a write path (``restore-collection.cwl`` runs it).
Until #636 both flags defaulted to exactly those, so a forgotten flag wrote into
production. Now, per leg, the URL comes from a registry ROUTE (wins), else the
explicit flag, else the settings' URL ONLY IF that setting was explicitly
configured — never the code default. Otherwise: exit 2, naming the flag.

No test here opens a connection. ``amain`` — the first thing that would build a
store — is replaced by a spy, so a regression that lets a run through is caught
as "the spy was reached" rather than by reaching whatever ``localhost`` is. Every
URL that is supplied is the dead port ``127.0.0.1:1``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.collection_store import CollectionSpec
from ragstack.ops import ingest_target as it

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import load_embeddings as load_cli  # noqa: E402

yaml = pytest.importorskip("yaml")

DEAD = "http://127.0.0.1:1"
#: What the code defaults are. A settings double holds these too, so a
#: regression that falls back to them is visible in the target the spy records.
LOCAL_Q, LOCAL_ES = "http://localhost:6333", "http://localhost:9200"


def _spec(cid="corpus", collection="store_a", **over) -> CollectionSpec:
    body = {"id": cid, "collection": collection, "embedding_model": "m",
            "embedding_model_dim": 4, "chunk_method": "fixed_token",
            "chunk_size": 256, "chunk_overlap": 32}
    body.update(over)
    return CollectionSpec(**body)


def _settings(explicit=(), **over):
    """A settings double. ``explicit`` is what pydantic would record in
    ``model_fields_set`` — the fields set by env/.env rather than defaulted."""
    base = {
        "qdrant_url": LOCAL_Q, "qdrant_collection_routes": {},
        "elasticsearch_url": LOCAL_ES, "es_collection_routes": {},
        "collection_store_backend": "json", "collection_store_path": "",
        "collections_file": "", "collections_json": "",
        "model_fields_set": set(explicit),
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Drive ``main()`` with a stubbed registry and a spy in place of ``amain``.

    Returns ``(rc_or_SystemExit_code, reached_target_or_None)``."""
    monkeypatch.delenv("RAGSTACK_COLLECTION_ID", raising=False)
    reached: list = []

    async def spy(args, target=None):
        reached.append(target)
        return 0

    monkeypatch.setattr(load_cli, "amain", spy)

    def _run(argv, *, settings, specs=None):
        monkeypatch.setattr(it, "load_specs",
                            lambda s=None: list(specs if specs is not None else [_spec()]))
        try:
            rc = load_cli.main(
                [str(tmp_path / "e.emb.jsonl"), "--out", str(tmp_path / "s.json"), *argv],
                settings=settings)
        except SystemExit as e:
            rc = e.code
        return rc, (reached[0] if reached else None)

    return _run


# --------------------------------------------------------------------------- #
# no URL -> exit 2, naming the flag
# --------------------------------------------------------------------------- #


def test_no_urls_and_nothing_configured_exits_2_naming_both_flags(run, capsys):
    rc, reached = run(["--collection-id", "corpus"], settings=_settings())
    assert rc == 2
    assert reached is None, "the load ran with no store URL — it would have hit localhost"
    err = capsys.readouterr().err
    assert "--qdrant-url is required" in err
    assert "--es-url is required" in err
    assert "PRODUCTION" in err


@pytest.mark.parametrize("given,missing", [
    (["--qdrant-url", DEAD], "--es-url"),
    (["--es-url", DEAD], "--qdrant-url"),
])
def test_one_url_missing_names_just_that_flag(run, capsys, given, missing):
    rc, reached = run(["--collection-id", "corpus", *given], settings=_settings())
    assert rc == 2 and reached is None
    err = capsys.readouterr().err
    assert f"{missing} is required" in err
    other = "--qdrant-url" if missing == "--es-url" else "--es-url"
    assert f"{other} is required" not in err


def test_the_parser_itself_has_no_localhost_default():
    """The argparse default WAS the mechanism (#454's lesson); pin its absence."""
    args = load_cli.parse_args(["e.emb.jsonl"])
    assert args.qdrant_url == "" and args.es_url == ""


# --------------------------------------------------------------------------- #
# the three ways a URL may legitimately be decided
# --------------------------------------------------------------------------- #


def test_explicit_flags_are_used(run):
    rc, target = run(["--collection-id", "corpus", "--qdrant-url", DEAD, "--es-url", DEAD],
                     settings=_settings())
    assert rc == 0
    assert (target.qdrant_url, target.es_url) == (DEAD, DEAD)


def test_a_routed_collection_needs_no_flag(run):
    """Both legs routed: the route is where the store lives, and wins anyway."""
    s = _settings(qdrant_collection_routes={"store_a": DEAD},
                  es_collection_routes={"store_a": DEAD})
    rc, target = run(["--collection-id", "corpus"], settings=s)
    assert rc == 0
    assert (target.qdrant_url, target.es_url) == (DEAD, DEAD)


def test_a_route_still_beats_the_flag(run):
    s = _settings(qdrant_collection_routes={"store_a": DEAD})
    rc, target = run(["--collection-id", "corpus", "--qdrant-url", "http://127.0.0.1:2",
                      "--es-url", DEAD], settings=s)
    assert rc == 0 and target.qdrant_url == DEAD


def test_routing_only_one_leg_still_requires_the_other_flag(run, capsys):
    s = _settings(qdrant_collection_routes={"store_a": DEAD})
    rc, reached = run(["--collection-id", "corpus"], settings=s)
    assert rc == 2 and reached is None
    err = capsys.readouterr().err
    assert "--es-url is required" in err and "--qdrant-url is required" not in err


def test_an_explicitly_configured_setting_is_honoured(run):
    s = _settings(explicit={"qdrant_url", "elasticsearch_url"},
                  qdrant_url=DEAD, elasticsearch_url=DEAD)
    rc, target = run(["--collection-id", "corpus"], settings=s)
    assert rc == 0
    assert (target.qdrant_url, target.es_url) == (DEAD, DEAD)


def test_a_settings_object_that_records_nothing_counts_as_not_configured(run, capsys):
    """No ``model_fields_set`` at all (an older double) is the unsafe unknown:
    treat it as the code default and refuse."""
    s = _settings(qdrant_url=DEAD, elasticsearch_url=DEAD)
    del s.model_fields_set
    rc, reached = run(["--collection-id", "corpus"], settings=s)
    assert rc == 2 and reached is None


def test_configured_explicitly_tells_env_from_code_default(monkeypatch):
    """The real ``Settings``: the env var is what makes the URL explicit."""
    from ragstack.config import Settings

    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.qdrant_url == LOCAL_Q  # the code default is still localhost…
    assert not it.configured_explicitly(s, "qdrant_url")  # …and NOT honoured
    assert not it.configured_explicitly(s, "elasticsearch_url")

    monkeypatch.setenv("QDRANT_URL", DEAD)
    s = Settings(_env_file=None)
    assert it.configured_explicitly(s, "qdrant_url")
    assert not it.configured_explicitly(s, "elasticsearch_url")
    # and a named registry's view answers for the settings behind it (#563)
    view = it._RegistryView(s, "x", {})
    assert it.configured_explicitly(view, "qdrant_url")


def test_an_env_configured_url_set_to_the_default_value_is_still_explicit(monkeypatch):
    """Explicit means SET, not "different from the default": an operator who
    writes ``QDRANT_URL=http://localhost:6333`` has decided, and is honoured."""
    from ragstack.config import Settings

    monkeypatch.setenv("QDRANT_URL", LOCAL_Q)
    assert it.configured_explicitly(Settings(_env_file=None), "qdrant_url")


# --------------------------------------------------------------------------- #
# the in-memory backend: no URL, but a collection
# --------------------------------------------------------------------------- #

_MEM = ["--vector-backend", "memory", "--text-backend", "memory"]


def test_memory_backend_needs_no_url(run):
    rc, target = run([*_MEM, "--collection-id", "corpus"], settings=_settings())
    assert rc == 0
    assert target is None  # nothing resolved, nothing to connect to


def test_memory_backend_accepts_the_physical_name_too(run):
    rc, _ = run([*_MEM, "--collection", "store_a"], settings=_settings())
    assert rc == 0


def test_memory_backend_without_a_collection_exits_2(run, capsys):
    rc, reached = run(_MEM, settings=_settings())
    assert rc == 2 and reached is None
    assert "--collection-id is required" in capsys.readouterr().err


def test_qdrant_backend_without_a_collection_exits_2(run, capsys):
    """The pre-existing refusal (#263), pinned alongside the memory one so "no
    collection fails on every path" is one claim with two halves."""
    rc, reached = run(["--qdrant-url", DEAD, "--es-url", DEAD], settings=_settings())
    assert rc == 2 and reached is None
    assert "--collection-id is required" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the two workflows that run this tool still hand it both URLs
# --------------------------------------------------------------------------- #

_CWL = ROOT.parent / "cwl"


def _load_tool(cwl_name: str) -> dict:
    doc = yaml.safe_load((_CWL / cwl_name).read_text(encoding="utf-8"))
    tools = [
        step["run"] for step in (doc.get("steps") or {}).values()
        if "load_embeddings.py" in " ".join(map(str, step["run"].get("baseCommand", [])))
    ]
    assert len(tools) == 1, f"{cwl_name}: expected one load_embeddings.py step"
    return tools[0]


def _command_line(tool: dict, values: dict) -> list[str]:
    """The argv cwltool would build from ``tool``'s bindings, for ``values``
    (absent = a ``null`` optional input, which binds nothing)."""
    parts: list[tuple[int, list[str]]] = []
    for name, spec in (tool.get("inputs") or {}).items():
        b = spec.get("inputBinding")
        if b is None or name not in values:
            continue
        v, prefix = values[name], b.get("prefix")
        if isinstance(v, bool):
            toks = [prefix] if v else []
        elif isinstance(v, list):
            toks = ([prefix] if prefix else []) + list(v)
        else:
            toks = ([prefix] if prefix else []) + [str(v)]
        parts.append((int(b.get("position", 0)), toks))
    for a in tool.get("arguments") or []:
        toks = [a["prefix"]] if a.get("prefix") else []
        if "valueFrom" in a:
            toks.append(str(a["valueFrom"]))
        parts.append((int(a.get("position", 0)), toks))
    return [t for _, toks in sorted(parts, key=lambda p: p[0]) for t in toks]


_CWL_VALUES = {
    "load-embeddings.cwl": {
        "embeddings": ["a.emb.jsonl"], "collection": "store_a", "collection_id": "corpus",
        "qdrant_url": DEAD, "es_url": DEAD, "fail_on_error": True, "backpressure": False,
    },
    "restore-collection.cwl": {
        "versions": ["v1"], "collection_id": "corpus", "spec_hash": "h",
        "qdrant_url": DEAD, "es_url": DEAD, "backpressure": False, "bulk_refresh": True,
    },
}


@pytest.mark.parametrize("cwl_name", sorted(_CWL_VALUES))
def test_the_workflow_command_line_passes_both_urls_and_is_accepted(
        cwl_name, monkeypatch, tmp_path):
    """Each workflow still parses, binds both URLs, and the command it builds
    clears the new guard with settings that configure NOTHING — i.e. on the
    strength of its own flags alone, which is the point."""
    tool = _load_tool(cwl_name)
    for key, flag in (("qdrant_url", "--qdrant-url"), ("es_url", "--es-url")):
        assert tool["inputs"][key]["type"] == "string", f"{cwl_name}: {key} not required"
        assert tool["inputs"][key]["inputBinding"]["prefix"] == flag

    argv = _command_line(tool, _CWL_VALUES[cwl_name])
    args = load_cli.parse_args(argv)
    assert (args.qdrant_url, args.es_url) == (DEAD, DEAD)

    monkeypatch.delenv("RAGSTACK_COLLECTION_ID", raising=False)
    monkeypatch.setattr(it, "load_specs", lambda s=None: [_spec()])
    reached: list = []

    async def spy(a, target=None):
        reached.append(target)
        return 0

    monkeypatch.setattr(load_cli, "amain", spy)
    assert load_cli.main(argv, settings=_settings()) == 0
    assert (reached[0].qdrant_url, reached[0].es_url) == (DEAD, DEAD)

    # …and with the URL inputs dropped, the same command is refused.
    without = {k: v for k, v in _CWL_VALUES[cwl_name].items()
               if k not in ("qdrant_url", "es_url")}
    with pytest.raises(SystemExit) as e:
        load_cli.main(_command_line(tool, without), settings=_settings())
    assert e.value.code == 2
