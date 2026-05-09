"""Unit tests for StandaloneTaggingService.lookup orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock

from tagging_ms.acoustid import AcoustIdLookupResult
from tagging_ms.joint_matcher import Thresholds
from tagging_ms.service import (
    LookupItem,
    MaterialisedRelease,
    StandaloneTaggingService,
    Unmatched,
)


def _release_dict(release_id: str, recording_ids: list[str]) -> dict:
    """Build a MusicBrainz-shape release dict with one CD medium."""
    return {
        "id": release_id,
        "title": "Test Album",
        "country": "DE",
        "date": "2020-01-01",
        "release-group": {
            "id": f"rg-{release_id}",
            "primary-type": "Album",
            "first-release-date": "2020-01-01",
        },
        "media": [
            {
                "position": 1,
                "format": "CD",
                "track-count": len(recording_ids),
                "tracks": [
                    {
                        "id": f"track-{i + 1}",
                        "position": i + 1,
                        "recording": {
                            "id": rec_id,
                            "title": f"Track {i + 1}",
                            "length": 200_000,
                        },
                    }
                    for i, rec_id in enumerate(recording_ids)
                ],
            }
        ],
        "release-events": [{"country": "DE", "date": "2020-01-01"}],
    }


def test_lookup_joint_routes_stage2_unmatched_to_rescue() -> None:
    """4 files → 3-track release: 3 land in joint, 1 gets per-file rescue."""
    release = _release_dict("rel-X", ["rec-1", "rec-2", "rec-3"])
    fingerprint_to_recording = {
        "fp1": "rec-1",
        "fp2": "rec-2",
        "fp3": "rec-3",
        "fp4": "rec-1",
    }

    def fake_acoustid_lookup(fingerprint: str, duration: int, limit: int = 10):
        rec_id = fingerprint_to_recording[fingerprint]
        return AcoustIdLookupResult(
            acoustid_id=f"acid-{fingerprint}",
            recordings=[
                {
                    "id": rec_id,
                    "title": f"Track for {rec_id}",
                    "length": 200_000,
                    "score": 100,
                    "releases": [release],
                }
            ],
        )

    mb_client = MagicMock()
    mb_client.get_release.return_value = release

    acoustid_client = MagicMock()
    acoustid_client.lookup_by_fingerprint.side_effect = fake_acoustid_lookup

    service = StandaloneTaggingService(
        client=mb_client, acoustid_client=acoustid_client
    )

    items = [
        LookupItem(source_id=f"f{i}", fingerprint=f"fp{i}", duration=200, metadata=None)
        for i in (1, 2, 3, 4)
    ]
    result = service.lookup(
        items=items,
        joint=True,
        preferred_countries=["DE"],
        thresholds=Thresholds(
            min_per_file_score=0.5, min_coverage=0.5, split_margin=0.15
        ),
        search_limit=5,
    )

    assert result.mode == "joint"
    # The joint stage produced 1 release for 3 of 4 files; the rescue produced
    # a second 1-file assignment for the 4th file (which still matches rel-X
    # by recording-id but had no track slot left in the joint Hungarian).
    assert sum(len(a.tracks) for a in result.assignments) == 4
    assert result.unmatched == ()
    assert result.diagnostics["files_in"] == 4
    assert result.diagnostics["files_matched"] == 4

    # Release tags hoisted, acoustid_id on each file.
    for assignment in result.assignments:
        assert assignment.applied_release_tags.get("release")
        # The fake release fixture has no nested `artist` objects on its
        # tracks, so structured artist credits resolve to empty tuples —
        # matching the safe-default behavior.
        assert assignment.release_artists == ()
        for file in assignment.tracks:
            assert file.acoustid_id is not None
            assert file.applied_track_tags.get("title")
            assert file.artists == ()


def test_lookup_per_file_no_recordings_falls_into_unmatched() -> None:
    """A fingerprint with zero AcoustID hits → unmatched, not assignment."""
    acoustid_client = MagicMock()
    acoustid_client.lookup_by_fingerprint.return_value = AcoustIdLookupResult(
        acoustid_id=None, recordings=[]
    )
    service = StandaloneTaggingService(
        client=MagicMock(), acoustid_client=acoustid_client
    )
    result = service.lookup(
        items=[LookupItem(source_id="f1", fingerprint="fpX", duration=200)],
        joint=False,
        preferred_countries=["DE"],
        thresholds=Thresholds(min_per_file_score=0.3, min_coverage=0.5, split_margin=0.15),
        search_limit=5,
    )
    assert result.mode == "per-file"
    assert result.assignments == ()
    assert len(result.unmatched) == 1
    assert isinstance(result.unmatched[0], Unmatched)
    assert result.unmatched[0].source_id == "f1"
    assert result.unmatched[0].reason == "AcoustID lookup returned no recordings"
    assert result.unmatched[0].best_guess is None


def test_lookup_per_file_below_threshold_lands_in_unmatched_with_best_guess() -> None:
    """A fingerprint with a hit but score below threshold → unmatched + best_guess populated."""
    release = _release_dict("rel-Y", ["rec-9"])

    acoustid_client = MagicMock()
    acoustid_client.lookup_by_fingerprint.return_value = AcoustIdLookupResult(
        acoustid_id="acid-fpZ",
        recordings=[
            {
                "id": "rec-9",
                "title": "Some Other Title",
                "length": 999_000,
                "score": 100,
                "releases": [release],
            }
        ],
    )
    mb_client = MagicMock()
    mb_client.get_release.return_value = release

    service = StandaloneTaggingService(client=mb_client, acoustid_client=acoustid_client)
    # min_per_file_score=0.99 forces score-below-threshold path.
    from tagging_ms.models import AudioMetadata

    result = service.lookup(
        items=[
            LookupItem(
                source_id="f1",
                fingerprint="fpZ",
                duration=200,
                metadata=AudioMetadata(title="Different Title", length_ms=200_000),
            )
        ],
        joint=False,
        preferred_countries=["DE"],
        thresholds=Thresholds(
            min_per_file_score=0.99, min_coverage=0.5, split_margin=0.15
        ),
        search_limit=5,
    )
    assert result.mode == "per-file"
    assert result.assignments == ()
    assert len(result.unmatched) == 1
    u = result.unmatched[0]
    assert u.reason == "No AcoustID match above threshold"
    assert u.best_guess is not None
    assert u.best_guess.recording_id == "rec-9"
    assert u.best_guess.acoustid_id == "acid-fpZ"


def test_lookup_per_file_groups_same_release_into_one_assignment() -> None:
    """3 files independently resolving to rel-Z collapse into one 3-file assignment."""
    release = _release_dict("rel-Z", ["rec-1", "rec-2", "rec-3"])
    fp_to_rec = {"fp1": "rec-1", "fp2": "rec-2", "fp3": "rec-3"}

    def fake_acoustid_lookup(fingerprint: str, duration: int, limit: int = 10):
        rec_id = fp_to_rec[fingerprint]
        return AcoustIdLookupResult(
            acoustid_id=f"acid-{fingerprint}",
            recordings=[
                {
                    "id": rec_id,
                    "title": f"Track for {rec_id}",
                    "length": 200_000,
                    "score": 100,
                    "releases": [release],
                }
            ],
        )

    mb_client = MagicMock()
    mb_client.get_release.return_value = release
    acoustid_client = MagicMock()
    acoustid_client.lookup_by_fingerprint.side_effect = fake_acoustid_lookup

    service = StandaloneTaggingService(client=mb_client, acoustid_client=acoustid_client)
    result = service.lookup(
        items=[
            LookupItem(source_id=f"f{i}", fingerprint=f"fp{i}", duration=200)
            for i in (1, 2, 3)
        ],
        joint=False,
        preferred_countries=["DE"],
        thresholds=Thresholds(min_per_file_score=0.3, min_coverage=0.5, split_margin=0.15),
        search_limit=5,
    )
    assert result.mode == "per-file"
    assert len(result.assignments) == 1
    assignment = result.assignments[0]
    assert assignment.release_id == "rel-Z"
    assert len(assignment.tracks) == 3
    assert [f.source_id for f in assignment.tracks] == ["f1", "f2", "f3"]
    assert result.unmatched == ()


def test_lookup_per_file_match_produces_single_file_assignment() -> None:
    release = _release_dict("rel-Z", ["rec-1"])
    acoustid_client = MagicMock()
    acoustid_client.lookup_by_fingerprint.return_value = AcoustIdLookupResult(
        acoustid_id="acid-fp1",
        recordings=[
            {
                "id": "rec-1",
                "title": "Track 1",
                "length": 200_000,
                "score": 100,
                "releases": [release],
            }
        ],
    )
    mb_client = MagicMock()
    mb_client.get_release.return_value = release

    service = StandaloneTaggingService(client=mb_client, acoustid_client=acoustid_client)
    result = service.lookup(
        items=[LookupItem(source_id="f1", fingerprint="fp1", duration=200)],
        joint=False,
        preferred_countries=["DE"],
        thresholds=Thresholds(min_per_file_score=0.3, min_coverage=0.5, split_margin=0.15),
        search_limit=5,
    )
    assert result.mode == "per-file"
    assert len(result.assignments) == 1
    assert isinstance(result.assignments[0], MaterialisedRelease)
    assert len(result.assignments[0].tracks) == 1
    assert result.assignments[0].tracks[0].source_id == "f1"
    assert result.assignments[0].tracks[0].acoustid_id == "acid-fp1"
    assert result.unmatched == ()
