"""MusicBrainz client configuration: mirror env vars and the public-API interlock."""

from __future__ import annotations

import sys

import pytest

from tagging_ms import ratecontrol
from tagging_ms.musicbrainz import DEFAULT_BASE_URL, MusicBrainzClient

MIRROR = "http://musicbrainz:5000/ws/2"


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TAGGING_MS_MB_BASE_URL", "TAGGING_MS_MB_RATE_LIMIT_MS", "TAGGING_MS_MB_CONCURRENCY"):
        monkeypatch.delenv(key, raising=False)


def test_defaults_to_public_api_with_public_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    client = MusicBrainzClient()
    assert client.base_url == DEFAULT_BASE_URL
    assert client.rate_limit_ms == 1000
    assert client.concurrency == 1
    hostkey = ratecontrol.hostkey_from_url(DEFAULT_BASE_URL)
    assert ratecontrol.REQUEST_DELAY_MINIMUM[hostkey] == 1000
    assert ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] == 1.0


def test_env_configures_mirror_without_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAGGING_MS_MB_BASE_URL", MIRROR + "/")
    monkeypatch.setenv("TAGGING_MS_MB_RATE_LIMIT_MS", "0")
    monkeypatch.setenv("TAGGING_MS_MB_CONCURRENCY", "8")
    client = MusicBrainzClient()
    assert client.base_url == MIRROR
    hostkey = ratecontrol.hostkey_from_url(MIRROR)
    assert hostkey == ("musicbrainz", 5000)
    assert ratecontrol.REQUEST_DELAY[hostkey] == 0
    assert ratecontrol.REQUEST_DELAY_MINIMUM[hostkey] == 0
    assert ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] == 8.0
    # No spacing: consecutive requests are not told to wait.
    for _ in range(3):
        assert ratecontrol.get_delay_to_next_request(hostkey) == (False, 0)


def test_mirror_window_allows_parallel_requests_from_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0, concurrency=4)
    hostkey = ratecontrol.hostkey_from_url(MIRROR)
    try:
        for _ in range(4):
            assert ratecontrol.get_delay_to_next_request(hostkey) == (False, 0)
            ratecontrol.increment_requests(hostkey)
        # Fifth in-flight request exceeds the window and must back off.
        assert ratecontrol.get_delay_to_next_request(hostkey) == (True, sys.maxsize)
    finally:
        for _ in range(4):
            ratecontrol.decrement_requests(hostkey)


@pytest.mark.parametrize(
    ("base_url", "rate_limit_ms", "concurrency"),
    [
        (DEFAULT_BASE_URL, 0, 1),
        (DEFAULT_BASE_URL, 999, 1),
        (DEFAULT_BASE_URL, 1000, 2),
        ("https://beta.musicbrainz.org/ws/2", 0, 1),
    ],
)
def test_interlock_refuses_mirror_limits_against_public_api(
    monkeypatch: pytest.MonkeyPatch, base_url: str, rate_limit_ms: int, concurrency: int
) -> None:
    monkeypatch.setenv("TAGGING_MS_MB_BASE_URL", base_url)
    monkeypatch.setenv("TAGGING_MS_MB_RATE_LIMIT_MS", str(rate_limit_ms))
    monkeypatch.setenv("TAGGING_MS_MB_CONCURRENCY", str(concurrency))
    with pytest.raises(RuntimeError, match="public MusicBrainz API"):
        MusicBrainzClient()


def test_interlock_does_not_trigger_for_lookalike_host() -> None:
    # "notmusicbrainz.org" is not the public API; only the exact host or its subdomains are.
    MusicBrainzClient(base_url="http://notmusicbrainz.org/ws/2", rate_limit_ms=0)


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        MusicBrainzClient(base_url=MIRROR, rate_limit_ms=-1)
    with pytest.raises(ValueError):
        MusicBrainzClient(base_url=MIRROR, concurrency=0)
