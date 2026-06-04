from __future__ import annotations

import hmac
import logging
import os
import traceback
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__
from .joint_matcher import Thresholds
from .models import (
    ArtistCredit,
    AudioMetadata,
    Performer,
    ReleaseCredits,
    TrackCredits,
    Work,
)
from .service import LookupItem, LookupResult, StandaloneTaggingService

load_dotenv(override=True)

logger = logging.getLogger("tagging_ms.api")

app = FastAPI(
    title="Tagging Microservice",
    version=__version__,
    description=(
        "HTTP API for joint AcoustID/MusicBrainz release matching. "
        "Submit one or more files (Chromaprint fingerprint + duration) to "
        "/api/lookup. Toggle the `joint` flag to switch between joint release "
        "matching (stage-1 release selection + stage-2 bipartite track "
        "assignment) and independent per-file matching."
    ),
    openapi_tags=[
        {"name": "health", "description": "Service health and version probes."},
        {"name": "lookup", "description": "Joint or per-file AcoustID lookup."},
    ],
)

service = StandaloneTaggingService()
bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
) -> None:
    expected = os.getenv("TAGGING_MS_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TAGGING_MS_API_KEY is not configured",
        )
    if creds is None or not hmac.compare_digest(creds.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ----- Schemas -----


class HealthResponse(BaseModel):
    status: str = Field(description="Static service status string.")

    model_config = {"json_schema_extra": {"example": {"status": "ok"}}}


class VersionResponse(BaseModel):
    name: str
    version: str
    git_sha: str

    model_config = {
        "json_schema_extra": {
            "example": {"name": "tagging-ms", "version": "0.2.0", "git_sha": "abc1234"}
        }
    }


class AudioMetadataPayload(BaseModel):
    title: str = Field(default="", description="Track title.")
    artist: str = Field(default="", description="Track artist credit.")
    release: str = Field(default="", description="Release title.")
    release_artist: str = Field(default="", description="Release artist credit.")
    tracknumber: str = Field(default="")
    totaltracks: str = Field(default="")
    discnumber: str = Field(default="")
    totaldiscs: str = Field(default="")
    date: str = Field(default="")
    isrc: str = Field(default="")
    release_country: str = Field(default="")
    release_type: str = Field(default="", description="Release type, e.g. album, single.")
    media: str = Field(default="", description="Medium label such as CD or Digital Media.")
    format_name: str = Field(default="")
    is_video: bool = Field(default=False)
    length_ms: int = Field(default=0, ge=0)


class LookupItemPayload(BaseModel):
    source_id: str = Field(description="Caller-defined identifier echoed in the response.")
    fingerprint: str = Field(description="Chromaprint fingerprint string (`fpcalc -json`).")
    duration: int = Field(gt=0, description="Fingerprint duration in whole seconds.")
    metadata: AudioMetadataPayload | None = Field(
        default=None,
        description="Optional file metadata used to refine joint scoring.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_id": "01.wav",
                "fingerprint": "AQADtNQYRYmSNFFy...",
                "duration": 287,
                "metadata": {
                    "title": "Track 1",
                    "artist": "Artist 1",
                    "release": "Album 1",
                    "length_ms": 287000,
                },
            }
        }
    }


class LookupThresholds(BaseModel):
    min_per_file_score: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum acceptable per-file score for an assignment.",
    )
    min_coverage: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="Fraction of files in a release that must score above min_per_file_score for the release to be accepted (joint mode only).",
    )
    split_margin: float = Field(
        default=0.15, ge=0.0, le=1.0,
        description="A file is held back for the next stage-1 iteration only if its best alternative outscores its primary by this margin (joint mode only).",
    )


class LookupRequest(BaseModel):
    items: list[LookupItemPayload] = Field(
        min_length=1,
        description="Files to resolve in this batch.",
    )
    joint: bool = Field(
        default=True,
        description=(
            "If true, run joint release matching (stage 1 + stage 2). "
            "If false, pick the best release per file independently."
        ),
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered ISO-3166-1 codes used to bias release selection. "
            "Earlier entries score higher; absent codes score zero. "
            "MusicBrainz pseudo-codes XE (Europe) and XW (worldwide) are accepted."
        ),
    )
    thresholds: LookupThresholds = Field(default_factory=LookupThresholds)
    search_limit: int = Field(
        default=10, ge=1, le=100,
        description="Per-file AcoustID candidate limit.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "source_id": "01.wav",
                        "fingerprint": "AQAD...",
                        "duration": 287,
                    },
                    {
                        "source_id": "02.wav",
                        "fingerprint": "AQAD...",
                        "duration": 254,
                    },
                ],
                "joint": True,
                "preferred_release_countries": ["DE", "XE", "XW"],
                "thresholds": {
                    "min_per_file_score": 0.5,
                    "min_coverage": 0.6,
                    "split_margin": 0.15,
                },
                "search_limit": 10,
            }
        }
    }


class ArtistCreditPayload(BaseModel):
    name: str
    sort_name: str = ""
    musicbrainz_artistid: str = ""
    type: str = ""
    disambiguation: str = ""


