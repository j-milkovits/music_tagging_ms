"""Request-size limits: item count, fingerprint length, body size."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tagging_ms.api import MAX_BODY_BYTES, MAX_FINGERPRINT_CHARS, MAX_ITEMS, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-bearer-token"}


def _item(i: int, fingerprint: str = "AQAA") -> dict:
    return {"source_id": f"f{i}", "fingerprint": fingerprint, "duration": 10}


def test_too_many_items_is_422() -> None:
    res = client.post("/api/lookup", headers=AUTH, json={"items": [_item(i) for i in range(MAX_ITEMS + 1)]})
    assert res.status_code == 422
    assert "items" in res.text


def test_overlong_fingerprint_is_422() -> None:
    res = client.post(
        "/api/lookup", headers=AUTH, json={"items": [_item(0, "A" * (MAX_FINGERPRINT_CHARS + 1))]}
    )
    assert res.status_code == 422
    assert "fingerprint" in res.text


def test_oversized_body_is_413_before_auth() -> None:
    # No auth header on purpose: the size check must run before anything else.
    res = client.post(
        "/api/lookup",
        content=b"x",
        headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "application/json"},
    )
    assert res.status_code == 413


def test_missing_content_length_is_411() -> None:
    def chunks() -> object:
        yield b"{}"

    res = client.post("/api/lookup", content=chunks(), headers={"Content-Type": "application/json"})
    assert res.status_code == 411
