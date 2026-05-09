"""Unit tests for joint_matcher: stage 1 selection, stage 2 assignment,
determinism, splits, coverage fallback.
"""

from __future__ import annotations

from tagging_ms.joint_matcher import (
    FileCandidates,
    Thresholds,
    assign_files_to_tracks,
    select_releases,
)
from tagging_ms.models import AudioMetadata, ReleaseTrack


def _release(release_id: str, title: str = "Album", country: str = "DE") -> dict:
    return {
        "id": release_id,
        "title": title,
        "country": country,
        "date": "2020-01-01",
        "release-group": {"id": f"rg-{release_id}", "primary-type": "Album"},
        "media": [{"format": "CD", "track-count": 10}],
    }


def _recording(rec_id: str, title: str, length_ms: int, releases: list[dict]) -> dict:
    return {
        "id": rec_id,
        "title": title,
        "length": length_ms,
        "score": 100,
        "releases": releases,
    }


def _file(source_id: str, title: str, length_ms: int, recordings: list[dict]) -> FileCandidates:
    return FileCandidates(
        source_id=source_id,
        metadata=AudioMetadata(title=title, length_ms=length_ms, release_type="album"),
        recordings=tuple(recordings),
    )


# ----- Stage 1 -----


def test_select_releases_picks_highest_joint_score() -> None:
    rel_a = _release("rel-A", "Greatest Hits", "DE")
    rel_b = _release("rel-B", "Different Album", "US")
    files = [
        _file("f1", "Track 1", 200_000, [_recording("r1", "Track 1", 200_000, [rel_a, rel_b])]),
        _file("f2", "Track 2", 210_000, [_recording("r2", "Track 2", 210_000, [rel_a])]),
    ]
    result = select_releases(files, ["DE"], Thresholds(min_per_file_score=0.1, min_coverage=0.5, split_margin=0.15))
    assert len(result.selections) == 1
    assert result.selections[0].release_id == "rel-A"
    assert set(result.selections[0].file_ids) == {"f1", "f2"}


def test_select_releases_is_deterministic_under_input_shuffle() -> None:
    rel_a = _release("rel-A", "Greatest Hits", "DE")
    rel_b = _release("rel-B", "Different Album", "DE")
    files = [
        _file("f1", "T1", 200_000, [_recording("r1", "T1", 200_000, [rel_a, rel_b])]),
        _file("f2", "T2", 210_000, [_recording("r2", "T2", 210_000, [rel_a, rel_b])]),
        _file("f3", "T3", 220_000, [_recording("r3", "T3", 220_000, [rel_a])]),
    ]
    thresholds = Thresholds(min_per_file_score=0.1, min_coverage=0.5, split_margin=0.15)

    base = select_releases(files, ["DE"], thresholds)
    for _ in range(10):
        shuffled = list(reversed(files))
        repeat = select_releases(shuffled, ["DE"], thresholds)
        assert repeat.selections == base.selections
        assert repeat.unmatched_file_ids == base.unmatched_file_ids


def test_select_releases_country_tiebreak_picks_preferred() -> None:
    rel_de = _release("rel-DE", "Album", "DE")
    rel_us = _release("rel-US", "Album", "US")
    files = [
        _file("f1", "T1", 200_000, [_recording("r1", "T1", 200_000, [rel_de, rel_us])]),
    ]
    result = select_releases(files, ["DE", "US"], Thresholds(min_per_file_score=0.0, min_coverage=0.0, split_margin=0.15))
    assert result.selections[0].release_id == "rel-DE"


def test_select_releases_min_coverage_drops_release_routes_to_unmatched() -> None:
    rel = _release("rel-X")
    file_with_match = _file(
        "f1", "T1", 200_000, [_recording("r1", "T1", 200_000, [rel])]
    )
    file_with_weak_match = _file(
        "f2", "T2", 60_000, [_recording("r2", "Different Title", 999_000, [rel])]
    )
    file_with_weak_match2 = _file(
        "f3", "T3", 60_000, [_recording("r3", "Other Title", 999_000, [rel])]
    )
    thresholds = Thresholds(min_per_file_score=0.5, min_coverage=0.8, split_margin=0.0)
    result = select_releases(
        [file_with_match, file_with_weak_match, file_with_weak_match2],
        ["DE"],
        thresholds,
    )
    assert result.selections == ()
    assert set(result.unmatched_file_ids) == {"f1", "f2", "f3"}


