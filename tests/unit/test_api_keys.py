"""Per-client bearer keys via TAGGING_MS_API_KEYS."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from tagging_ms.api import _configured_keys, require_bearer

app = FastAPI()


@app.get("/who")
def who(request: Request, label: str = Depends(require_bearer)) -> dict[str, str]:  # noqa: B008
    return {"label": label, "state": request.state.key_label}


client = TestClient(app)


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_parses_label_key_pairs_and_single_key_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGING_MS_API_KEYS", " laptop = aaa , papa=bbb,, ")
    monkeypatch.setenv("TAGGING_MS_API_KEY", "ccc")
    assert _configured_keys() == {"aaa": "laptop", "bbb": "papa", "ccc": "default"}


def test_each_key_resolves_to_its_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGING_MS_API_KEYS", "laptop=aaa,papa=bbb")
    monkeypatch.delenv("TAGGING_MS_API_KEY", raising=False)
    assert client.get("/who", headers=_auth("aaa")).json() == {"label": "laptop", "state": "laptop"}
    assert client.get("/who", headers=_auth("bbb")).json()["label"] == "papa"


def test_removed_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGING_MS_API_KEYS", "laptop=aaa")
    monkeypatch.delenv("TAGGING_MS_API_KEY", raising=False)
    assert client.get("/who", headers=_auth("bbb")).status_code == 401
    assert client.get("/who").status_code == 401


def test_malformed_config_is_a_server_error_not_a_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGING_MS_API_KEYS", "no-equals-sign")
    monkeypatch.delenv("TAGGING_MS_API_KEY", raising=False)
    assert client.get("/who", headers=_auth("no-equals-sign")).status_code == 500


def test_no_keys_configured_is_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAGGING_MS_API_KEYS", raising=False)
    monkeypatch.setenv("TAGGING_MS_API_KEY", "")
    assert client.get("/who", headers=_auth("anything")).status_code == 500
