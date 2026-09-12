"""Live checks against a MusicBrainz mirror.

Skipped unless TAGGING_MS_MB_BASE_URL points at a mirror. Run on the VM with:

    TAGGING_MS_MB_BASE_URL=http://127.0.0.1:5000/ws/2 TAGGING_MS_MB_RATE_LIMIT_MS=0 \
        pytest -m mirror --record-mode=none -p no:recording
"""

from __future__ import annotations

import os

import pytest

from tagging_ms.musicbrainz import MusicBrainzClient

CARMEN = "2dc5dfc7-61df-4948-8c6b-e25a4cd039c8"
CARMEN_BARCODE = "720642442524"
CARMEN_FANTASIE_CATNO = "437 544-2"
ELTON_DISCID = "78MUjxQ_365SFuDcykZxpqGoG2A-"

pytestmark = [
    pytest.mark.mirror,
    pytest.mark.skipif(
        "musicbrainz.org" in os.getenv("TAGGING_MS_MB_BASE_URL", "musicbrainz.org"),
        reason="TAGGING_MS_MB_BASE_URL does not point at a mirror",
    ),
]


@pytest.fixture(scope="module")
def mirror() -> MusicBrainzClient:
    return MusicBrainzClient()


def test_release_has_genres_relations_and_cover_art_block(mirror: MusicBrainzClient) -> None:
    release = mirror.get_release(CARMEN)
    assert release["title"]
    assert release["media"] and release["media"][0]["tracks"]
    assert release.get("genres"), "genres empty: mbdump-derived not loaded?"
    recording_rels = [
        rel for medium in release["media"] for track in medium["tracks"]
        for rel in track["recording"].get("relations", [])
    ]
    assert recording_rels, "no recording-level relations"
    assert isinstance(release.get("cover-art-archive"), dict)


def test_discid_lookup(mirror: MusicBrainzClient) -> None:
    payload = mirror.get_release_by_discid(ELTON_DISCID, "")
    assert payload.get("releases")


def test_barcode_search_uses_solr(mirror: MusicBrainzClient) -> None:
    assert mirror.find_releases_by_identifier(barcode=CARMEN_BARCODE)


def test_catno_search_uses_solr(mirror: MusicBrainzClient) -> None:
    assert mirror.find_releases_by_identifier(catalog_number=CARMEN_FANTASIE_CATNO)
