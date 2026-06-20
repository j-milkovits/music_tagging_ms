"""Unit tests for the rate-limited request loop in ratecontrol.send_json."""

from __future__ import annotations

import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from tagging_ms import ratecontrol

URL = "https://musicbrainz.org/ws/2/release/abc"


def _json_response(payload: bytes = b'{"ok": true}'):
    class _Resp:
        def __enter__(self):
            return BytesIO(payload)

        def __exit__(self, *exc):
            return False

    return _Resp()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the rate-limiter backoff so retries run instantly."""
    monkeypatch.setattr(ratecontrol.time, "sleep", lambda *_: None)


def test_retries_on_connection_reset_then_succeeds() -> None:
    reset = urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))
    attempts = [reset, _json_response()]

    def fake_urlopen(_request, timeout=None):  # noqa: ANN001
        result = attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(ratecontrol.urllib.request, "urlopen", side_effect=fake_urlopen):
        assert ratecontrol.send_json(lambda: object(), URL) == {"ok": True}
    assert attempts == []  # both the failure and the success were consumed


def test_raises_after_exhausting_connection_retries() -> None:
    reset = urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))

    def fake_urlopen(_request, timeout=None):  # noqa: ANN001
        raise reset

    with patch.object(ratecontrol.urllib.request, "urlopen", side_effect=fake_urlopen):
        with pytest.raises(urllib.error.URLError):
            ratecontrol.send_json(lambda: object(), URL)


def test_http_error_other_than_503_429_is_not_retried() -> None:
    calls = {"n": 0}

    def fake_urlopen(_request, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        raise urllib.error.HTTPError(URL, 404, "Not Found", {}, None)

    with patch.object(ratecontrol.urllib.request, "urlopen", side_effect=fake_urlopen):
        with pytest.raises(urllib.error.HTTPError):
            ratecontrol.send_json(lambda: object(), URL)
    assert calls["n"] == 1