class PerformerPayload(BaseModel):
    name: str
    sort_name: str = ""
    musicbrainz_artistid: str = ""
    type: str = ""
    disambiguation: str = ""
    attributes: list[str] = Field(default_factory=list)


class WorkPayload(BaseModel):
    title: str
    musicbrainz_id: str = ""


class ReleaseMetadataPayload(BaseModel):
    # The release artist (`release_artist` / `musicbrainz_release_artist_id`) is
    # intentionally absent — clients should reconstruct it from the structured
    # `artists` array below (join `name`s for the display string; collect
    # `musicbrainz_artistid` for the IDs).
    title: str | None = None
    date: str | None = None
    originaldate: str | None = None
    country: str | None = None
    type: str | None = None
    musicbrainz_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    label: str | None = None
    catalognumber: str | None = None
    barcode: str | None = None
    script: str | None = None
    cover_art_url: str | None = None
    cover_art_thumb_url: str | None = None
    artists: list[ArtistCreditPayload] = Field(
        default_factory=list,
        description="Structured release artist credits.",
    )
    producers: list[ArtistCreditPayload] = Field(default_factory=list)
    engineers: list[ArtistCreditPayload] = Field(default_factory=list)
    mixers: list[ArtistCreditPayload] = Field(default_factory=list)
    conductors: list[ArtistCreditPayload] = Field(default_factory=list)
    arrangers: list[ArtistCreditPayload] = Field(default_factory=list)
    performers: list[PerformerPayload] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class TrackMetadataPayload(BaseModel):
    # `artist` and `musicbrainz_artistid` are intentionally absent — clients
    # should reconstruct them from the structured `artists` array below.
    title: str | None = None
    tracknumber: str | None = None
    totaltracks: str | None = None
    discnumber: str | None = None
    totaldiscs: str | None = None
    isrc: str | None = None
    length_ms: int | None = None
    media: str | None = None
    musicbrainz_trackid: str | None = None
    musicbrainz_recordingid: str | None = None
    genre: str | None = None
    artists: list[ArtistCreditPayload] = Field(
        default_factory=list,
        description="Structured track artist credits.",
    )
    composers: list[ArtistCreditPayload] = Field(default_factory=list)
    lyricists: list[ArtistCreditPayload] = Field(default_factory=list)
    writers: list[ArtistCreditPayload] = Field(default_factory=list)
    arrangers: list[ArtistCreditPayload] = Field(default_factory=list)
    producers: list[ArtistCreditPayload] = Field(default_factory=list)
    engineers: list[ArtistCreditPayload] = Field(default_factory=list)
    mixers: list[ArtistCreditPayload] = Field(default_factory=list)
    conductors: list[ArtistCreditPayload] = Field(default_factory=list)
    performers: list[PerformerPayload] = Field(default_factory=list)
    works: list[WorkPayload] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class MatchedTrackPayload(BaseModel):
    source_id: str
    track_id: str
    recording_id: str
    acoustid_id: str | None = None
    score: float
    metadata: TrackMetadataPayload


class AssignmentPayload(BaseModel):
    release_id: str
    score: float = Field(
        description="Mean of `tracks[].score`. Same semantic in joint and per-file modes.",
    )
    metadata: ReleaseMetadataPayload
    tracks: list[MatchedTrackPayload]


class BestGuessPayload(BaseModel):
    release_id: str | None = None
    recording_id: str | None = None
    acoustid_id: str | None = None
    score: float


class UnmatchedPayload(BaseModel):
    source_id: str
    reason: str
    best_guess: BestGuessPayload | None = None


class LookupDiagnostics(BaseModel):
    candidate_releases_considered: int
    split_count: int
    files_in: int
    files_matched: int


class LookupResponse(BaseModel):
    mode: Literal["joint", "per-file"]
    assignments: list[AssignmentPayload] = Field(
        description=(
            "Successfully matched files grouped by release. Each assignment "
            "carries release-level `metadata` plus a `tracks` array, where "
            "each matched track has its own `metadata`. Per-file mode "
            "produces 1-track assignments."
        ),
    )
    unmatched: list[UnmatchedPayload] = Field(
        description="Files no release could claim. Each carries a `reason` and an optional `best_guess`.",
    )
    diagnostics: LookupDiagnostics


# ----- Endpoints -----


@app.get(
    "/api/health",
    tags=["health"],
    response_model=HealthResponse,
    summary="Health check",
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/version",
    tags=["health"],
    response_model=VersionResponse,
    summary="Service version",
)
def version() -> dict[str, str]:
    return {
        "name": "tagging-ms",
        "version": __version__,
        "git_sha": os.getenv("GIT_SHA", "unknown"),
    }


