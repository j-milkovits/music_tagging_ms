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


# ----- MusicBrainzClient.find_releases_by_identifier -----


def test_find_releases_by_identifier_barcode_builds_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_send_json(factory, url):  # noqa: ANN001
        captured["url"] = url
        return {"releases": []}

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", fake_send_json)
    MusicBrainzClient().find_releases_by_identifier(barcode="720642442524")
    url = captured["url"]
    assert "/release?" in url
    assert "barcode%3A%22720642442524%22" in url  # barcode:"720642442524"
    assert "dismax" not in url


def test_find_releases_by_identifier_catno_quotes_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_send_json(factory, url):  # noqa: ANN001
        captured["url"] = url
        return {"releases": []}

    monkeypatch.setattr(musicbrainz.ratecontrol, "send_json", fake_send_json)
    MusicBrainzClient().find_releases_by_identifier(catalog_number="GED 24425")
    # catno:"GED 24425" — quoted phrase, space urlencoded as +.
    assert "catno%3A%22GED+24425%22" in captured["url"]


def test_find_releases_by_identifier_requires_exactly_one() -> None:
    client = MusicBrainzClient()
    with pytest.raises(ValueError):
        client.find_releases_by_identifier()
    with pytest.raises(ValueError):
        client.find_releases_by_identifier(barcode="1", catalog_number="2")


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


def _two_disc_release(release_id: str) -> dict:
    """2-CD release: disc 1 has one ~200s track, disc 2 one ~180s track."""
    release = _release_dict(release_id, "DE", "1991")
    release["media"] = [
        {
            "position": 1,
            "format": "CD",
            "track-count": 1,
            "discs": [{"id": "discid-cd1"}],
            "tracks": [
                {
                    "id": "t1",
                    "position": 1,
                    "recording": {"id": "r1", "title": "Track 1", "length": 200000},
                }
            ],
        },
        {
            "position": 2,
            "format": "CD",
            "track-count": 1,
            "discs": [{"id": "discid-cd2"}],
            "tracks": [
                {
                    "id": "t2",
                    "position": 1,
                    "recording": {"id": "r2", "title": "Track 2", "length": 180000},
                }
            ],
        },
    ]
    return release


def test_lookup_disc_discid_reports_matched_disc_position() -> None:
    """A concrete DiscID pins the matched medium via its `discs` list."""
    release = _two_disc_release("rel-2cd")
    svc = _service_with([release])
    result = svc.lookup_disc("discid-cd2", "", ["DE"], None)
    assert result.release is not None
    assert result.release.discnumber == "2"
    assert result.release.totaldiscs == "2"
    # Track-level discnumber tags let the caller select the matched disc.
    matched = [
        t
        for t in result.release.tracks
        if t.applied_track_tags.get("discnumber") == result.release.discnumber
    ]
    assert [t.track_id for t in matched] == ["t2"]


def test_lookup_disc_toc_reports_matched_disc_position() -> None:
    """A TOC-only lookup derives the matched medium from track durations:
    1 track of (13650-150)/75 = 180s — disc 2, not disc 1 (200s)."""
    release = _two_disc_release("rel-2cd")
    svc = _service_with([release])
    result = svc.lookup_disc("-", "1+1+13650+150", ["DE"], None)
    assert result.release is not None
    assert result.release.discnumber == "2"
    assert result.release.totaldiscs == "2"


# ----- barcode / catalog_number lookups -----


def test_lookup_disc_by_barcode_skips_discid_lookup() -> None:
    de = _release_dict("rel-DE", "DE", "1991")
    svc = _service_with([de])
    svc.client.find_releases_by_identifier.return_value = [de]
    result = svc.lookup_disc("-", "", ["DE"], None, barcode="111")
    svc.client.find_releases_by_identifier.assert_called_once_with(
        barcode="111", catalog_number=""
    )
    svc.client.get_release_by_discid.assert_not_called()
    assert result.release is not None
    assert result.release.release_id == "rel-DE"
    # No disc matched a specific medium — discnumber stays empty.
    assert result.release.discnumber == ""
    assert result.release.totaldiscs == "1"
    assert len(result.candidates) == 1


def test_lookup_disc_by_catalog_number_no_match_sets_reason() -> None:
    svc = _service_with([])
    svc.client.find_releases_by_identifier.return_value = []
    result = svc.lookup_disc("-", "", [], None, catalog_number="GED 24425")
    assert result.release is None
    assert result.candidates == ()
    assert result.reason == "No MusicBrainz release found for this catalog number"
