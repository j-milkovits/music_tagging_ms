"""Integration tests for /api/lookup against recorded HTTP cassettes.

Cassettes live next to this file (tests/integration/cassettes/). Re-record by
deleting the cassette and running with --record-mode=once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tagging_ms.api import app

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def carmen_items(fingerprints: list[dict]) -> list[dict]:
    """Use first 3 Carmen tracks for fast cassette playback."""
    return [
        {
            "source_id": fp["source_id"],
            "fingerprint": fp["fingerprint"],
            "duration": fp["duration"],
        }
        for fp in fingerprints[:3]
    ]


# ----- Auth -----


def test_health_unauthenticated(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_version_unauthenticated(client: TestClient) -> None:
    res = client.get("/api/version")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "tagging-ms"
    assert body["version"]
    assert body["git_sha"]


def test_lookup_requires_bearer(client: TestClient, carmen_items: list[dict]) -> None:
    res = client.post("/api/lookup", json={"items": carmen_items, "joint": True})
    assert res.status_code == 401


def test_lookup_rejects_wrong_bearer(client: TestClient, carmen_items: list[dict]) -> None:
    res = client.post(
        "/api/lookup",
        json={"items": carmen_items, "joint": True},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 401


# ----- Lookup (cassette-replayed) -----


@pytest.mark.vcr(
    cassette_library_dir=str(CASSETTE_DIR),
)
def test_lookup_joint_carmen(client: TestClient, carmen_items: list[dict]) -> None:
    res = client.post(
        "/api/lookup",
        json={
            "items": carmen_items,
            "joint": True,
            "preferred_release_countries": ["DE", "XE", "XW"],
            "thresholds": {
                "min_per_file_score": 0.3,
                "min_coverage": 0.5,
                "split_margin": 0.15,
            },
            "search_limit": 5,
        },
        headers={"Authorization": "Bearer test-bearer-token"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "joint"
    # Joint matcher should land all 3 tracks on a single release.
    assert len(body["assignments"]) == 1
    rel = body["assignments"][0]
    assert rel["release_id"]
    assert rel["score"] > 0
    assert len(rel["tracks"]) == 3
    sids = {t["source_id"] for t in rel["tracks"]}
    assert sids == {"track-01", "track-02", "track-03"}

    # Release-level metadata. Flat artist strings/IDs are intentionally absent —
    # clients reconstruct them from the structured `artists` array nested
    # inside `metadata`.
    release_md = rel["metadata"]
    assert release_md.get("title")
    assert "release_artist" not in release_md
    assert "musicbrainz_release_artist_id" not in release_md
    assert release_md.get("label")
    assert release_md.get("barcode")
    assert release_md.get("musicbrainz_id")

    sample_track = rel["tracks"][0]
    track_md = sample_track["metadata"]
    assert track_md.get("title")
    assert "artist" not in track_md
    assert "musicbrainz_artistid" not in track_md
    assert track_md.get("tracknumber")
    assert track_md.get("musicbrainz_trackid")
    assert track_md.get("musicbrainz_recordingid")
    # Structured works array (replaces flat musicbrainz_workid).
    assert track_md["works"]
    assert any(w.get("musicbrainz_id") for w in track_md["works"])
    assert track_md.get("genre")
    # acoustid_id is top-level on each matched track, not in metadata.
    assert sample_track["acoustid_id"]

    # Structured artist credits live inside the respective metadata block.
    release_artists = release_md["artists"]
    assert len(release_artists) >= 1
    first_ra = release_artists[0]
    assert first_ra["name"]
    assert first_ra["sort_name"]
    assert first_ra["musicbrainz_artistid"]
    # carmen has the release artist as a Person.
    assert any(ac["type"] == "Person" for ac in release_artists)

    assert track_md["artists"]
    first_ta = track_md["artists"][0]
    assert first_ta["name"]

    assert body["unmatched"] == []
    assert body["diagnostics"]["files_in"] == 3
    assert body["diagnostics"]["files_matched"] == 3


@pytest.mark.vcr(
    cassette_library_dir=str(CASSETTE_DIR),
)
def test_lookup_joint_carmen_byte_equal_across_runs(
    client: TestClient, carmen_items: list[dict]
) -> None:
    """Determinism: identical request body → identical response across two calls."""
    payload = {
        "items": carmen_items,
        "joint": True,
        "preferred_release_countries": ["DE", "XE", "XW"],
        "thresholds": {
            "min_per_file_score": 0.3,
            "min_coverage": 0.5,
            "split_margin": 0.15,
        },
        "search_limit": 5,
    }
    headers = {"Authorization": "Bearer test-bearer-token"}
    a = client.post("/api/lookup", json=payload, headers=headers)
    b = client.post("/api/lookup", json=payload, headers=headers)
    assert a.status_code == 200 and b.status_code == 200
    assert json.dumps(a.json(), sort_keys=True) == json.dumps(b.json(), sort_keys=True)


@pytest.mark.vcr(
    cassette_library_dir=str(CASSETTE_DIR),
)
def test_lookup_per_file_carmen(client: TestClient, carmen_items: list[dict]) -> None:
    res = client.post(
        "/api/lookup",
        json={
            "items": carmen_items,
            "joint": False,
            "preferred_release_countries": ["DE", "XE", "XW"],
            "thresholds": {
                "min_per_file_score": 0.3,
                "min_coverage": 0.0,
                "split_margin": 0.0,
            },
            "search_limit": 5,
        },
        headers={"Authorization": "Bearer test-bearer-token"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "per-file"
    # Per-file mode resolves each file independently. Files that happen to
    # pick the same release are grouped into one assignment, but per-file
    # may scatter the carmen tracks across multiple release editions
    # (the joint matcher would have forced them onto a single release).
    assert len(body["assignments"]) >= 1
    total_tracks = sum(len(a["tracks"]) for a in body["assignments"])
    assert total_tracks == 3
    sids = {t["source_id"] for a in body["assignments"] for t in a["tracks"]}
    assert sids == {"track-01", "track-02", "track-03"}
    for asgn in body["assignments"]:
        assert asgn["score"] > 0
        assert asgn["metadata"].get("title")
        assert len(asgn["metadata"]["artists"]) >= 1
        assert asgn["metadata"]["artists"][0].get("name")
        for track in asgn["tracks"]:
            assert track["metadata"].get("title")
            assert track["metadata"]["artists"]
    assert body["unmatched"] == []
    assert body["diagnostics"]["files_matched"] == 3
