from __future__ import annotations

import hmac
import json
import logging
import os
import time
import traceback
import urllib.error
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__, ratecontrol
from .joint_matcher import Thresholds
from .models import (
    ArtistCredit,
    AudioMetadata,
    CoverArt,
    Performer,
    ReleaseCredits,
    TrackCredits,
    Work,
)
from .service import (
    DiscLookupResult,
    LookupItem,
    LookupResult,
    StandaloneTaggingService,
)

load_dotenv(override=True)

logger = logging.getLogger("tagging_ms.api")
access_log = logging.getLogger("tagging_ms.access")

# TAGGING_MS_ENV=production turns off the interactive API docs, trims
# /api/version and keeps tracebacks out of responses. Anything internet-facing
# runs with it set (deploy/compose.yml does).
PRODUCTION = os.getenv("TAGGING_MS_ENV", "development").strip().lower() == "production"

# Request-size limits. These bound memory per request; they are DoS controls,
# not quotas. A default fpcalc fingerprint (120 s) is ~3.5k characters and a
# double CD is ~40 tracks, so both caps leave real headroom.
MAX_ITEMS = 64
MAX_FINGERPRINT_CHARS = 16_000
MAX_BODY_BYTES = 2 * 1024 * 1024

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
        {"name": "disc", "description": "CD DiscID/TOC lookup (no fingerprint)."},
    ],
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)

service = StandaloneTaggingService()
bearer_scheme = HTTPBearer(auto_error=False)


def _configured_keys() -> dict[str, str]:
    """Bearer keys as ``{key: label}``.

    ``TAGGING_MS_API_KEYS`` holds ``label=key`` pairs separated by commas, one
    per client, so a single leaked key can be revoked without touching the
    others. ``TAGGING_MS_API_KEY`` is still honoured as one key labelled
    ``default`` for local development. Read per request so a key rotation is
    a restart away, not a rebuild.
    """
    keys: dict[str, str] = {}
    for pair in os.getenv("TAGGING_MS_API_KEYS", "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        label, sep, key = (part.strip() for part in pair.partition("="))
        if not sep or not label or not key:
            raise ValueError(f"TAGGING_MS_API_KEYS entry is not label=key: {pair!r}")
        keys[key] = label
    single = os.getenv("TAGGING_MS_API_KEY", "").strip()
    if single:
        keys[single] = "default"
    return keys


def require_bearer(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
) -> str:
    """Authenticate the request; returns the client label and stores it on ``request.state``."""
    try:
        keys = _configured_keys()
    except ValueError as exc:
        logger.error("%s", exc)
        keys = {}
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No API keys are configured",
        )
    if creds is not None:
        for key, label in keys.items():
            if hmac.compare_digest(creds.credentials, key):
                request.state.key_label = label
                return label
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _internal_error(exc: BaseException) -> HTTPException:
    """Build the 500 for an unexpected failure; call from inside an ``except``.

    The traceback always goes to the log under a correlation id. In production
    the response carries only that id; in development it also carries the
    traceback for convenience.
    """
    correlation_id = uuid.uuid4().hex[:12]
    logger.exception("internal error [%s]", correlation_id)
    detail: dict[str, str] = {"error": "internal error", "correlation_id": correlation_id}
    if not PRODUCTION:
        detail["error"] = str(exc)
        detail["traceback"] = traceback.format_exc()
    return HTTPException(status_code=500, detail=detail)


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all so nothing outside the handlers' own try blocks leaks a traceback."""
    correlation_id = uuid.uuid4().hex[:12]
    logger.error("unhandled error [%s] on %s", correlation_id, request.url.path, exc_info=exc)
    detail: dict[str, str] = {"error": "internal error", "correlation_id": correlation_id}
    if not PRODUCTION:
        detail["error"] = str(exc)
        detail["traceback"] = "".join(traceback.format_exception(exc))
    return JSONResponse(status_code=500, content={"detail": detail})


