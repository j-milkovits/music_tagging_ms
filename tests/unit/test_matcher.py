"""Unit tests for matcher.py.

Covers the score_file_track_on_release / compare_track parity (the new helper
must produce the same number that compare_track produces for a single-release
track), and the relationship extraction on real release fixtures.
"""

from __future__ import annotations

from tagging_ms.matcher import (
    FILE_COMPARISON_WEIGHTS,
    build_release_tracks,
    compare_track,
    release_to_metadata,
    score_file_track_on_release,
    score_track_only_parts,
)
from tagging_ms.models import AudioMetadata


def _build_track(release: dict) -> dict:
    """Pick a recording from a release and build a track-shaped dict around it."""
    medium = release["media"][0]
    track = medium["tracks"][0]
    rec = track["recording"]
    return {
        "id": rec["id"],
        "title": rec.get("title", ""),
        "artist-credit": rec.get("artist-credit", []),
        "length": rec.get("length"),
        "score": 100,
        "releases": [release],
    }


def test_score_file_track_on_release_matches_compare_track_single_release(
    karajan_release: dict,
) -> None:
    track = _build_track(karajan_release)
    file_md = AudioMetadata(
        title=track["title"],
        artist="Berliner Philharmoniker",
        length_ms=int(track["length"]),
        date="1962",
        release_type="album",
    )
    via_helper = score_file_track_on_release(
        file_md, track, karajan_release, FILE_COMPARISON_WEIGHTS, ["DE", "XE"]
    )
    via_compare = compare_track(file_md, track, FILE_COMPARISON_WEIGHTS, ["DE", "XE"])
    assert via_helper == via_compare > 0.0


def test_compare_track_no_releases_returns_track_only_score() -> None:
    file_md = AudioMetadata(title="Hello", length_ms=200000)
    track = {"title": "Hello", "length": 200000, "score": 100}
    score = compare_track(file_md, track, FILE_COMPARISON_WEIGHTS, None)
    assert 0.0 < score <= 1.0


# ----- Issue 2: honest scores when metadata is sparse/absent -----


def test_isvideo_alone_does_not_inflate_score() -> None:
    """A file with no comparable metadata must not score on is_video alone."""
    file_md = AudioMetadata()  # all defaults; is_video=False
    track = {"title": "Anything", "video": False}
    parts = score_track_only_parts(file_md, track, FILE_COMPARISON_WEIGHTS)
    # No title/artist/length signal -> is_video is suppressed -> no parts.
    assert parts == []


def test_isvideo_counts_alongside_real_metadata() -> None:
    file_md = AudioMetadata(title="Hello")
    track = {"title": "Hello", "video": False}
    keys = {w for _, w in score_track_only_parts(file_md, track, FILE_COMPARISON_WEIGHTS)}
    # Both the title weight (13) and the isvideo weight (2) contribute.
    assert FILE_COMPARISON_WEIGHTS["title"] in keys
    assert FILE_COMPARISON_WEIGHTS["isvideo"] in keys


def test_no_metadata_score_is_fingerprint_confidence_not_one() -> None:
    """With no metadata the score is the AcoustID confidence, not a fake 1.0."""
    file_md = AudioMetadata()
    track = {"title": "Anything", "score": 87.0}  # AcoustID confidence 0.87
    on_release = score_file_track_on_release(
        file_md, track, {}, FILE_COMPARISON_WEIGHTS, None
    )
    via_compare = compare_track(file_md, track, FILE_COMPARISON_WEIGHTS, None)
    assert on_release == via_compare == 0.87


# ----- Issue 1: null-safe extraction against malformed MusicBrainz data -----


def test_release_to_metadata_survives_null_nested_values() -> None:
    """MusicBrainz can return present-but-null keys and null list elements."""
    release = {
        "id": "rid",
        "title": "Some Release",
        "text-representation": None,
        "label-info": [
            None,
            {"label": None},
            {"label": {"name": "Real Label"}, "catalog-number": "CAT-1"},
            {"catalog-number": None},
        ],
        "genres": [None, {"name": "Rock", "count": 3}],
    }
    md = release_to_metadata(release)
    assert md.label == "Real Label"
    assert md.catalognumber == "CAT-1"
    assert md.script == ""


