from __future__ import annotations

import traceback
from dataclasses import asdict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .models import AudioMetadata, InputFile
from .service import StandaloneTaggingService

load_dotenv(override=True)

app = FastAPI(
    title="Picard Matching Microservice",
    version="0.1.0",
    description=(
        "HTTP API for MusicBrainz and AcoustID based track matching. "
        "The service accepts client-extracted metadata and optional acoustic "
        "fingerprints, resolves candidate recordings, and returns the tags "
        "that should be applied locally."
    ),
    openapi_tags=[
        {"name": "health", "description": "Service health and readiness checks."},
        {
            "name": "metadata",
            "description": (
                "Lookup endpoints that use only structured file metadata and "
                "query MusicBrainz directly."
            ),
        },
        {
            "name": "acoustid",
            "description": (
                "Lookup endpoints that use AcoustID fingerprints and duration "
                "without additional metadata ranking."
            ),
        },
        {
            "name": "hybrid",
            "description": (
                "Lookup endpoints that use AcoustID for candidate generation "
                "and metadata for final ranking."
            ),
        },
    ],
)
service = StandaloneTaggingService()

METADATA_EXAMPLE = {
    "title": "Song 1",
    "artist": "Artist 1",
    "album": "Album 1",
    "albumartist": "Artist 1",
    "tracknumber": "1",
    "totaltracks": "10",
    "discnumber": "1",
    "totaldiscs": "1",
    "date": "2024-05-01",
    "isrc": "USRC17607839",
    "releasecountry": "DE",
    "releasetype": "album",
    "media": "Digital Media",
    "format_name": "WAVE",
    "is_video": False,
    "length_ms": 190000,
    "musicbrainz_albumid": "",
    "musicbrainz_trackid": "",
    "musicbrainz_recordingid": "",
    "musicbrainz_releasegroupid": "",
    "musicbrainz_artistid": "",
    "musicbrainz_albumartistid": "",
    "musicbrainz_workid": "",
    "label": "",
    "catalognumber": "",
    "barcode": "",
    "script": "",
    "originaldate": "",
    "work": "",
    "composer": "",
    "lyricist": "",
    "writer": "",
    "arranger": "",
    "producer": "",
    "engineer": "",
    "mixer": "",
    "conductor": "",
    "performers": "",
}


class ErrorDetailResponse(BaseModel):
    error: str = Field(description="Human-readable application error message.")
    traceback: str | None = Field(
        default=None,
        description="Development traceback text when included by the service.",
    )

    model_config = {
        "title": "ErrorDetailResponse",
        "json_schema_extra": {
            "example": {
                "error": "AcoustID lookup requires a non-empty fingerprint",
                "traceback": "Traceback ...",
            }
        },
    }


class ErrorResponse(BaseModel):
    detail: ErrorDetailResponse

    model_config = {
        "title": "ErrorResponse",
        "json_schema_extra": {
            "example": {
                "detail": ErrorDetailResponse.model_config["json_schema_extra"][
                    "example"
                ]
            }
        },
    }