@app.post(
    "/api/lookup",
    tags=["lookup"],
    response_model=LookupResponse,
    dependencies=[Depends(require_bearer)],
    summary="Joint or per-file AcoustID lookup",
)
def lookup(req: LookupRequest) -> dict:
    try:
        items = [
            LookupItem(
                source_id=item.source_id,
                fingerprint=item.fingerprint,
                duration=item.duration,
                metadata=AudioMetadata(**item.metadata.model_dump()) if item.metadata else None,
            )
            for item in req.items
        ]
        result = service.lookup(
            items=items,
            joint=req.joint,
            preferred_countries=req.preferred_release_countries,
            thresholds=Thresholds(
                min_per_file_score=req.thresholds.min_per_file_score,
                min_coverage=req.thresholds.min_coverage,
                split_margin=req.thresholds.split_margin,
            ),
            search_limit=req.search_limit,
        )
    except Exception as exc:
        logger.exception("lookup failed")
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc

    return _serialize_lookup_result(result)


def _serialize_lookup_result(result: LookupResult) -> dict:
    return {
        "mode": result.mode,
        "assignments": [
            {
                "release_id": rel.release_id,
                "score": rel.score,
                "metadata": {
                    **_serialize_release_tags(rel.applied_release_tags),
                    "artists": [
                        _serialize_artist_credit(ac) for ac in rel.release_artists
                    ],
                    **_serialize_release_credits(rel.release_credits),
                },
                "tracks": [
                    {
                        "source_id": t.source_id,
                        "track_id": t.track_id,
                        "recording_id": t.recording_id,
                        "acoustid_id": t.acoustid_id,
                        "score": t.score,
                        "metadata": {
                            **_serialize_applied_tags(t.applied_track_tags),
                            "artists": [
                                _serialize_artist_credit(ac) for ac in t.artists
                            ],
                            **_serialize_track_credits(t.credits),
                        },
                    }
                    for t in rel.tracks
                ],
            }
            for rel in result.assignments
        ],
        "unmatched": [
            {
                "source_id": u.source_id,
                "reason": u.reason,
                "best_guess": (
                    {
                        "release_id": u.best_guess.release_id,
                        "recording_id": u.best_guess.recording_id,
                        "acoustid_id": u.best_guess.acoustid_id,
                        "score": u.best_guess.score,
                    }
                    if u.best_guess is not None
                    else None
                ),
            }
            for u in result.unmatched
        ],
        "diagnostics": result.diagnostics,
    }


def _serialize_artist_credit(ac: ArtistCredit) -> dict[str, str]:
    return {
        "name": ac.name,
        "sort_name": ac.sort_name,
        "musicbrainz_artistid": ac.musicbrainz_artistid,
        "type": ac.type,
        "disambiguation": ac.disambiguation,
    }


def _serialize_performer(p: Performer) -> dict[str, object]:
    return {
        "name": p.name,
        "sort_name": p.sort_name,
        "musicbrainz_artistid": p.musicbrainz_artistid,
        "type": p.type,
        "disambiguation": p.disambiguation,
        "attributes": list(p.attributes),
    }


def _serialize_work(w: Work) -> dict[str, str]:
    return {"title": w.title, "musicbrainz_id": w.musicbrainz_id}


def _serialize_track_credits(c: TrackCredits) -> dict[str, list]:
    return {
        "composers": [_serialize_artist_credit(a) for a in c.composers],
        "lyricists": [_serialize_artist_credit(a) for a in c.lyricists],
        "writers": [_serialize_artist_credit(a) for a in c.writers],
        "arrangers": [_serialize_artist_credit(a) for a in c.arrangers],
        "producers": [_serialize_artist_credit(a) for a in c.producers],
        "engineers": [_serialize_artist_credit(a) for a in c.engineers],
        "mixers": [_serialize_artist_credit(a) for a in c.mixers],
        "conductors": [_serialize_artist_credit(a) for a in c.conductors],
        "performers": [_serialize_performer(p) for p in c.performers],
        "works": [_serialize_work(w) for w in c.works],
    }


def _serialize_release_credits(c: ReleaseCredits) -> dict[str, list]:
    return {
        "producers": [_serialize_artist_credit(a) for a in c.producers],
        "engineers": [_serialize_artist_credit(a) for a in c.engineers],
        "mixers": [_serialize_artist_credit(a) for a in c.mixers],
        "conductors": [_serialize_artist_credit(a) for a in c.conductors],
        "arrangers": [_serialize_artist_credit(a) for a in c.arrangers],
        "performers": [_serialize_performer(p) for p in c.performers],
    }


def _serialize_applied_tags(tags: dict[str, str]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in tags.items():
        if key == "length_ms":
            try:
                payload[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif key == "is_video":
            payload[key] = str(value).strip().lower() in {"1", "true", "yes"}
        else:
            payload[key] = value
    return payload


# Map flat AudioMetadata release-tag keys to the de-prefixed keys exposed in
# ReleaseMetadataPayload (the metadata object is itself a release, so the
# `release_` prefix would be redundant).
_RELEASE_TAG_RENAMES: dict[str, str] = {
    "release": "title",
    "release_country": "country",
    "release_type": "type",
    "musicbrainz_release_id": "musicbrainz_id",
}


def _serialize_release_tags(tags: dict[str, str]) -> dict[str, object]:
    raw = _serialize_applied_tags(tags)
    return {_RELEASE_TAG_RENAMES.get(key, key): value for key, value in raw.items()}