def test_select_releases_single_file_degenerate() -> None:
    rel = _release("rel-X")
    files = [_file("f1", "T1", 200_000, [_recording("r1", "T1", 200_000, [rel])])]
    result = select_releases(files, ["DE"], Thresholds(min_per_file_score=0.1, min_coverage=0.5, split_margin=0.15))
    assert len(result.selections) == 1
    assert result.selections[0].file_ids == ("f1",)


def test_select_releases_split_routes_misfit_files() -> None:
    """A single misfit file goes to a secondary release on the next iteration."""
    rel_main = _release("rel-main", "Album", "DE")
    rel_extra = _release("rel-extra", "Bonus", "DE")
    files = [
        _file("f1", "T1", 200_000, [_recording("r1", "T1", 200_000, [rel_main])]),
        _file("f2", "T2", 210_000, [_recording("r2", "T2", 210_000, [rel_main])]),
        _file("f3", "T3", 220_000, [_recording("r3", "T3", 220_000, [rel_main])]),
        _file("bonus", "Bonus Track", 100_000, [
            _recording("rB", "Bonus Track", 100_000, [rel_extra]),
        ]),
    ]
    thresholds = Thresholds(min_per_file_score=0.1, min_coverage=0.25, split_margin=0.15)
    result = select_releases(files, ["DE"], thresholds)
    release_ids = {sel.release_id for sel in result.selections}
    assert "rel-main" in release_ids
    assert "rel-extra" in release_ids


# ----- Stage 2 -----


def _release_track(track_id: str, title: str, length_ms: int, position: int) -> ReleaseTrack:
    return ReleaseTrack(
        release_id="rel-X",
        release_group_id="rg-X",
        track_id=track_id,
        recording_id=f"rec-{track_id}",
        metadata=AudioMetadata(
            title=title,
            length_ms=length_ms,
            tracknumber=str(position),
            discnumber="1",
        ),
    )


def test_assign_files_to_tracks_basic_one_to_one() -> None:
    files = [
        _file("f1", "Track 1", 200_000, []),
        _file("f2", "Track 2", 210_000, []),
    ]
    selection = type(
        "S",
        (),
        {
            "release_id": "rel-X",
            "joint_score": 1.5,
            "file_ids": ("f1", "f2"),
            "per_file_best": {"f1": 0.9, "f2": 0.85},
        },
    )()
    tracks = [
        _release_track("t1", "Track 1", 200_000, 1),
        _release_track("t2", "Track 2", 210_000, 2),
    ]

    def score_fn(file: FileCandidates, track: ReleaseTrack) -> float:
        # Reward exact title match heavily.
        return 1.0 if file.metadata.title == track.metadata.title else 0.0

    result = assign_files_to_tracks(files, selection, tracks, score_fn, min_per_file_score=0.5)
    assert {a.source_id: a.track_id for a in result.assignments} == {"f1": "t1", "f2": "t2"}
    assert result.unmatched_file_ids == ()


def test_assign_files_to_tracks_excludes_low_scores() -> None:
    files = [_file("f1", "Track 1", 200_000, [])]
    selection = type(
        "S",
        (),
        {
            "release_id": "rel-X",
            "joint_score": 0.1,
            "file_ids": ("f1",),
            "per_file_best": {"f1": 0.1},
        },
    )()
    tracks = [_release_track("t1", "Other", 999_999, 1)]

    def score_fn(file: FileCandidates, track: ReleaseTrack) -> float:
        return 0.1

    result = assign_files_to_tracks(files, selection, tracks, score_fn, min_per_file_score=0.5)
    assert result.assignments == ()
    assert result.unmatched_file_ids == ("f1",)


def test_assign_files_to_tracks_more_files_than_tracks() -> None:
    files = [
        _file("f1", "Track 1", 200_000, []),
        _file("f2", "Track 2", 210_000, []),
        _file("f3", "Track 3", 220_000, []),
    ]
    selection = type(
        "S",
        (),
        {
            "release_id": "rel-X",
            "joint_score": 1.5,
            "file_ids": ("f1", "f2", "f3"),
            "per_file_best": {"f1": 0.9, "f2": 0.85, "f3": 0.0},
        },
    )()
    tracks = [
        _release_track("t1", "Track 1", 200_000, 1),
        _release_track("t2", "Track 2", 210_000, 2),
    ]

    def score_fn(file: FileCandidates, track: ReleaseTrack) -> float:
        return 1.0 if file.metadata.title == track.metadata.title else 0.0

    result = assign_files_to_tracks(files, selection, tracks, score_fn, min_per_file_score=0.5)
    assigned = {a.source_id for a in result.assignments}
    assert assigned == {"f1", "f2"}
    assert result.unmatched_file_ids == ("f3",)
