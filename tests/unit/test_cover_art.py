"""Unit tests for cover-art pass-through from the MusicBrainz release.

The service no longer makes a separate Cover Art Archive request; it forwards
the release's `cover-art-archive` availability block instead.
"""

from __future__ import annotations

from tagging_ms.matcher import _extract_cover_art, build_release_tracks
from tagging_ms.models import CoverArt


def test_extract_cover_art_maps_caa_block() -> None:
    release = {
        "cover-art-archive": {
            "front": True,
            "back": False,
            "count": 1,
            "artwork": True,
            "darkened": False,
        }
    }
    assert _extract_cover_art(release) == CoverArt(
        front=True, back=False, count=1, artwork=True, darkened=False
    )


def test_extract_cover_art_missing_block_returns_none() -> None:
    assert _extract_cover_art({}) is None


def test_extract_cover_art_coerces_missing_fields() -> None:
    assert _extract_cover_art({"cover-art-archive": {}}) == CoverArt(
        front=False, back=False, count=0, artwork=False, darkened=False
    )


def test_build_release_tracks_attaches_cover_art(carmen_release: dict) -> None:
    tracks = build_release_tracks(carmen_release, ["DE"])
    assert tracks
    # Carmen fixture exposes front art with 23 images.
    assert all(t.cover_art is not None for t in tracks)
    assert tracks[0].cover_art.front is True
    assert tracks[0].cover_art.count == 23