class AppliedTagsResponse(BaseModel):
    title: str | None = Field(default=None, description="Track title to write.")
    artist: str | None = Field(
        default=None, description="Track artist credit to write."
    )
    album: str | None = Field(default=None, description="Release title to write.")
    albumartist: str | None = Field(
        default=None, description="Album artist credit to write."
    )
    tracknumber: str | None = Field(default=None, description="Track number to write.")
    totaltracks: str | None = Field(
        default=None, description="Total track count to write."
    )
    discnumber: str | None = Field(default=None, description="Disc number to write.")
    totaldiscs: str | None = Field(
        default=None, description="Total disc count to write."
    )
    date: str | None = Field(default=None, description="Release date to write.")
    isrc: str | None = Field(default=None, description="ISRC to write.")
    releasecountry: str | None = Field(
        default=None, description="Release country to write."
    )
    releasetype: str | None = Field(default=None, description="Release type to write.")
    media: str | None = Field(default=None, description="Medium label to write.")
    format_name: str | None = Field(
        default=None,
        description="Detected format name returned for reference in the match result.",
    )
    is_video: bool | None = Field(
        default=None,
        description="Whether the matched track is marked as video.",
    )
    length_ms: int | None = Field(
        default=None,
        description="Matched track length in milliseconds.",
    )
    musicbrainz_albumid: str | None = Field(
        default=None, description="MusicBrainz release MBID to write."
    )
    musicbrainz_trackid: str | None = Field(
        default=None, description="MusicBrainz track MBID to write."
    )
    musicbrainz_recordingid: str | None = Field(
        default=None, description="MusicBrainz recording MBID to write."
    )
    musicbrainz_releasegroupid: str | None = Field(
        default=None, description="MusicBrainz release-group MBID to write."
    )
    musicbrainz_artistid: str | None = Field(
        default=None,
        description="MusicBrainz artist MBID(s) for the track artist to write. Multiple IDs separated by '; '.",
    )
    musicbrainz_albumartistid: str | None = Field(
        default=None,
        description="MusicBrainz artist MBID(s) for the album artist to write. Multiple IDs separated by '; '.",
    )
    musicbrainz_workid: str | None = Field(
        default=None,
        description="MusicBrainz work MBID(s) linked via performance relationship. Multiple IDs separated by '; '.",
    )
    acoustid_id: str | None = Field(
        default=None,
        description="Resolved AcoustID identifier to write when available.",
    )
    label: str | None = Field(
        default=None, description="Record label name(s) to write. Multiple labels separated by '; '."
    )
    catalognumber: str | None = Field(
        default=None, description="Release catalog number(s) to write. Multiple values separated by '; '."
    )
    barcode: str | None = Field(
        default=None, description="Release barcode (EAN/UPC) to write."
    )
    script: str | None = Field(
        default=None, description="Script of the release text (e.g. Latn, Cyrl)."
    )
    originaldate: str | None = Field(
        default=None, description="Earliest known release date of the release group to write."
    )
    work: str | None = Field(
        default=None,
        description="Title of the linked MusicBrainz work (composition). Multiple titles separated by '; '.",
    )
    composer: str | None = Field(
        default=None, description="Composer(s) from the linked work. Multiple names separated by '; '."
    )
    lyricist: str | None = Field(
        default=None, description="Lyricist(s) from the linked work. Multiple names separated by '; '."
    )
    writer: str | None = Field(
        default=None, description="Writer(s) from the linked work (undifferentiated composer/lyricist). Multiple names separated by '; '."
    )
    arranger: str | None = Field(
        default=None, description="Arranger(s) from recording or work relationships. Multiple names separated by '; '."
    )
    producer: str | None = Field(
        default=None, description="Producer(s) from recording relationships. Multiple names separated by '; '."
    )
    engineer: str | None = Field(
        default=None, description="Engineer(s) from recording relationships. Multiple names separated by '; '."
    )
    mixer: str | None = Field(
        default=None, description="Mix engineer(s) from recording relationships. Multiple names separated by '; '."
    )
    conductor: str | None = Field(
        default=None, description="Conductor(s) from recording relationships. Multiple names separated by '; '."
    )
    performers: str | None = Field(
        default=None,
        description=(
            "Instrument and vocal performers from recording relationships. "
            "Each entry is formatted as 'Name (instrument)' where the instrument is available. "
            "Multiple entries separated by '; '."
        ),
    )

    model_config = {
        "title": "AppliedTagsResponse",
        "json_schema_extra": {
            "example": {
                "title": "Song 1",
                "artist": "Artist 1",
                "album": "Album 1",
                "albumartist": "Artist 1",
                "tracknumber": "1",
                "totaltracks": "10",
                "discnumber": "1",
                "totaldiscs": "1",
                "date": "2024-05-01",
                "isrc": "USRC17607839",
                "releasecountry": "DE",
                "releasetype": "album",
                "media": "Digital Media",
                "format_name": "WAVE",
                "is_video": False,
                "length_ms": 190000,
                "musicbrainz_albumid": "release-mbid",
                "musicbrainz_trackid": "track-mbid",
                "musicbrainz_recordingid": "recording-mbid",
                "musicbrainz_releasegroupid": "release-group-mbid",
                "musicbrainz_artistid": "artist-mbid",
                "musicbrainz_albumartistid": "albumartist-mbid",
                "musicbrainz_workid": "work-mbid",
                "acoustid_id": "27b3fad6-d400-4930-a261-dd994f036244",
                "label": "Some Records",
                "catalognumber": "SR-001",
                "barcode": "5099749994324",
                "script": "Latn",
                "originaldate": "2024-05-01",
                "work": "Song 1",
                "composer": "A. Composer",
                "lyricist": "A. Lyricist",
                "writer": "",
                "arranger": "",
                "producer": "A. Producer",
                "engineer": "A. Engineer",
                "mixer": "A. Mixer",
                "conductor": "",
                "performers": "John Doe (Guitar); Jane Doe (lead vocals)",
            }
        },
    }


