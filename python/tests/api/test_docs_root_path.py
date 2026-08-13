"""/docs behind a path-stripping gateway (X-Forwarded-Prefix / ROOT_PATH).

The gateway publishes each deployment at /ragstack/<tenant>/api/ and strips the
prefix, so the app saw /docs and rendered `url: '/openapi.json'` — the GATEWAY's
root, which 404s ("Failed to load API definition"). The prefix is a per-request
fact, not a build-time one, so it comes from the proxy's header; with no header
the app is mounted at the root and every URL it emits is unchanged, which is what
keeps direct-port debugging (http://localhost:PORT/docs) working.
"""
import pytest

from ragstack.api.root_path import normalize_prefix
from ragstack.config import settings

pytestmark = pytest.mark.asyncio

PREFIX = "/ragstack/asm-next/api"


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


async def test_prefix_does_not_re_route_requests(client):
    # The gateway already stripped the prefix, so root_path must change only the
    # URLs the app EMITS — never which route a request matches.
    r = await client.get("/health", headers={"X-Forwarded-Prefix": PREFIX})
    assert r.status_code == 200


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


async def test_root_path_setting_pins_the_prefix(client, monkeypatch):
    # For a proxy that cannot be made to send the header. Being a deployment
    # setting it also OUTRANKS the header, which a caller controls.
    monkeypatch.setattr(settings, "root_path", "/pinned/")
    r = await client.get("/docs", headers={"X-Forwarded-Prefix": PREFIX})
    assert "'/pinned/openapi.json'" in r.text


async def test_normalize_prefix_shapes():  # async only to match the module's mark
    assert normalize_prefix("/ragstack/asm/api/") == "/ragstack/asm/api"
    assert normalize_prefix("  /a/b  ") == "/a/b"
    assert normalize_prefix("") == ""
    assert normalize_prefix("/") == ""  # the root is "no prefix", not "/"
    assert normalize_prefix("no-leading-slash") == ""
