"""/docs behind a path-stripping gateway (X-Forwarded-Prefix / ROOT_PATH).

The gateway publishes each deployment at /ragstack/<tenant>/api/ and strips the
prefix, so the app saw /docs and rendered `url: '/openapi.json'` — the GATEWAY's
root, which 404s ("Failed to load API definition"). The prefix is a per-request
fact, not a build-time one, so it comes from the proxy's header; with no header
the app is mounted at the root and every URL it emits is unchanged, which is what
keeps direct-port debugging (http://localhost:PORT/docs) working.
"""
import pytest

from ragstack.api import root_path as root_path_mod
from ragstack.api.root_path import _PREFIX_RE, normalize_prefix, validate_setting

pytestmark = pytest.mark.asyncio

PREFIX = "/ragstack/asm-next/api"


@pytest.fixture
def pin_root_path(monkeypatch):
    """Set ROOT_PATH the way the process would see it: resolved once, at import.

    The setting is validated at import (validate_setting), so assigning
    settings.root_path mid-process is NOT how a deployment configures it — the
    resolved value is what the middleware reads. monkeypatch restores it.
    """

    def _pin(value: str) -> None:
        monkeypatch.setattr(root_path_mod, "_CONFIGURED", validate_setting(value))

    return _pin


async def test_docs_stays_root_relative_without_a_proxy(client):
    r = await client.get("/docs")
    assert r.status_code == 200
    assert "'/openapi.json'" in r.text
    assert "servers" not in (await client.get("/openapi.json")).json()


async def test_docs_points_at_the_forwarded_prefix(client):
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": PREFIX})
    assert r.status_code == 200
    # The exact string the browser fetches — the bug was this being root-absolute.
    assert f"'{PREFIX}/openapi.json'" in r.text


async def test_redoc_points_at_the_forwarded_prefix(client):
    r = await client.get("/redoc", headers={"X-Forwarded-Prefix": PREFIX})
    assert f'spec-url="{PREFIX}/openapi.json"' in r.text


async def test_schema_advertises_the_prefix_as_its_server(client):
    # What makes "Try it out" hit /ragstack/<tenant>/api/v1/... instead of /v1/...
    r = await client.get("/openapi.json", headers={"X-Forwarded-Prefix": PREFIX})
    assert r.json()["servers"] == [{"url": PREFIX}]


async def test_the_prefix_does_not_leak_into_the_next_request(client):
    """Why python/pyproject.toml floors fastapi at >=0.133.

    Before 0.133 the /openapi.json route did `self.servers.insert(0, {"url":
    root_path})` — APP-level state, on a cached schema. This middleware makes
    root_path reachable from an unauthenticated request header, so on an older
    FastAPI one curl would pin a stranger's prefix into the schema served to
    every later client, and a fresh prefix per request would grow that list
    without bound. The floor is a security floor; this is what it buys.
    """
    for prefix in (PREFIX, "/other/api"):
        r = await client.get("/openapi.json", headers={"X-Forwarded-Prefix": prefix})
        assert r.json()["servers"] == [{"url": prefix}], "one server, this request's"

    clean = await client.get("/openapi.json")
    assert "servers" not in clean.json(), "a header must not outlive its request"


async def test_prefix_does_not_re_route_requests(client):
    # The gateway already stripped the prefix, so root_path must change only the
    # URLs the app EMITS — never which route a request matches.
    r = await client.get("/health", headers={"X-Forwarded-Prefix": PREFIX})
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/health", "/v1/stats/tenants"])
async def test_a_prefix_the_path_carries_is_dropped(client, path):
    """The one case where root_path is NOT inert.

    Starlette's get_route_path strips root_path off the path, so honouring a
    header prefix the path starts with would re-route the caller's own request
    (X-Forwarded-Prefix: /health + GET /health used to 404). It is never the
    real proxied case — the gateway strips the prefix before we see it — so the
    header is dropped and routing is what the claim says it is.
    """
    before = await client.get(path)
    after = await client.get(path, headers={"X-Forwarded-Prefix": path})
    assert after.status_code == before.status_code
    # A prefix the path does NOT carry is still honoured on that same request —
    # dropping it is about routing, not about distrusting the header.
    honoured = await client.get("/docs", headers={"X-Forwarded-Prefix": path})
    assert f"'{path}/openapi.json'" in honoured.text