class FileAssignmentResponse(BaseModel):
    source_id: str = Field(description="Caller-provided file identifier.")
    matched: bool = Field(description="Whether the lookup produced an accepted match.")
    similarity: float = Field(
        description="Final similarity or ranking score for the selected candidate."
    )
    acoustid_id: str | None = Field(
        default=None,
        description="Resolved AcoustID identifier when the lookup used AcoustID.",
    )
    target_path: str | None = Field(
        default=None,
        description="Optional output path used by local file-writing flows.",
    )
    release_id: str | None = Field(
        default=None, description="Matched MusicBrainz release MBID."
    )
    track_id: str | None = Field(
        default=None, description="Matched MusicBrainz track MBID."
    )
    recording_id: str | None = Field(
        default=None, description="Matched MusicBrainz recording MBID."
    )
    applied_tags: AppliedTagsResponse = Field(
        default_factory=AppliedTagsResponse,
        description="Structured tag payload that should be written to the local file by the client.",
    )
    reason: str | None = Field(
        default=None,
        description="Explanation when no match was accepted or when a partial failure occurred.",
    )

    model_config = {
        "title": "FileAssignmentResponse",
        "json_schema_extra": {
            "example": {
                "source_id": "song1.wav",
                "matched": True,
                "similarity": 0.91,
                "acoustid_id": "27b3fad6-d400-4930-a261-dd994f036244",
                "target_path": None,
                "release_id": "release-mbid",
                "track_id": "track-mbid",
                "recording_id": "recording-mbid",
                "applied_tags": {
                    "title": "Song 1",
                    "artist": "Artist 1",
                    "album": "Album 1",
                    "musicbrainz_recordingid": "recording-mbid",
                },
                "reason": None,
            }
        },
    }


class HealthResponse(BaseModel):
    status: str = Field(description="Static service status string.")

    model_config = {
        "title": "HealthResponse",
        "json_schema_extra": {"example": {"status": "ok"}},
    }


class MetadataFileLookupResponse(BaseModel):
    mode: str = Field(
        description="Response mode identifier.", examples=["metadata-file"]
    )
    result: FileAssignmentResponse = Field(
        description="Resolved match result for the requested file."
    )

    model_config = {"title": "MetadataFileLookupResponse"}


class MetadataClusterLookupResponse(BaseModel):
    mode: str = Field(
        description="Response mode identifier.", examples=["metadata-cluster"]
    )
    release_id: str = Field(description="Matched MusicBrainz release MBID.")
    similarity: float = Field(
        description="Cluster-level similarity for the chosen release."
    )
    release_title: str = Field(description="Matched release title.")
    release_artist: str = Field(description="Matched release artist credit.")
    assignments: list[FileAssignmentResponse] = Field(
        description="Per-file assignments within the matched release."
    )

    model_config = {"title": "MetadataClusterLookupResponse"}


class AcoustIdFileLookupResponse(BaseModel):
    mode: str = Field(
        description="Response mode identifier.", examples=["acoustid-file"]
    )
    result: FileAssignmentResponse = Field(
        description="Resolved AcoustID-only match result for the requested file."
    )

    model_config = {"title": "AcoustIdFileLookupResponse"}


class AcoustIdBatchLookupResponse(BaseModel):
    mode: str = Field(
        description="Response mode identifier.", examples=["acoustid-files"]
    )
    results: list[FileAssignmentResponse] = Field(
        description="Resolved AcoustID-only match results, one per input item."
    )

    model_config = {"title": "AcoustIdBatchLookupResponse"}


