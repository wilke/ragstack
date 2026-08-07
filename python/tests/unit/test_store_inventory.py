"""Tests for the report-only store inventory (#293).

The failure this tool must never have is a false ``unclaimed``: its output is an
input to an operator's delete. Most of what follows tests exactly that direction
— loopback spellings, name collisions across instances, the settings-derived
default that has no registry row, and absence on a backend that was never probed.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from ragstack.ops import store_inventory as si

# --------------------------------------------------------------------------
# canonical_url — one instance written two ways must be ONE instance
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("http://localhost:6333", "http://127.0.0.1:6333"),
        ("http://localhost:6333/", "http://localhost:6333"),
        ("http://LOCALHOST:6333", "http://localhost:6333"),
        ("localhost:6333", "http://localhost:6333"),
        ("http://localhost", "http://localhost:80"),
        ("https://es.example.org", "https://es.example.org:443"),
    ],
)
def test_canonical_url_folds_equivalent_spellings(a, b):
    assert si.canonical_url(a) == si.canonical_url(b)


def test_canonical_url_keeps_distinct_instances_distinct():
    assert si.canonical_url("http://localhost:6333") != si.canonical_url("http://localhost:6343")
    assert si.canonical_url("http://a:6333") != si.canonical_url("http://b:6333")


def test_canonical_url_empty():
    assert si.canonical_url("") == ""
    assert si.canonical_url("   ") == ""


# --------------------------------------------------------------------------
# env parsing — must agree with the shell that actually sources these files
# --------------------------------------------------------------------------


def test_parse_env_file_shapes(tmp_path):
    p = tmp_path / "tenant.env"
    p.write_text(
        "# a comment\n"
        "\n"
        "QDRANT_URL=http://localhost:6333\n"
        "export ELASTICSEARCH_URL=http://localhost:9200\n"
        'EMBEDDING_MODEL="Salesforce/SFR-Embedding-Mistral"\n'
        "API_KEYS='[\"a\",\"b\"]'\n"
        "EMPTY=\n"
        "not a key line\n"
    )
    values = si.parse_env_file(p)
    assert values["QDRANT_URL"] == "http://localhost:6333"
    assert values["ELASTICSEARCH_URL"] == "http://localhost:9200"
    assert values["EMBEDDING_MODEL"] == "Salesforce/SFR-Embedding-Mistral"
    # the lucid incident: single-quoted JSON keeps its inner double quotes
    assert json.loads(values["API_KEYS"]) == ["a", "b"]
    assert values["EMPTY"] == ""
    assert "not a key line" not in values


def test_settings_ignore_ambient_environment(tmp_path, monkeypatch):
    """The operator's own shell must not describe somebody else's deployment."""
    monkeypatch.setenv("QDRANT_URL", "http://leaked:9999")
    monkeypatch.setenv("ELASTICSEARCH_INDEX", "leaked-index")
    s, _ = si.settings_from_env({"QDRANT_URL": "http://real:6333"})
    assert s.qdrant_url == "http://real:6333"
    assert s.elasticsearch_index == "ragstack"  # the field default, not the leak


def test_settings_report_unknown_keys():
    _, unknown = si.settings_from_env({"QDRANT_URL": "http://x:6333", "QRDANT_URL": "typo"})
    assert unknown == ["QRDANT_URL"]


def test_settings_from_env_restores_environment(monkeypatch):
    monkeypatch.setenv("SENTINEL_VAR", "kept")
    si.settings_from_env({"QDRANT_URL": "http://x:6333"})
    assert os.environ["SENTINEL_VAR"] == "kept"


# --------------------------------------------------------------------------
# claims — the settings-derived default has no registry row and must still count
# --------------------------------------------------------------------------


def _write_registry(tmp_path, specs):
    p = tmp_path / "collections.json"
    p.write_text(json.dumps(specs))
    return p


def test_pinned_default_is_claimed_without_any_registry_row(tmp_path):
    """Lucid production's shape: no collections file, one pinned corpus.

    Reading only registry files would report the largest production stores on
    this host as unclaimed.
    """
    dep = si.claims_for("lucid", "x.env", {
        "QDRANT_URL": "http://localhost:6343",
        "QDRANT_COLLECTION_EXPLICIT": "lucid_sfr_tok256",
        "ELASTICSEARCH_URL": "http://localhost:9200",
    })
    assert dep.errors == []
    vector = [c for c in dep.claims if c.leg == si.VECTOR]
    text = [c for c in dep.claims if c.leg == si.TEXT]
    assert [c.key.name for c in vector] == ["lucid_sfr_tok256"]
    assert vector[0].key.backend == si.canonical_url("http://localhost:6343")
    # the BM25 leg follows the pin, or hybrid retrieval would fuse two corpora
    assert [c.key.name for c in text] == ["lucid_sfr_tok256"]
    assert all(c.source == "settings-default" for c in dep.claims)


def test_registry_specs_are_claimed_on_both_legs(tmp_path):
    reg = _write_registry(tmp_path, [
        {"id": "a", "collection": "store_a", "embedding_model_dim": 4},
        {"id": "b", "collection": "store_b", "text_index": "text_b", "embedding_model_dim": 4},
    ])
    dep = si.claims_for("dev", "x.env", {
        "QDRANT_URL": "http://localhost:6333",
        "ELASTICSEARCH_URL": "http://localhost:9200",
        "COLLECTIONS_FILE": str(reg),
    })
    assert dep.errors == []
    got = {(c.leg, c.key.name) for c in dep.claims if c.source.startswith("registry:")}
    assert got == {
        (si.VECTOR, "store_a"), (si.TEXT, "store_a"),
        (si.VECTOR, "store_b"), (si.TEXT, "text_b"),  # legs may differ in name
    }


def test_routed_collection_is_claimed_on_its_own_instance(tmp_path):
    """A routed collection lives on a different Qdrant than qdrant_url."""
    reg = _write_registry(tmp_path, [
        {"id": "big", "collection": "semantic", "embedding_model_dim": 4},
    ])
    dep = si.claims_for("routed", "x.env", {
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_COLLECTION_ROUTES": '{"semantic": "http://localhost:6343"}',
        "ELASTICSEARCH_URL": "http://localhost:9200",
        "COLLECTIONS_FILE": str(reg),
    })
    assert dep.errors == []
    claim = next(c for c in dep.claims if c.key.name == "semantic" and c.leg == si.VECTOR)
    assert claim.key.backend == si.canonical_url("http://localhost:6343")


def test_broken_registry_is_reported_not_swallowed():
    dep = si.claims_for("bad", "x.env", {
        "QDRANT_URL": "http://localhost:6333",
        "COLLECTIONS_FILE": "/nonexistent/collections.json",
    })
    # the settings-derived claim still stands; only the registry read failed
    assert dep.claims
    assert any("registry unreadable" in e for e in dep.errors)


def test_unsourceable_config_is_an_error_not_silence(tmp_path):
    """The asm/lucid failure: bash strips the inner quotes from an unquoted JSON
    value, the config becomes unparseable, and the deployment contributes zero
    claims. Silent zero claims turns every store it owns into a delete
    candidate, so this must surface as an error on the deployment."""
    env = tmp_path / "tenant.env"
    env.write_text('API_KEYS=["a","b"]\nQDRANT_URL=http://localhost:6333\n')
    dep = si.claims_for("asm", str(env), si.parse_env_file(env))
    assert dep.claims == []
    assert any("config unusable" in e for e in dep.errors)
    text = si.render_text([], [dep])
    assert "config unusable" in text
    # and loudly, above the store list — not only in the per-registry detail
    assert "could not be read" in text
    assert si.to_dict([], [dep])["unreadable_registries"] == ["asm"]


def test_missing_registry_file_is_an_error_not_an_empty_registry(tmp_path):
    dep = si.claims_for("t", "x.env", {
        "QDRANT_URL": "http://localhost:6333",
        "COLLECTION_STORE_BACKEND": "sqlite",
        "COLLECTION_STORE_PATH": str(tmp_path / "gone.db"),
    })
    assert any("registry unreadable" in e for e in dep.errors)


def test_discover_finds_both_layouts_and_honours_exclude(tmp_path):
    conf = tmp_path / "config"
    conf.mkdir()
    (conf / "unified.env").write_text("QDRANT_URL=http://localhost:6333\n")
    (conf / "rag.env").write_text("QDRANT_URL=http://localhost:6333\n")
    (conf / "demo.collections.json").write_text(
        json.dumps([{"id": "a", "collection": "store_a", "embedding_model_dim": 4}]))
    tenants = tmp_path / "tenants"
    (tenants / "dev" / "config").mkdir(parents=True)
    (tenants / "dev" / "config" / "tenant.env").write_text("QDRANT_URL=http://localhost:24041\n")

    names = {d.name for d in si.discover(config_dirs=[conf], tenant_dirs=[tenants],
                                         exclude=["rag"])}
    assert names == {"unified", "dev", "demo (backend unknown)"}


def test_discover_disambiguates_two_configs_with_one_name(tmp_path):
    """A tenant dir and the legacy env it was migrated from are both 'lucid'."""
    conf = tmp_path / "config"
    conf.mkdir()
    (conf / "lucid.env").write_text("QDRANT_URL=http://localhost:6343\n")
    tenants = tmp_path / "tenants"
    (tenants / "lucid" / "config").mkdir(parents=True)
    (tenants / "lucid" / "config" / "tenant.env").write_text(
        "QDRANT_URL=http://localhost:6343\n")
    names = [d.name for d in si.discover(config_dirs=[conf], tenant_dirs=[tenants])]
    assert len(set(names)) == 2, names


def test_launcher_only_keys_are_not_reported_as_typos():
    _, unknown = si.settings_from_env({"QDRANT_URL": "http://x:6333", "PORT": "24040",
                                       "HF_HOME": "/rag/cache"})
    assert unknown == []


def test_discover_links_a_collections_file_to_its_env(tmp_path):
    """A registry named by an env must not be counted twice — once with the
    env's backend and once as backend-unknown."""
    conf = tmp_path / "config"
    conf.mkdir()
    reg = conf / "unified.collections.json"
    reg.write_text(json.dumps([{"id": "a", "collection": "store_a", "embedding_model_dim": 4}]))
    (conf / "unified.env").write_text(
        f"QDRANT_URL=http://localhost:6333\nCOLLECTIONS_FILE={reg}\n")
    deps = si.discover(config_dirs=[conf])
    assert [d.name for d in deps] == ["unified"]
    assert all(c.key.backend for c in deps[0].claims)