@app.middleware("http")
async def _access_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One JSON line per request: who, what, outcome, latency, upstream cost.

    ``key_label`` and ``item_count`` are filled in by the auth dependency and
    the handlers via ``request.state``; ``upstream_*`` come from the rate
    limiter's per-request counters.
    """
    stats: dict[str, int] = {}
    token = ratecontrol.REQUEST_STATS.set(stats)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        ratecontrol.REQUEST_STATS.reset(token)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "key": getattr(request.state, "key_label", None),
            "ip": request.headers.get("cf-connecting-ip")
            or (request.client.host if request.client else None),
            "method": request.method,
            "path": request.url.path,
            "items": getattr(request.state, "item_count", None),
            "status": status_code,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "upstream_requests": stats.get("upstream_requests", 0),
            "upstream_retries": stats.get("upstream_retries", 0),
        }
        access_log.info(json.dumps(record, separators=(",", ":")))


@app.middleware("http")
async def _limit_body_size(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject oversized bodies before they are read. Chunked uploads without a
    Content-Length are refused outright; every legitimate client sends one."""
    if request.method in ("POST", "PUT", "PATCH"):
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return JSONResponse(status_code=411, content={"detail": "Content-Length required"})
        if int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body exceeds {MAX_BODY_BYTES} bytes"},
            )
    return await call_next(request)


# ----- Schemas -----


class HealthResponse(BaseModel):
    status: str = Field(description="Static service status string.")

    model_config = {"json_schema_extra": {"example": {"status": "ok"}}}


class VersionResponse(BaseModel):
    name: str
    version: str
    git_sha: str | None = Field(default=None, description="Omitted in production.")

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
    fingerprint: str = Field(
        min_length=1,
        max_length=MAX_FINGERPRINT_CHARS,
        description="Chromaprint fingerprint string (`fpcalc -json`).",
    )
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
        max_length=MAX_ITEMS,
        description=f"Files to resolve in this batch (at most {MAX_ITEMS}).",
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


class CoverArtPayload(BaseModel):
    front: bool = False
    back: bool = False
    count: int = 0
    artwork: bool = False
    darkened: bool = False


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
    language: str | None = None
    cover_art: CoverArtPayload | None = Field(
        default=None,
        # Serialize under MusicBrainz's own key so the block matches the raw
        # release JSON. FastAPI emits responses with by_alias=True by default.
        alias="cover-art-archive",
        description=(
            "MusicBrainz `cover-art-archive` availability block. Fetch the image "
            "on demand from coverartarchive.org/release/{musicbrainz_id}/front."
        ),
    )
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
    instruments: list[PerformerPayload] = Field(default_factory=list)

    model_config = {"extra": "ignore", "populate_by_name": True}


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
    instruments: list[PerformerPayload] = Field(default_factory=list)
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


class DiscLookupRequest(BaseModel):
    discid: str = Field(
        default="-",
        description="MusicBrainz DiscID, or '-' for a TOC-only fuzzy lookup.",
    )
    toc: str = Field(
        default="",
        description=(
            "MusicBrainz `toc` string (e.g. `1+12+267257+150+...`). Required "
            "unless a concrete `discid` is given."
        ),
    )
    barcode: str = Field(
        default="",
        description=(
            "Release barcode (EAN/UPC) to find the release by instead of a "
            "disc TOC. Mutually exclusive with `discid`/`toc` and with "
            "`catalog_number`."
        ),
    )
    catalog_number: str = Field(
        default="",
        description=(
            "Label catalogue number (MusicBrainz `catalog-number`) to find "
            "the release by instead of a disc TOC. Mutually exclusive with "
            "`discid`/`toc` and with `barcode`."
        ),
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description="Ordered ISO-3166-1 codes used to pick among matching pressings.",
    )
    metadata: AudioMetadataPayload | None = Field(
        default=None,
        description="Optional; ranks candidate pressings when re-tagging an existing rip.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "discid": "-",
                "toc": "1+12+267257+150+22767+41887+58317+72102+91375+104652+115380+132165+143932+159870+174597",
                "preferred_release_countries": ["DE", "XE", "XW"],
            }
        }
    }


class DiscCandidatePayload(BaseModel):
    release_id: str
    title: str
    artist: str
    country: str
    date: str
    barcode: str
    track_count: int


class DiscTrackPayload(BaseModel):
    track_id: str
    recording_id: str
    metadata: TrackMetadataPayload


class DiscReleasePayload(BaseModel):
    release_id: str
    discnumber: str | None = Field(
        default=None,
        description=(
            "Position of the medium the disc TOC/DiscID matched within the "
            "release. Filter `tracks` on `metadata.discnumber == discnumber` "
            "to get the tracks of the physical disc. Null for barcode/"
            "catalog_number lookups, which match a release, not a disc."
        ),
    )
    totaldiscs: str | None = Field(
        default=None,
        description="Number of media in the release.",
    )
    metadata: ReleaseMetadataPayload
    tracks: list[DiscTrackPayload]


