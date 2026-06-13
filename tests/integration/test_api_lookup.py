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
    # Release-level cover-art availability block, forwarded from MusicBrainz
    # (no separate Cover Art Archive request).
    cover_art = release_md.get("cover_art")
    assert cover_art is not None
    assert cover_art["front"] is True
    assert cover_art["count"] >= 1
    assert {"front", "back", "count", "artwork", "darkened"} <= cover_art.keys()

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
    # Cover art is release-level only — not duplicated onto every track.
    assert "cover_art" not in track_md
    # acoustid_id is top-level on each matched track, not in metadata.
    assert sample_track["acoustid_id"]
    # Score reflects real AcoustID fingerprint confidence, not a hardcoded 1.0.
    assert 0.0 < sample_track["score"] <= 1.0

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


# ----- Disc (DiscID/TOC) lookup -----

# Verified TOC for Nirvana — Nevermind (CD, 12 tracks).
_NEVERMIND_TOC = (
    "1+12+267257+150+22767+41887+58317+72102+91375"
    "+104652+115380+132165+143932+159870+174597"
)


def test_disc_lookup_requires_bearer(client: TestClient) -> None:
    res = client.post("/api/disc", json={"toc": _NEVERMIND_TOC})
    assert res.status_code == 401


def test_disc_lookup_requires_discid_or_toc(client: TestClient) -> None:
    res = client.post(
        "/api/disc",
        json={"discid": "-", "toc": ""},
        headers={"Authorization": "Bearer test-bearer-token"},
    )
    assert res.status_code == 400


@pytest.mark.vcr(
    cassette_library_dir=str(CASSETTE_DIR),
)
def test_disc_lookup_returns_release_and_candidates(client: TestClient) -> None:
    res = client.post(
        "/api/disc",
        json={
            "discid": "-",
            "toc": _NEVERMIND_TOC,
            "preferred_release_countries": ["DE", "XE", "XW"],
        },
        headers={"Authorization": "Bearer test-bearer-token"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Lightweight candidate list of all matching pressings.
    assert len(body["candidates"]) > 1
    first_candidate = body["candidates"][0]
    assert first_candidate["release_id"]
    assert first_candidate["title"] == "Nevermind"
    assert first_candidate["artist"] == "Nirvana"
    assert first_candidate["track_count"] == 12

    # Best release fully materialised — same rich shape as /api/lookup.
    rel = body["release"]
    assert rel is not None
    assert body["reason"] is None
    assert rel["release_id"]
    assert rel["metadata"]["title"] == "Nevermind"
    assert rel["metadata"]["artists"][0]["name"] == "Nirvana"
    cover_art = rel["metadata"].get("cover_art")
    assert cover_art is not None
    assert cover_art["front"] is True
    assert len(rel["tracks"]) == 12
    track = rel["tracks"][0]
    assert track.get("title")
    assert track["artists"][0]["name"] == "Nirvana"
    # No file-match fields leak into the disc response.
    assert "score" not in track
    assert "source_id" not in track