def test_backend_unknown_registry_file(tmp_path):
    reg = _write_registry(tmp_path, [
        {"id": "a", "collection": "store_a", "embedding_model_dim": 4},
    ])
    dep = si.claims_from_registry_file("demo (backend unknown)", reg)
    assert dep.errors == []
    assert all(c.key.backend == "" for c in dep.claims)


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------


def _store(backend, name, leg=si.VECTOR, count=1, size=None):
    return si.PhysicalStore(key=si.StoreKey(si.canonical_url(backend), name),
                            leg=leg, count=count, size_bytes=size)


def _dep(name, claims):
    return si.Deployment(name=name, config_path="x", claims=claims)


def _claim(backend, name, leg=si.VECTOR, dep="d", source="registry:a"):
    return si.Claim(si.StoreKey(si.canonical_url(backend), name), leg, dep, source)


def test_claimed_and_unclaimed():
    stores = [_store("http://localhost:6333", "kept"), _store("http://localhost:6333", "loose")]
    deps = [_dep("d", [_claim("http://localhost:6333", "kept")])]
    rows = {r.key.name: r for r in si.reconcile(stores, deps)}
    assert rows["kept"].status == si.CLAIMED
    assert rows["loose"].status == si.UNCLAIMED
    assert rows["kept"].claimed_by == ["d[registry:a]"]