class DiscLookupResponse(BaseModel):
    release: DiscReleasePayload | None = Field(
        default=None,
        description="Best-matching release fully materialised, or null if none matched.",
    )
    candidates: list[DiscCandidatePayload] = Field(
        default_factory=list,
        description="All releases matching the disc, as lightweight summaries.",
    )
    reason: str | None = Field(
        default=None,
        description="Why no release was returned (when `release` is null).",
    )


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
    response_model_exclude_none=True,
    summary="Service version",
)
def version() -> dict[str, str]:
    info = {"name": "tagging-ms", "version": __version__}
    if not PRODUCTION:
        info["git_sha"] = os.getenv("GIT_SHA", "unknown")
    return info


@app.post(
    "/api/lookup",
    tags=["lookup"],
    response_model=LookupResponse,
    dependencies=[Depends(require_bearer)],
    summary="Joint or per-file AcoustID lookup",
)
def lookup(req: LookupRequest, request: Request) -> dict:
    request.state.item_count = len(req.items)
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
        raise _internal_error(exc) from exc

    return _serialize_lookup_result(result)


@app.post(
    "/api/disc",
    tags=["disc"],
    response_model=DiscLookupResponse,
    dependencies=[Depends(require_bearer)],
    summary="CD DiscID/TOC lookup",
)
def lookup_disc(req: DiscLookupRequest) -> dict:
    barcode = req.barcode.strip()
    catalog_number = req.catalog_number.strip()
    has_disc = req.discid.strip() not in ("", "-") or bool(req.toc.strip())
    if barcode and catalog_number:
        raise HTTPException(
            status_code=400,
            detail="barcode and catalog_number are mutually exclusive.",
        )
    if (barcode or catalog_number) and has_disc:
        raise HTTPException(
            status_code=400,
            detail=(
                "barcode/catalog_number and DiscID/TOC are mutually exclusive."
            ),
        )
    if not (barcode or catalog_number or has_disc):
        raise HTTPException(
            status_code=400,
            detail="Provide a DiscID or a TOC (or both), or a barcode or catalog_number.",
        )
    try:
        result = service.lookup_disc(
            discid=req.discid,
            toc=req.toc,
            preferred_countries=req.preferred_release_countries,
            metadata=(
                AudioMetadata(**req.metadata.model_dump()) if req.metadata else None
            ),
            barcode=barcode,
            catalog_number=catalog_number,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise HTTPException(
                status_code=400, detail="Invalid DiscID or TOC"
            ) from exc
        raise _internal_error(exc) from exc
    except Exception as exc:
        raise _internal_error(exc) from exc

    return _serialize_disc_result(result)


def _serialize_lookup_result(result: LookupResult) -> dict:
    return {
        "mode": result.mode,
        "assignments": [
            {
                "release_id": rel.release_id,
                "score": rel.score,
                "metadata": {
                    **_serialize_release_tags(rel.applied_release_tags),
                    "cover-art-archive": _serialize_cover_art(rel.cover_art),
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


def _serialize_disc_result(result: DiscLookupResult) -> dict:
    release: dict | None = None
    if result.release is not None:
        rel = result.release
        release = {
            "release_id": rel.release_id,
            "discnumber": rel.discnumber or None,
            "totaldiscs": rel.totaldiscs or None,
            "metadata": {
                **_serialize_release_tags(rel.applied_release_tags),
                "cover_art": _serialize_cover_art(rel.cover_art),
                "artists": [
                    _serialize_artist_credit(ac) for ac in rel.release_artists
                ],
                **_serialize_release_credits(rel.release_credits),
            },
            "tracks": [
                {
                    "track_id": t.track_id,
                    "recording_id": t.recording_id,
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
    return {
        "release": release,
        "candidates": [
            {
                "release_id": c.release_id,
                "title": c.title,
                "artist": c.artist,
                "country": c.country,
                "date": c.date,
                "barcode": c.barcode,
                "track_count": c.track_count,
            }
            for c in result.candidates
        ],
        "reason": result.reason,
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
        "instruments": [_serialize_performer(p) for p in c.instruments],
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
        "instruments": [_serialize_performer(p) for p in c.instruments],
    }


def _serialize_cover_art(c: CoverArt | None) -> dict[str, object] | None:
    if c is None:
        return None
    return {
        "front": c.front,
        "back": c.back,
        "count": c.count,
        "artwork": c.artwork,
        "darkened": c.darkened,
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
