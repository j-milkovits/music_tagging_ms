from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AudioMetadata:
    title: str = ""
    artist: str = ""
    release: str = ""
    release_artist: str = ""
    tracknumber: str = ""
    totaltracks: str = ""
    discnumber: str = ""
    totaldiscs: str = ""
    date: str = ""
    isrc: str = ""
    release_country: str = ""
    release_type: str = ""
    media: str = ""
    format_name: str = ""
    is_video: bool = False
    length_ms: int = 0
    musicbrainz_release_id: str = ""
    musicbrainz_trackid: str = ""
    musicbrainz_recordingid: str = ""
    musicbrainz_release_group_id: str = ""
    musicbrainz_artistid: str = ""
    musicbrainz_release_artist_id: str = ""
    label: str = ""
    catalognumber: str = ""
    barcode: str = ""
    script: str = ""
    originaldate: str = ""
    genre: str = ""
    cover_art_url: str = ""
    cover_art_thumb_url: str = ""


@dataclass(frozen=True, slots=True)
class ArtistCredit:
    name: str
    sort_name: str = ""
    musicbrainz_artistid: str = ""
    type: str = ""
    disambiguation: str = ""


@dataclass(frozen=True, slots=True)
class Performer:
    name: str
    sort_name: str = ""
    musicbrainz_artistid: str = ""
    type: str = ""
    disambiguation: str = ""
    attributes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Work:
    title: str
    musicbrainz_id: str = ""


@dataclass(frozen=True, slots=True)
class TrackCredits:
    composers: tuple[ArtistCredit, ...] = ()
    lyricists: tuple[ArtistCredit, ...] = ()
    writers: tuple[ArtistCredit, ...] = ()
    arrangers: tuple[ArtistCredit, ...] = ()
    producers: tuple[ArtistCredit, ...] = ()
    engineers: tuple[ArtistCredit, ...] = ()
    mixers: tuple[ArtistCredit, ...] = ()
    conductors: tuple[ArtistCredit, ...] = ()
    performers: tuple[Performer, ...] = ()
    instruments: tuple[Performer, ...] = ()
    works: tuple[Work, ...] = ()


@dataclass(frozen=True, slots=True)
class ReleaseCredits:
    producers: tuple[ArtistCredit, ...] = ()
    engineers: tuple[ArtistCredit, ...] = ()
    mixers: tuple[ArtistCredit, ...] = ()
    conductors: tuple[ArtistCredit, ...] = ()
    arrangers: tuple[ArtistCredit, ...] = ()
    performers: tuple[Performer, ...] = ()
    instruments: tuple[Performer, ...] = ()


@dataclass(slots=True)
class ReleaseTrack:
    release_id: str
    release_group_id: str
    track_id: str
    recording_id: str
    metadata: AudioMetadata
    release_artists: tuple[ArtistCredit, ...] = ()
    artists: tuple[ArtistCredit, ...] = ()
    track_credits: TrackCredits = field(default_factory=TrackCredits)
    release_credits: ReleaseCredits = field(default_factory=ReleaseCredits)


@dataclass(slots=True)
class MatchCandidate:
    similarity: float
    payload: dict | None = None