def test_claim_written_with_a_different_loopback_spelling_still_matches():
    """The regression that would report every store on an instance as unclaimed."""
    stores = [_store("http://127.0.0.1:6333", "kept")]
    deps = [_dep("d", [_claim("http://localhost:6333", "kept")])]
    assert si.reconcile(stores, deps)[0].status == si.CLAIMED


def test_name_collision_across_instances_is_not_a_claim():
    """Three of these are live right now, and one of each pair is production."""
    stores = [_store("http://localhost:6343", "ragstack_sfr_semantic")]
    deps = [_dep("other", [_claim("http://localhost:6333", "ragstack_sfr_semantic")])]
    row = si.reconcile(stores, deps)[0]
    assert row.status == si.UNCLAIMED
    assert row.claimed_by == []


def test_legs_are_never_paired_by_name():
    """A vector claim does not vouch for an index of the same name, or vice versa."""
    stores = [_store("http://localhost:9200", "shared_name", leg=si.TEXT)]
    deps = [_dep("d", [_claim("http://localhost:9200", "shared_name", leg=si.VECTOR)])]
    assert si.reconcile(stores, deps)[0].status == si.UNCLAIMED


def test_name_only_claim_is_graded_weaker_than_exact():
    stores = [_store("http://localhost:6333", "store_a")]
    deps = [_dep("demo", [si.Claim(si.StoreKey("", "store_a"), si.VECTOR, "demo", "registry:a")])]
    row = si.reconcile(stores, deps)[0]
    assert row.status == si.CLAIMED_NAME_ONLY
    assert row.claimed_by == ["demo[registry:a]"]


