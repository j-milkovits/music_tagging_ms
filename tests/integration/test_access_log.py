"""Structured access log: one JSON line per request with key label and upstream cost."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

import tagging_ms.api as api_module
from tagging_ms import ratecontrol

client = TestClient(api_module.app)
AUTH = {"Authorization": "Bearer test-bearer-token"}


def _records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == "tagging_ms.access"]


def test_unauthenticated_request_is_logged_without_key(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="tagging_ms.access"):
        client.get("/api/version", headers={"CF-Connecting-IP": "203.0.113.7"})
    (rec,) = _records(caplog)
    assert rec["path"] == "/api/version"
    assert rec["status"] == 200
    assert rec["key"] is None
    assert rec["ip"] == "203.0.113.7"
    assert rec["ms"] >= 0


def test_lookup_logs_key_items_and_upstream_counts(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_lookup(*args: object, **kwargs: object) -> None:
        # Runs in FastAPI's worker thread; the counters must still reach the middleware.
        ratecontrol._count("upstream_requests")
        ratecontrol._count("upstream_requests")
        ratecontrol._count("upstream_retries")
        raise RuntimeError("stop here")

    monkeypatch.setattr(api_module.service, "lookup", fake_lookup)
    items = [{"source_id": f"f{i}", "fingerprint": "AQAA", "duration": 10} for i in range(3)]
    with caplog.at_level(logging.INFO, logger="tagging_ms.access"):
        res = client.post("/api/lookup", headers=AUTH, json={"items": items})
    assert res.status_code == 500
    (rec,) = _records(caplog)
    assert rec["key"] == "default"
    assert rec["items"] == 3
    assert rec["status"] == 500
    assert rec["upstream_requests"] == 2
    assert rec["upstream_retries"] == 1
