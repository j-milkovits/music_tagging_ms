"""TAGGING_MS_ENV=production: no docs, no git SHA, no tracebacks in responses."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import tagging_ms.api as api_module


@pytest.fixture
def production_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Reload the API module with the production flag set, restore afterwards."""
    monkeypatch.setenv("TAGGING_MS_ENV", "production")
    module = importlib.reload(api_module)
    try:
        yield TestClient(module.app, raise_server_exceptions=False)
    finally:
        monkeypatch.delenv("TAGGING_MS_ENV")
        importlib.reload(api_module)


def test_docs_are_disabled(production_client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert production_client.get(path).status_code == 404, path


def test_version_has_no_git_sha(production_client: TestClient) -> None:
    body = production_client.get("/api/version").json()
    assert body == {"name": "tagging-ms", "version": api_module.__version__}


def test_handler_failure_returns_correlation_id_only(
    production_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(api_module.service, "lookup", boom)
    res = production_client.post(
        "/api/lookup",
        headers={"Authorization": "Bearer test-bearer-token"},
        json={"items": [{"source_id": "a", "fingerprint": "AQAA", "duration": 10}]},
    )
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["error"] == "internal error"
    assert len(detail["correlation_id"]) == 12
    assert "traceback" not in detail
    assert "secret internals" not in res.text


def test_unhandled_exception_is_generic(
    production_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail after the handler's own try block so the catch-all handler runs.
    def boom(result: object) -> dict:
        raise ValueError("secret internals")

    monkeypatch.setattr(api_module, "_serialize_lookup_result", boom)
    monkeypatch.setattr(api_module.service, "lookup", lambda *a, **k: object())
    res = production_client.post(
        "/api/lookup",
        headers={"Authorization": "Bearer test-bearer-token"},
        json={"items": [{"source_id": "a", "fingerprint": "AQAA", "duration": 10}]},
    )
    assert res.status_code == 500
    assert res.json()["detail"]["error"] == "internal error"
    assert "secret internals" not in res.text


def test_development_mode_keeps_docs_and_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_module.app)
    assert client.get("/docs").status_code == 200
    assert "git_sha" in client.get("/api/version").json()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("visible in dev")

    monkeypatch.setattr(api_module.service, "lookup", boom)
    res = client.post(
        "/api/lookup",
        headers={"Authorization": "Bearer test-bearer-token"},
        json={"items": [{"source_id": "a", "fingerprint": "AQAA", "duration": 10}]},
    )
    assert res.status_code == 500
    assert "visible in dev" in res.json()["detail"]["traceback"]