def test_stopped_deployment_still_claims_its_stores():
    """The 268 GB case: nothing is running, the config file is still on disk."""
    stores = [_store("http://localhost:6333", "ragstack_sfr_tok512", size=215 * 1024**3)]
    deps = [_dep("unified (stopped)", [_claim("http://localhost:6333", "ragstack_sfr_tok512")])]
    assert si.reconcile(stores, deps)[0].status == si.CLAIMED


def test_registry_entry_with_no_store_is_reported_missing():
    stores = [_store("http://localhost:6333", "present")]
    deps = [_dep("d", [_claim("http://localhost:6333", "present"),
                       _claim("http://localhost:6333", "absent")])]
    rows = {r.key.name: r for r in si.reconcile(stores, deps)}
    assert rows["absent"].status == si.MISSING
    assert rows["absent"].store is None


def test_absence_is_not_asserted_for_an_unprobed_backend():
    """Unreachable is not empty. A claim on an instance we never probed is
    unknown, and calling it missing is the same mistake as calling an unclaimed
    store an orphan."""
    stores = [_store("http://localhost:6333", "present")]
    deps = [_dep("d", [_claim("http://localhost:6333", "present"),
                       _claim("http://unprobed:6333", "elsewhere")])]
    assert [r.key.name for r in si.reconcile(stores, deps)] == ["present"]


def test_empty_store_is_not_special_cased():
    """A freshly provisioned tenant is exactly the empty case, so 0 points is
    reported like any other count and never promoted to a delete candidate."""
    stores = [_store("http://localhost:6333", "brand_new", count=0)]
    deps = [_dep("new-tenant", [_claim("http://localhost:6333", "brand_new")])]
    assert si.reconcile(stores, deps)[0].status == si.CLAIMED


def test_unclaimed_sorts_first_and_largest_first():
    stores = [
        _store("http://localhost:6333", "small_loose", size=10),
        _store("http://localhost:6333", "kept", size=10**12),
        _store("http://localhost:6333", "big_loose", size=10**9),
    ]
    deps = [_dep("d", [_claim("http://localhost:6333", "kept")])]
    rows = si.reconcile(stores, deps)
    assert [r.key.name for r in rows] == ["big_loose", "small_loose", "kept"]


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_qdrant_parses_names_and_counts():
    def handler(request):
        if request.url.path == "/collections":
            return httpx.Response(200, json={"result": {"collections": [
                {"name": "b"}, {"name": "a"}]}})
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"result": {
            "points_count": 7 if name == "a" else 0, "status": "green"}})

    with _client(handler) as c:
        stores = si.probe_qdrant("http://localhost:6333/", client=c)
    assert [s.key.name for s in stores] == ["a", "b"]
    assert stores[0].count == 7
    assert stores[0].key.backend == si.canonical_url("http://localhost:6333")
    assert all(s.leg == si.VECTOR for s in stores)


def test_probe_qdrant_sends_api_key():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"result": {"collections": []}})

    with _client(handler) as c:
        si.probe_qdrant("http://localhost:6333", api_key="secret", client=c)
    assert seen["key"] == "secret"