def test_release_to_metadata_sets_cover_art_fields() -> None:
    md = release_to_metadata(
        {"id": "rid", "title": "X"},
        cover_art={
            "cover_art_url": "http://coverartarchive.org/release/rid/1.jpg",
            "cover_art_thumb_url": "http://coverartarchive.org/release/rid/1-250.jpg",
        },
    )
    assert md.cover_art_url.endswith("/1.jpg")
    assert md.cover_art_thumb_url.endswith("-250.jpg")


def test_build_release_tracks_extracts_composer_for_karajan(karajan_release: dict) -> None:
    """The work-rels fix should expose composer on Karajan's Beethoven 9 tracks."""
    tracks = build_release_tracks(karajan_release, ["DE", "XE"])
    assert tracks, "Expected at least one track"
    composer_names = {
        c.name for t in tracks for c in t.track_credits.composers
    }
    assert "Ludwig van Beethoven" in composer_names


def test_build_release_tracks_populates_conductor_and_performers(
    karajan_release: dict,
) -> None:
    tracks = build_release_tracks(karajan_release, ["DE"])
    conductor_seen = any(
        "Karajan" in c.name
        for t in tracks
        for c in (*t.track_credits.conductors, *t.release_credits.conductors)
    )
    performers_seen = any(
        "Berliner Philharmoniker" in p.name
        for t in tracks
        for p in (*t.track_credits.performers, *t.release_credits.performers)
    )
    assert conductor_seen
    assert performers_seen


def test_build_release_tracks_exposes_instruments_separate_from_performers(
    carmen_release: dict,
) -> None:
    """Instrument relations land in `instruments` (not `performers`)."""
    tracks = build_release_tracks(carmen_release, ["DE"])
    assert tracks

    all_instruments = [
        p
        for t in tracks
        for p in (*t.track_credits.instruments, *t.release_credits.instruments)
    ]
    # Anne-Sophie Mutter plays violin on this release.
    assert any(
        p.name == "Anne‐Sophie Mutter" and "violin" in p.attributes
        for p in all_instruments
    )

    # Instrument players must NOT be duplicated into `performers`.
    performer_names = {
        p.name
        for t in tracks
        for p in (*t.track_credits.performers, *t.release_credits.performers)
    }
    assert "Anne‐Sophie Mutter" not in performer_names


def test_build_release_tracks_populates_label_isrc_barcode(carmen_release: dict) -> None:
    tracks = build_release_tracks(carmen_release, ["DE"])
    assert tracks
    assert any(t.metadata.isrc for t in tracks)
    assert all(t.metadata.label == "Deutsche Grammophon" for t in tracks)
    assert all(t.metadata.barcode == "028943754422" for t in tracks)
    assert all(t.metadata.genre for t in tracks)


def test_build_release_tracks_populates_workid_when_composer_absent(
    carmen_release: dict,
) -> None:
    """Carmen-Fantasie works lack direct composer rels; workid should still appear."""
    tracks = build_release_tracks(carmen_release, ["DE"])
    work_ids = [
        w.musicbrainz_id
        for t in tracks
        for w in t.track_credits.works
        if w.musicbrainz_id
    ]
    assert len(work_ids) >= 5


def test_build_release_tracks_populates_release_artists_with_sort_names(
    carmen_release: dict,
) -> None:
    """Structured release-artist credits include sort_name, type, and MBID."""
    tracks = build_release_tracks(carmen_release, ["DE"])
    assert tracks
    # Every track on this release shares the same release-level artist credits.
    release_artists = tracks[0].release_artists
    assert len(release_artists) == 3
    first = release_artists[0]
    assert first.name == "Anne‐Sophie Mutter"
    assert first.sort_name == "Mutter, Anne‐Sophie"
    assert first.musicbrainz_artistid == "206e3a6b-301f-4a0d-8632-7dfd7927cbcf"
    assert first.type == "Person"
    assert first.disambiguation == "violinist"
    # Second contributor is the orchestra — verifies the type field carries
    # group/orchestra distinctions.
    assert any(ac.type == "Orchestra" for ac in release_artists)
    # Track-level artists are also populated (may differ per track).
    assert all(t.artists for t in tracks)