class HybridFileLookupResponse(BaseModel):
    mode: str = Field(description="Response mode identifier.", examples=["hybrid-file"])
    result: FileAssignmentResponse = Field(
        description="Resolved hybrid match result for the requested file."
    )

    model_config = {"title": "HybridFileLookupResponse"}


class HybridBatchLookupResponse(BaseModel):
    mode: str = Field(
        description="Response mode identifier.", examples=["hybrid-files"]
    )
    results: list[FileAssignmentResponse] = Field(
        description="Resolved hybrid match results, one per input item."
    )

    model_config = {"title": "HybridBatchLookupResponse"}


class AudioMetadataRequest(BaseModel):
    title: str = Field(default="", description="Track title.")
    artist: str = Field(
        default="", description="Track artist credit as extracted from the file."
    )
    album: str = Field(default="", description="Release title or album name.")
    albumartist: str = Field(default="", description="Album artist credit.")
    tracknumber: str = Field(
        default="", description="Track number on the medium, usually as a string."
    )
    totaltracks: str = Field(
        default="", description="Total number of tracks on the medium."
    )
    discnumber: str = Field(
        default="", description="Disc or medium number within the release."
    )
    totaldiscs: str = Field(
        default="", description="Total number of discs or media in the release."
    )
    date: str = Field(
        default="", description="Release date text from the file metadata."
    )
    isrc: str = Field(default="", description="Track ISRC if present.")
    releasecountry: str = Field(
        default="", description="Release country code if known."
    )
    releasetype: str = Field(
        default="", description="Release type such as album, single, or EP."
    )
    media: str = Field(
        default="",
        description="Physical or digital medium label, for example CD or Digital Media.",
    )
    format_name: str = Field(
        default="", description="Container or format name detected by the client."
    )
    is_video: bool = Field(
        default=False,
        description="Whether the source should be treated as a video track.",
    )
    length_ms: int = Field(
        default=0, ge=0, description="Track duration in milliseconds."
    )
    musicbrainz_albumid: str = Field(
        default="", description="MusicBrainz release MBID if already known."
    )
    musicbrainz_trackid: str = Field(
        default="", description="MusicBrainz track MBID if already known."
    )
    musicbrainz_recordingid: str = Field(
        default="", description="MusicBrainz recording MBID if already known."
    )
    musicbrainz_releasegroupid: str = Field(
        default="", description="MusicBrainz release-group MBID if already known."
    )
    musicbrainz_artistid: str = Field(
        default="", description="MusicBrainz artist MBID(s) if already known."
    )
    musicbrainz_albumartistid: str = Field(
        default="", description="MusicBrainz album artist MBID(s) if already known."
    )
    musicbrainz_workid: str = Field(
        default="", description="MusicBrainz work MBID(s) if already known."
    )
    label: str = Field(default="", description="Record label name if known.")
    catalognumber: str = Field(default="", description="Release catalog number if known.")
    barcode: str = Field(default="", description="Release barcode if known.")
    script: str = Field(default="", description="Release script if known.")
    originaldate: str = Field(default="", description="Original release date if known.")
    work: str = Field(default="", description="Work title if known.")
    composer: str = Field(default="", description="Composer(s) if known.")
    lyricist: str = Field(default="", description="Lyricist(s) if known.")
    writer: str = Field(default="", description="Writer(s) if known.")
    arranger: str = Field(default="", description="Arranger(s) if known.")
    producer: str = Field(default="", description="Producer(s) if known.")
    engineer: str = Field(default="", description="Engineer(s) if known.")
    mixer: str = Field(default="", description="Mix engineer(s) if known.")
    conductor: str = Field(default="", description="Conductor(s) if known.")
    performers: str = Field(default="", description="Performer credits if known.")

    model_config = {
        "title": "AudioMetadataPayload",
        "json_schema_extra": {"example": METADATA_EXAMPLE},
    }


class FileLookupRequest(BaseModel):
    metadata: AudioMetadataRequest = Field(
        description="Extracted metadata for the single input file."
    )
    source_id: str = Field(
        default="input",
        description="Caller-defined identifier echoed back as `source_id` in the response.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of MusicBrainz search results to inspect.",
    )
    track_match_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum similarity required for a successful metadata-based match.",
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered ISO-3166-1 country codes used to rank candidate releases. "
            "The first entry scores highest; later entries decay proportionally; "
            "a release country absent from the list scores zero."
        ),
    )

    model_config = {
        "title": "MetadataFileLookupRequest",
        "json_schema_extra": {
            "example": {
                "source_id": "song1.wav",
                "metadata": METADATA_EXAMPLE,
                "search_limit": 10,
                "track_match_threshold": 0.4,
                "preferred_release_countries": ["DE", "GB"],
            }
        },
    }


