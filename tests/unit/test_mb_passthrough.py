"""/api/mb: authenticated passthrough to the mirror's ws/2."""

from __future__ import annotations

import io
import urllib.error
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import tagging_ms.api as api_module

AUTH = {"Authorization": "Bearer test-bearer-token"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, MagicMock]:
    mb = MagicMock()
    mb.get_raw.return_value = {"id": "abc", "title": "X"}
    monkeypatch.setattr(api_module.service, "client", mb)
    return TestClient(api_module.app, raise_server_exceptions=False), mb


def test_requires_bearer(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    assert c.get("/api/mb/release/abc").status_code == 401
    mb.get_raw.assert_not_called()


def test_lookup_forwards_path_and_params(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    res = c.get("/api/mb/release/233121a4?inc=labels+recordings&fmt=xml", headers=AUTH)
    assert res.status_code == 200
    assert res.json() == {"id": "abc", "title": "X"}
    # `+` arrives decoded as a space; the client re-encodes it as `+` for the mirror.
    mb.get_raw.assert_called_once_with("/release/233121a4", {"inc": "labels recordings"})


def test_inc_plus_separator_round_trips_to_the_mirror() -> None:
    from tagging_ms.musicbrainz import MusicBrainzClient

    seen: dict[str, str] = {}
    client = MusicBrainzClient(base_url="http://mirror:5000/ws/2", rate_limit_ms=0)
    client._get_json = lambda path, params: seen.update(path=path, **params) or {}  # type: ignore[method-assign]
    client.get_raw("/release/x", {"inc": "labels recordings"})
    import urllib.parse

    assert urllib.parse.urlencode({"inc": seen["inc"]}) == "inc=labels+recordings"
    assert seen["fmt"] == "json"


def test_search_without_identifier(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    res = c.get("/api/mb/release?query=barcode:720642442524&limit=5", headers=AUTH)
    assert res.status_code == 200
    mb.get_raw.assert_called_once_with("/release", {"query": "barcode:720642442524", "limit": "5"})


@pytest.mark.parametrize("entity", ["collection", "rating", "editor", "../etc"])
def test_unknown_entity_is_404_without_upstream_call(
    client: tuple[TestClient, MagicMock], entity: str
) -> None:
    c, mb = client
    assert c.get(f"/api/mb/{entity}/abc", headers=AUTH).status_code == 404
    mb.get_raw.assert_not_called()


def test_identifier_charset_is_enforced(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    assert c.get("/api/mb/release/abc%20def", headers=AUTH).status_code == 422
    mb.get_raw.assert_not_called()


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


def test_upstream_404_passes_through_with_body(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    mb.get_raw.side_effect = _http_error(404, b'{"error":"Not Found","help":"..."}')
    res = c.get("/api/mb/release/nope", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["error"] == "Not Found"


def test_upstream_5xx_becomes_502(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    mb.get_raw.side_effect = _http_error(500)
    assert c.get("/api/mb/release/abc", headers=AUTH).status_code == 502


def test_unreachable_mirror_becomes_502(client: tuple[TestClient, MagicMock]) -> None:
    c, mb = client
    mb.get_raw.side_effect = urllib.error.URLError("connection refused")
    assert c.get("/api/mb/release/abc", headers=AUTH).status_code == 502
