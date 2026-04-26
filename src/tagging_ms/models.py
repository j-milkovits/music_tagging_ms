from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AudioMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    tracknumber: str = ""
    totaltracks: str = ""
    discnumber: str = ""
    totaldiscs: str = ""
    date: str = ""
    isrc: str = ""
    releasecountry: str = ""
    releasetype: str = ""
    media: str = ""
    format_name: str = ""
    is_video: bool = False
    length_ms: int = 0
    musicbrainz_albumid: str = ""
    musicbrainz_trackid: str = ""
    musicbrainz_recordingid: str = ""
    musicbrainz_releasegroupid: str = ""
    musicbrainz_artistid: str = ""
    musicbrainz_albumartistid: str = ""
    musicbrainz_workid: str = ""
    label: str = ""
    catalognumber: str = ""
    barcode: str = ""
    script: str = ""
    originaldate: str = ""
    work: str = ""
    composer: str = ""
    lyricist: str = ""
    writer: str = ""
    arranger: str = ""
    producer: str = ""
    engineer: str = ""
    mixer: str = ""
    conductor: str = ""
    performers: str = ""
    genre: str = ""


@dataclass(slots=True)
class InputFile:
    path: str
    metadata: AudioMetadata


@dataclass(slots=True)
class ReleaseTrack:
    album_id: str
    release_group_id: str
    track_id: str
    recording_id: str
    metadata: AudioMetadata


@dataclass(slots=True)
class MatchCandidate:
    similarity: float
    payload: dict | None = None


@dataclass(slots=True)
class FileAssignment:
    source_path: str
    matched: bool
    similarity: float
    acoustid_id: str | None = None
    target_path: str | None = None
    release_id: str | None = None
    track_id: str | None = None
    recording_id: str | None = None
    applied_tags: dict[str, str] = field(default_factory=dict)
    reason: str | None = None


@dataclass(slots=True)
class ClusterMatch:
    release_id: str
    similarity: float
    release_title: str
    release_artist: str
    assignments: list[FileAssignment]