class ClusterItemRequest(BaseModel):
    metadata: AudioMetadataRequest = Field(
        description="Metadata for one file participating in a cluster lookup."
    )
    source_id: str = Field(
        description="Caller-defined identifier for this file inside the cluster request."
    )

    model_config = {"title": "MetadataClusterLookupItem"}


class ClusterLookupRequest(BaseModel):
    items: list[ClusterItemRequest] = Field(
        min_length=1,
        description="Files that should be matched together against one release candidate.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of release candidates to inspect.",
    )
    cluster_match_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum similarity required for accepting a release-level cluster match.",
    )
    track_match_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum similarity required for assigning individual tracks within the chosen release.",
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered ISO-3166-1 country codes used to rank candidate releases. "
            "The first entry scores highest; later entries decay proportionally; "
            "a release country absent from the list scores zero."
        ),
    )

    model_config = {
        "title": "MetadataClusterLookupRequest",
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "source_id": "01.wav",
                        "metadata": {
                            **METADATA_EXAMPLE,
                            "title": "Track 1",
                            "tracknumber": "1",
                        },
                    },
                    {
                        "source_id": "02.wav",
                        "metadata": {
                            **METADATA_EXAMPLE,
                            "title": "Track 2",
                            "tracknumber": "2",
                        },
                    },
                ],
                "search_limit": 10,
                "cluster_match_threshold": 0.5,
                "track_match_threshold": 0.4,
                "preferred_release_countries": ["DE", "GB"],
            }
        },
    }


class AcoustIdFileLookupRequest(BaseModel):
    fingerprint: str = Field(
        description="Chromaprint fingerprint string generated client-side, for example via `fpcalc -json`."
    )
    duration: int = Field(
        gt=0,
        description="Fingerprint duration in whole seconds. This should come from the same fingerprinting run.",
    )
    source_id: str = Field(
        default="input",
        description="Caller-defined identifier echoed back as `source_id` in the response.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of AcoustID-normalized candidates to inspect.",
    )
    track_match_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum AcoustID score required for accepting the match.",
    )

    model_config = {
        "title": "AcoustIdFileLookupRequest",
        "json_schema_extra": {
            "example": {
                "source_id": "song1.wav",
                "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
                "duration": 190,
                "search_limit": 10,
                "track_match_threshold": 0.0,
            }
        },
    }


class AcoustIdItemRequest(BaseModel):
    fingerprint: str = Field(
        description="Chromaprint fingerprint string generated for this file."
    )
    duration: int = Field(
        gt=0,
        description="Fingerprint duration in whole seconds for this file.",
    )
    source_id: str = Field(
        description="Caller-defined identifier for this file inside the batch request."
    )

    model_config = {"title": "AcoustIdBatchLookupItem"}


class AcoustIdBatchLookupRequest(BaseModel):
    items: list[AcoustIdItemRequest] = Field(
        min_length=1,
        description="Files to resolve independently via AcoustID-only matching.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of AcoustID-normalized candidates to inspect per file.",
    )
    track_match_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum AcoustID score required for accepting each file match.",
    )

    model_config = {
        "title": "AcoustIdBatchLookupRequest",
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "source_id": "01.wav",
                        "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
                        "duration": 190,
                    },
                    {
                        "source_id": "02.wav",
                        "fingerprint": "AQAAjV2JZEoSZSoAAAAAAAAA",
                        "duration": 210,
                    },
                ],
                "search_limit": 10,
                "track_match_threshold": 0.0,
            }
        },
    }