async def test_a_dropped_prefix_leaves_the_docs_url_root_relative(client):
    # /docs asked for under a /docs prefix: the prefix is dropped, so the page
    # both renders (it used to 404) and points at the schema it can reach.
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": "/docs"})
    assert r.status_code == 200
    assert "'/openapi.json'" in r.text


async def test_only_a_whole_segment_counts_as_carried(client):
    # /health under a /healthy prefix is not "the path starts with it" in
    # routing terms (get_route_path requires a segment boundary), so the prefix
    # is still honoured for the emitted URLs.
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": "/doc"})
    assert "'/doc/openapi.json'" in r.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_prefix_dependent_responses_say_they_vary(client, path):
    # These three bodies embed the prefix, so a cache in front must key on it.
    r = await client.get(path, headers={"X-Forwarded-Prefix": PREFIX})
    assert "x-forwarded-prefix" in r.headers.get("vary", "").lower()


@pytest.mark.parametrize(
    "hostile",
    [
        "//evil.example",  # protocol-relative: would fetch the schema cross-origin
        "https://evil.example/x",
        "/ok/../../etc",
        "/ok?x=1",
        "/ok#frag",
        "/ok\r\nX-Injected: 1",
        "/" + "a" * 300,
    ],
)
async def test_hostile_prefix_is_ignored(client, hostile):
    # The header is caller-supplied. A rejected value must degrade to "no proxy",
    # never to a URL pointing somewhere else.
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": hostile})
    assert r.status_code == 200
    assert "'/openapi.json'" in r.text


async def test_root_path_setting_pins_the_prefix(client, pin_root_path):
    # For a proxy that cannot be made to send the header. Being a deployment
    # setting it also OUTRANKS the header, which a caller controls.
    pin_root_path("/pinned/")
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": PREFIX})
    assert "'/pinned/openapi.json'" in r.text


async def test_an_invalid_root_path_setting_means_no_proxy(client, pin_root_path, caplog):
    """An unusable ROOT_PATH must not silently hand the decision to the caller.

    The setting is documented as authoritative over the header; falling back to
    the header on a typo would invert exactly that, and quietly.
    """
    with caplog.at_level("WARNING"):
        pin_root_path("https://evil.example/x")
    assert "ROOT_PATH" in caplog.text

    r = await client.get("/docs", headers={"X-Forwarded-Prefix": PREFIX})
    assert "'/openapi.json'" in r.text, "a broken setting must not fall back to the header"


async def test_validate_setting_separates_unset_from_rejected():
    assert validate_setting("") is None  # unset: the header decides
    assert validate_setting("/ragstack/asm/api") == "/ragstack/asm/api"  # pinned
    assert validate_setting("/ok?x=1") == ""  # rejected: no proxy at all


async def test_normalize_prefix_shapes():  # async only to match the module's mark
    assert normalize_prefix("/ragstack/asm/api/") == "/ragstack/asm/api"
    assert normalize_prefix("  /a/b  ") == "/a/b"
    assert normalize_prefix("") == ""
    assert normalize_prefix("/") == ""  # the root is "no prefix", not "/"
    assert normalize_prefix("no-leading-slash") == ""
    # Traversal is a SEGMENT, not a substring: `".." in prefix` missed the first
    # two of these and rejected the last, which is a legitimate path.
    assert normalize_prefix("/a/./b") == ""
    assert normalize_prefix("/.") == ""
    assert normalize_prefix("/a/../b") == ""
    assert normalize_prefix("/v1..2") == "/v1..2"
    assert normalize_prefix("/ok\r\nX-Injected: 1") == ""
    # The charset is anchored with `\Z`, not `$` — `$` also matches just before
    # a trailing newline, so it would admit one. strip() happens to remove that
    # newline first; the anchor is what makes the pattern true on its own.
    assert _PREFIX_RE.match("/ok\n") is None
