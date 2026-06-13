"""Unit tests for the CD DiscID/TOC lookup flow."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock

import pytest

from tagging_ms import musicbrainz
from tagging_ms.models import AudioMetadata
from tagging_ms.musicbrainz import MusicBrainzClient
from tagging_ms.service import StandaloneTaggingService


def _release_dict(release_id: str, country: str, date: str, title: str = "Album") -> dict:
    """Minimal MusicBrainz release shape build_release_tracks can materialise."""
    return {
        "id": release_id,
        "title": title,
        "country": country,
        "date": date,
        "barcode": "111",
        "release-group": {"id": f"rg-{release_id}", "primary-type": "Album"},
        "release-events": [{"country": country, "date": date}],
        "artist-credit": [{"name": "The Band", "artist": {"id": "a1", "name": "The Band"}}],
        "media": [
            {
                "position": 1,
                "format": "CD",
                "track-count": 1,
                "tracks": [
                    {
                        "id": "t1",
                        "position": 1,
                        "recording": {"id": "r1", "title": "Track 1", "length": 200000},
                    }
                ],
            }
        ],
    }


# ----- MusicBrainzClient.get_release_by_discid -----


def test_get_release_by_discid_builds_url_and_normalises_toc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_send_json(factory, url):  # noqa: ANN001
        captured["url"] = url
        return {"releases": []}

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", fake_send_json)
    MusicBrainzClient().get_release_by_discid("-", "1+12+267257+150")
    url = captured["url"]
    assert "/discid/-?" in url
    assert "inc=artist-credits" in url
    # `+`-separated TOC normalised to spaces, which urlencode re-encodes as `+`.
    assert "toc=1+12+267257+150" in url


def test_get_release_by_discid_omits_empty_toc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        musicbrainz.ratecontrol,
        "send_json",
        lambda factory, url: captured.setdefault("url", url) or {"releases": []},
    )
    MusicBrainzClient().get_release_by_discid("realdiscid-", "")
    assert "/discid/realdiscid-?" in captured["url"]
    assert "toc=" not in captured["url"]


def test_get_release_by_discid_404_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_404(factory, url):  # noqa: ANN001
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", raise_404)
    assert MusicBrainzClient().get_release_by_discid("-", "1+2") == {}


def test_get_release_by_discid_400_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_400(factory, url):  # noqa: ANN001
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", raise_400)
    with pytest.raises(urllib.error.HTTPError):
        MusicBrainzClient().get_release_by_discid("-", "1+2")


# ----- StandaloneTaggingService.lookup_disc -----


def _service_with(releases: list[dict], full: dict | None = None) -> StandaloneTaggingService:
    client = MagicMock()
    client.get_release_by_discid.return_value = {"releases": releases}
    client.get_release.return_value = full or (releases[0] if releases else {})
    return StandaloneTaggingService(client=client, acoustid_client=MagicMock())


def test_lookup_disc_no_releases_sets_reason() -> None:
    svc = _service_with([])
    result = svc.lookup_disc("-", "1+2", ["DE"], None)
    assert result.release is None
    assert result.candidates == ()
    assert result.reason


def test_lookup_disc_picks_preferred_country() -> None:
    us = _release_dict("rel-US", "US", "1991")
    de = _release_dict("rel-DE", "DE", "1991")
    svc = _service_with([us, de])
    # Capture which release id the full fetch was asked for.
    svc.client.get_release.side_effect = lambda rid: {"rel-US": us, "rel-DE": de}[rid]
    result = svc.lookup_disc("-", "1+2", ["DE", "XE"], None)
    assert result.release is not None
    assert result.release.release_id == "rel-DE"
    assert len(result.candidates) == 2
    assert {c.release_id for c in result.candidates} == {"rel-US", "rel-DE"}


def test_lookup_disc_metadata_reranks_by_title() -> None:
    a = _release_dict("rel-A", "US", "1991", title="Wrong Album")
    b = _release_dict("rel-B", "US", "1991", title="Greatest Hits")
    svc = _service_with([a, b])
    svc.client.get_release.side_effect = lambda rid: {"rel-A": a, "rel-B": b}[rid]
    result = svc.lookup_disc(
        "-", "1+2", [], AudioMetadata(release="Greatest Hits")
    )
    assert result.release is not None
    assert result.release.release_id == "rel-B"


def test_lookup_disc_materialises_tracks_without_score_fields() -> None:
    de = _release_dict("rel-DE", "DE", "1991")
    svc = _service_with([de])
    result = svc.lookup_disc("-", "1+2", ["DE"], None)
    assert result.release is not None
    assert len(result.release.tracks) == 1
    track = result.release.tracks[0]
    assert track.applied_track_tags.get("title") == "Track 1"
    assert not hasattr(track, "score")
    assert not hasattr(track, "source_id")