class HybridFileLookupRequest(BaseModel):
    metadata: AudioMetadataRequest = Field(
        description="Extracted metadata used to rank the AcoustID candidate set."
    )
    fingerprint: str = Field(
        description="Chromaprint fingerprint string generated client-side."
    )
    duration: int = Field(
        gt=0,
        description="Fingerprint duration in whole seconds from the same fingerprinting run.",
    )
    source_id: str = Field(
        default="input",
        description="Caller-defined identifier echoed back as `source_id` in the response.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of AcoustID-normalized candidates to inspect before metadata ranking.",
    )
    track_match_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum final hybrid similarity required for accepting the match.",
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered ISO-3166-1 country codes used to rank releases attached to "
            "AcoustID candidates. The first entry scores highest; later entries "
            "decay proportionally; a release country absent from the list scores zero."
        ),
    )

    model_config = {
        "title": "HybridFileLookupRequest",
        "json_schema_extra": {
            "example": {
                "source_id": "song1.wav",
                "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
                "duration": 190,
                "metadata": METADATA_EXAMPLE,
                "search_limit": 10,
                "track_match_threshold": 0.0,
                "preferred_release_countries": ["DE", "GB"],
            }
        },
    }


class HybridItemRequest(BaseModel):
    metadata: AudioMetadataRequest = Field(
        description="Extracted metadata for this file."
    )
    fingerprint: str = Field(
        description="Chromaprint fingerprint string generated for this file."
    )
    duration: int = Field(
        gt=0,
        description="Fingerprint duration in whole seconds for this file.",
    )
    source_id: str = Field(
        description="Caller-defined identifier for this file inside the batch request."
    )

    model_config = {"title": "HybridBatchLookupItem"}


class HybridBatchLookupRequest(BaseModel):
    items: list[HybridItemRequest] = Field(
        min_length=1,
        description="Files to resolve independently with AcoustID candidate generation and metadata ranking.",
    )
    search_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of AcoustID-normalized candidates to inspect per file.",
    )
    track_match_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum final hybrid similarity required for accepting each file match.",
    )
    preferred_release_countries: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered ISO-3166-1 country codes used to rank releases attached to "
            "AcoustID candidates. The first entry scores highest; later entries "
            "decay proportionally; a release country absent from the list scores zero."
        ),
    )

    model_config = {
        "title": "HybridBatchLookupRequest",
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "source_id": "01.wav",
                        "fingerprint": "AQAAO0mUaEkSZSoAAAAAAAAA",
                        "duration": 190,
                        "metadata": {
                            **METADATA_EXAMPLE,
                            "title": "Track 1",
                            "tracknumber": "1",
                        },
                    },
                    {
                        "source_id": "02.wav",
                        "fingerprint": "AQAAjV2JZEoSZSoAAAAAAAAA",
                        "duration": 210,
                        "metadata": {
                            **METADATA_EXAMPLE,
                            "title": "Track 2",
                            "tracknumber": "2",
                        },
                    },
                ],
                "search_limit": 10,
                "track_match_threshold": 0.0,
                "preferred_release_countries": ["DE", "GB"],
            }
        },
    }