def test_probe_qdrant_survives_one_bad_collection():
    def handler(request):
        if request.url.path == "/collections":
            return httpx.Response(200, json={"result": {"collections": [
                {"name": "ok"}, {"name": "broken"}]}})
        if request.url.path.endswith("broken"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"result": {"points_count": 3}})

    with _client(handler) as c:
        stores = si.probe_qdrant("http://localhost:6333", client=c)
    by_name = {s.key.name: s for s in stores}
    assert by_name["ok"].count == 3
    assert by_name["broken"].count is None  # listed, count unknown — not dropped


def test_probe_elasticsearch_parses_cat_indices():
    def handler(request):
        return httpx.Response(200, json=[
            {"index": ".kibana_1", "docs.count": "1", "store.size": "10",
             "creation.date.string": "2026-01-01T00:00:00.000Z", "health": "green"},
            {"index": "lucid_sfr_tok256", "docs.count": "1554790",
             "store.size": "31000000000",
             "creation.date.string": "2026-08-05T00:00:00.000Z", "health": "yellow"},
        ])

    with _client(handler) as c:
        stores = si.probe_elasticsearch("http://localhost:9200", client=c)
    assert [s.key.name for s in stores] == ["lucid_sfr_tok256"]  # system index skipped
    assert stores[0].count == 1554790
    assert stores[0].size_bytes == 31000000000
    assert stores[0].created_at.startswith("2026-08-05")
    assert stores[0].leg == si.TEXT


def test_probe_elasticsearch_sends_api_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    with _client(handler) as c:
        si.probe_elasticsearch("http://localhost:9200", api_key="k", client=c)
    assert seen["auth"] == "ApiKey k"


def test_collect_probes_each_backend_once():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        if "_cat" in request.url.path:
            return httpx.Response(200, json=[])
        if request.url.path == "/collections":
            return httpx.Response(200, json={"result": {"collections": []}})
        return httpx.Response(200, json={"result": {}})

    deps = [
        si.Deployment(name="a", config_path="", qdrant_url=si.canonical_url("http://q:6333"),
                      es_url=si.canonical_url("http://e:9200")),
        si.Deployment(name="b", config_path="", qdrant_url=si.canonical_url("http://q:6333"),
                      es_url=si.canonical_url("http://e:9200"), es_api_key="k"),
    ]
    with _client(handler) as c:
        si.collect(deps, client=c)
    assert len([u for u in calls if "6333" in u]) == 1
    assert len([u for u in calls if "9200" in u]) == 1


def test_collect_tolerates_an_unreachable_backend():
    def handler(request):
        if "6333" in str(request.url):
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=[])

    deps = [si.Deployment(name="a", config_path="",
                          qdrant_url=si.canonical_url("http://q:6333"),
                          es_url=si.canonical_url("http://e:9200"))]
    with _client(handler) as c:
        assert si.collect(deps, client=c) == []


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_report_never_says_orphan():
    stores = [_store("http://localhost:6333", "loose")]
    deps = [_dep("d", [])]
    rows = si.reconcile(stores, deps)
    text = si.render_text(rows, deps)
    assert "orphan" not in text.lower().replace("does not mean orphan", "")
    assert si.UNCLAIMED in text


def test_json_report_carries_no_credentials():
    dep = si.Deployment(name="d", config_path="x", qdrant_url="http://q:6333",
                        qdrant_api_key="SECRET-QDRANT", es_api_key="SECRET-ES",
                        claims=[_claim("http://q:6333", "s")])
    stores = [_store("http://q:6333", "s")]
    blob = json.dumps(si.to_dict(si.reconcile(stores, [dep]), [dep]))
    assert "SECRET-QDRANT" not in blob
    assert "SECRET-ES" not in blob


def test_text_report_carries_no_credentials():
    dep = si.Deployment(name="d", config_path="x", qdrant_url="http://q:6333",
                        qdrant_api_key="SECRET-QDRANT", es_api_key="SECRET-ES")
    text = si.render_text([], [dep])
    assert "SECRET" not in text


def test_cli_refuses_to_run_with_nothing_to_reconcile_against():
    """Reconciling against a subset reports everything else as unclaimed, which
    is precisely the report that gets production deleted."""
    with pytest.raises(SystemExit) as e:
        si.main([])
    assert e.value.code != 0


def test_human_bytes():
    assert si.human_bytes(None) == "-"
    assert si.human_bytes(512) == "512B"
    assert si.human_bytes(215 * 1024**3) == "215.0GB"