@app.get(
    "/api/health",
    tags=["health"],
    summary="Health Check",
    description="Simple readiness probe for the API process.",
    response_description="Static service status payload.",
    response_model=HealthResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def health() -> dict[str, str]:
    return {"status": "ok"}


def _serialize_assignment(assignment) -> dict:
    payload = asdict(assignment)
    payload["source_id"] = payload.pop("source_path")
    payload["applied_tags"] = _serialize_applied_tags(payload.get("applied_tags") or {})
    return payload


def _serialize_assignments(assignments) -> list[dict]:
    return [_serialize_assignment(assignment) for assignment in assignments]


def _serialize_applied_tags(tags: dict[str, str]) -> dict:
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


@app.post(
    "/api/lookup/metadata/file",
    tags=["metadata"],
    summary="Single-File Metadata Lookup",
    description=(
        "Search MusicBrainz directly from one file's extracted metadata and "
        "return the best matching release-track assignment."
    ),
    response_description="Resolved metadata match result for one input file.",
    response_model=MetadataFileLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_file(request: FileLookupRequest) -> dict:
    try:
        result = service.autotag_metadata(
            metadata=_to_audio_metadata(request.metadata),
            source_id=request.source_id,
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
            preferred_countries=request.preferred_release_countries,
        )
        return {
            "mode": "metadata-file",
            "result": _serialize_assignment(result),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


@app.post(
    "/api/lookup/metadata/cluster",
    tags=["metadata"],
    summary="Cluster Metadata Lookup",
    description=(
        "Treat multiple input files as one album-like cluster, search for a "
        "matching MusicBrainz release, and assign the files to release tracks."
    ),
    response_description="Resolved cluster match with per-file assignments.",
    response_model=MetadataClusterLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_cluster(request: ClusterLookupRequest) -> dict:
    try:
        result = service.autotag_cluster_metadata(
            items=[
                InputFile(
                    path=item.source_id, metadata=_to_audio_metadata(item.metadata)
                )
                for item in request.items
            ],
            cluster_match_threshold=request.cluster_match_threshold,
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
            preferred_countries=request.preferred_release_countries,
        )
        return {
            "mode": "metadata-cluster",
            "release_id": result.release_id,
            "similarity": result.similarity,
            "release_title": result.release_title,
            "release_artist": result.release_artist,
            "assignments": _serialize_assignments(result.assignments),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


@app.post(
    "/api/lookup/acoustid/file",
    tags=["acoustid"],
    summary="Single-File AcoustID Lookup",
    description=(
        "Resolve one file from a Chromaprint fingerprint plus duration using "
        "AcoustID only. No metadata ranking is applied."
    ),
    response_description="Resolved AcoustID-only match result for one input file.",
    response_model=AcoustIdFileLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_acoustid_file(request: AcoustIdFileLookupRequest) -> dict:
    try:
        result = service.autotag_acoustid_file(
            fingerprint=request.fingerprint,
            duration=request.duration,
            source_id=request.source_id,
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
        )
        return {
            "mode": "acoustid-file",
            "result": _serialize_assignment(result),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


@app.post(
    "/api/lookup/acoustid/files",
    tags=["acoustid"],
    summary="Batch AcoustID Lookup",
    description=(
        "Resolve multiple files independently from fingerprint plus duration "
        "using AcoustID-only ranking."
    ),
    response_description="List of resolved AcoustID-only match results.",
    response_model=AcoustIdBatchLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_acoustid_files(request: AcoustIdBatchLookupRequest) -> dict:
    try:
        results = service.autotag_acoustid_tracks(
            items=[
                (item.source_id, item.fingerprint, item.duration)
                for item in request.items
            ],
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
        )
        return {
            "mode": "acoustid-files",
            "results": _serialize_assignments(results),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


@app.post(
    "/api/lookup/hybrid/file",
    tags=["hybrid"],
    summary="Single-File Hybrid Lookup",
    description=(
        "Use AcoustID fingerprint lookup to generate candidates, then rank "
        "those candidates with the supplied file metadata."
    ),
    response_description="Resolved hybrid match result for one input file.",
    response_model=HybridFileLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_hybrid_file(request: HybridFileLookupRequest) -> dict:
    try:
        result = service.autotag_hybrid_file(
            metadata=_to_audio_metadata(request.metadata),
            fingerprint=request.fingerprint,
            duration=request.duration,
            source_id=request.source_id,
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
            preferred_countries=request.preferred_release_countries,
        )
        return {
            "mode": "hybrid-file",
            "result": _serialize_assignment(result),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


@app.post(
    "/api/lookup/hybrid/files",
    tags=["hybrid"],
    summary="Batch Hybrid Lookup",
    description=(
        "Resolve multiple files independently with AcoustID candidate "
        "generation and metadata-based ranking."
    ),
    response_description="List of resolved hybrid match results.",
    response_model=HybridBatchLookupResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Unhandled application error."}
    },
)
def lookup_hybrid_files(request: HybridBatchLookupRequest) -> dict:
    try:
        results = service.autotag_hybrid_inputs(
            items=[
                (
                    InputFile(
                        path=item.source_id, metadata=_to_audio_metadata(item.metadata)
                    ),
                    item.fingerprint,
                    item.duration,
                )
                for item in request.items
            ],
            track_match_threshold=request.track_match_threshold,
            search_limit=request.search_limit,
            preferred_countries=request.preferred_release_countries,
        )
        return {
            "mode": "hybrid-files",
            "results": _serialize_assignments(results),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "traceback": traceback.format_exc()},
        ) from exc


def _to_audio_metadata(request: AudioMetadataRequest) -> AudioMetadata:
    return AudioMetadata(**request.model_dump())
