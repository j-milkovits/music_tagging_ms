from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict

from .models import (
    ArtistCredit,
    AudioMetadata,
    CoverArt,
    MatchCandidate,
    Performer,
    ReleaseCredits,
    ReleaseTrack,
    TrackCredits,
    Work,
)
from .musicbrainz import artist_credit_ids, artist_credit_name, parse_artist_credits
from .similarity import (
    extract_year_from_date,
    length_score,
    linear_combination_of_weights,
    similarity2,
    trackcount_score,
)

FILE_COMPARISON_WEIGHTS: dict[str, int] = {
    "release": 5,
    "artist": 4,
    "date": 4,
    "format": 2,
    "isvideo": 2,
    "length": 10,
    "release_country": 2,
    "release_type": 14,
    "title": 13,
    "totaltracks": 4,
}


def best_match(candidates: Iterable[MatchCandidate]) -> MatchCandidate | None:
    return max(candidates, key=lambda candidate: candidate.similarity, default=None)


def compare_release_parts(
    metadata: AudioMetadata,
    release: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> list[tuple[float, int]]:
    parts: list[tuple[float, int]] = []

    if metadata.release and "release" in weights:
        parts.append(
            (similarity2(metadata.release, release.get("title", "")), weights["release"])
        )

    if metadata.release_artist and "release_artist" in weights:
        parts.append(
            (
                similarity2(
                    metadata.release_artist,
                    artist_credit_name(release.get("artist-credit", [])),
                ),
                weights["release_artist"],
            )
        )

    if metadata.totaltracks and "totaltracks" in weights:
        expected = _safe_int(metadata.totaltracks)
        if expected is not None:
            if "media" in release:
                score = 0.0
                for medium in release["media"]:
                    score = max(
                        score, trackcount_score(expected, medium.get("track-count", 0))
                    )
            else:
                score = trackcount_score(expected, release.get("track-count", 0))
            parts.append((score, weights["totaltracks"]))

    if metadata.date and "date" in weights:
        parts.append(
            (
                _date_match_factor(metadata.date, release.get("date", "")),
                weights["date"],
            )
        )

    if "release_country" in weights and preferred_countries:
        parts.append(
            (
                _preferred_country_score(release, preferred_countries),
                weights["release_country"],
            )
        )

    if metadata.release_type and "release_type" in weights:
        release_type = ""
        release_group = release.get("release-group") or {}
        primary = release_group.get("primary-type")
        secondary = release_group.get("secondary-types") or []
        if primary:
            release_type = str(primary).lower()
        if secondary:
            release_type = " ".join(
                [release_type, *(str(item).lower() for item in secondary)]
            ).strip()
        parts.append(
            (similarity2(metadata.release_type, release_type), weights["release_type"])
        )

    if metadata.format_name and "format" in weights:
        media_formats = [
            str(medium.get("format", ""))
            for medium in release.get("media", [])
            if medium.get("format")
        ]
        score = max(
            (similarity2(metadata.format_name, fmt) for fmt in media_formats),
            default=0.0,
        )
        parts.append((score, weights["format"]))

    return parts


def score_track_only_parts(
    file_metadata: AudioMetadata, track: dict, weights: dict[str, int]
) -> list[tuple[float, int]]:
    """Build the file→track similarity parts (title/artist/length/isvideo).

    These parts are independent of the release context and can be reused when
    scoring the same track against multiple candidate releases.
    """
    parts: list[tuple[float, int]] = []

    if file_metadata.title:
        parts.append(
            (similarity2(file_metadata.title, track.get("title", "")), weights["title"])
        )

    if file_metadata.artist:
        parts.append(
            (
                similarity2(
                    file_metadata.artist,
                    artist_credit_name(track.get("artist-credit", [])),
                ),
                weights["artist"],
            )
        )

    if file_metadata.length_ms and track.get("length"):
        parts.append(
            (
                length_score(file_metadata.length_ms, int(track["length"])),
                weights["length"],
            )
        )

    # `isvideo` is only meaningful alongside a real metadata signal. Scoring it
    # on its own would let a file that supplied no metadata match perfectly
    # (False == False -> 1.0), so only include it when title/artist/length
    # already produced a part.
    if parts and "isvideo" in weights:
        file_is_video = file_metadata.is_video
        track_is_video = bool(track.get("video"))
        parts.append(
            (1.0 if file_is_video == track_is_video else 0.0, weights["isvideo"])
        )

    return parts


def score_file_track_on_release(
    file_metadata: AudioMetadata,
    track: dict,
    release: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> float:
    """Score a (file, track, release) triple, multiplied by the track's search score.

    This is the per-track-on-release scorer the joint matcher's stage 1 calls.
    Equivalent to one iteration of the inner loop in `compare_track`.
    """
    parts = score_track_only_parts(file_metadata, track, weights)
    parts.extend(compare_release_parts(file_metadata, release, weights, preferred_countries))
    # With no comparable metadata the score is purely the fingerprint/search
    # confidence — don't fabricate a 1.0 by averaging an empty parts list.
    if not parts:
        return get_search_score(track)
    return linear_combination_of_weights(parts) * get_search_score(track)


def compare_track(
    file_metadata: AudioMetadata,
    track: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> float:
    """Score a file against a track, taking the best score across the track's releases."""
    parts = score_track_only_parts(file_metadata, track, weights)

    releases = track.get("releases") or []
    if not releases:
        # No metadata to compare -> fall back to the fingerprint/search
        # confidence rather than averaging an empty parts list to 1.0.
        if not parts:
            return get_search_score(track)
        return linear_combination_of_weights(parts) * get_search_score(track)

    best = 0.0
    for release in releases:
        release_parts = parts + compare_release_parts(
            file_metadata, release, weights, preferred_countries
        )
        if not release_parts:
            similarity = get_search_score(track)
        else:
            similarity = linear_combination_of_weights(release_parts) * get_search_score(
                track
            )
        best = max(best, similarity)
    return best


def release_to_metadata(
    release: dict,
    preferred_countries: list[str] | None = None,
) -> AudioMetadata:
    release_group = release.get("release-group") or {}
    primary_type = str(release_group.get("primary-type", "")).lower()
    secondary_types = [
        str(item).lower() for item in release_group.get("secondary-types", [])
    ]
    label_infos = release.get("label-info") or []
    metadata = AudioMetadata(
        release=release.get("title", ""),
        release_artist=artist_credit_name(release.get("artist-credit", [])),
        date=release.get("date", ""),
        release_country=_pick_release_country(release, preferred_countries),
        release_type="; ".join(
            part for part in [primary_type, *secondary_types] if part
        ),
        totaldiscs=str(len(release.get("media", []))),
        media=" / ".join(
            str(medium.get("format", ""))
            for medium in release.get("media", [])
            if medium.get("format")
        ),
        musicbrainz_release_id=release.get("id", ""),
        musicbrainz_release_group_id=release_group.get("id", ""),
        musicbrainz_release_artist_id=artist_credit_ids(release.get("artist-credit", [])),
        barcode=release.get("barcode") or "",
        script=(release.get("text-representation") or {}).get("script") or "",
        originaldate=release_group.get("first-release-date", ""),
        label="; ".join(
            str((info.get("label") or {}).get("name"))
            for info in label_infos
            if info and (info.get("label") or {}).get("name")
        ),
        catalognumber="; ".join(
            info["catalog-number"]
            for info in label_infos
            if info and info.get("catalog-number")
        ),
    )
    return metadata


def _extract_cover_art(release: dict) -> CoverArt | None:
    """Pass through the release's `cover-art-archive` availability block.

    MusicBrainz includes this object by default on release responses, so we
    expose it directly instead of making a separate Cover Art Archive request.
    """
    caa = release.get("cover-art-archive")
    if not isinstance(caa, dict):
        return None
    return CoverArt(
        front=bool(caa.get("front")),
        back=bool(caa.get("back")),
        count=int(caa.get("count") or 0),
        artwork=bool(caa.get("artwork")),
        darkened=bool(caa.get("darkened")),
    )


def build_release_tracks(
    release: dict,
    preferred_countries: list[str] | None = None,
) -> list[ReleaseTrack]:
    release_metadata = release_to_metadata(release, preferred_countries)
    release_credits = _extract_release_credits(release)
    cover_art = _extract_cover_art(release)
    release_artists = parse_artist_credits(release.get("artist-credit", []))
    tracks: list[ReleaseTrack] = []
    media = release.get("media", [])
    totaldiscs = str(len(media))
    for medium in media:
        discnumber = str(medium.get("position", ""))
        totaltracks = str(medium.get("track-count", ""))
        media_format = str(medium.get("format", ""))
        for track in medium.get("tracks", []):
            recording = track.get("recording", {})
            track_artist_credit = track.get("artist-credit", recording.get("artist-credit", []))
            recording_isrcs = recording.get("isrcs") or []
            track_credits = _extract_track_credits(recording)
            track_genre = _extract_genres(
                recording, release, release.get("release-group") or {}
            )
            metadata = AudioMetadata(
                title=track.get("title", recording.get("title", "")),
                artist=artist_credit_name(track_artist_credit),
                release=release_metadata.release,
                release_artist=release_metadata.release_artist,
                tracknumber=str(track.get("position", "")),
                totaltracks=totaltracks,
                discnumber=discnumber,
                totaldiscs=totaldiscs,
                date=release_metadata.date,
                isrc=recording_isrcs[0] if recording_isrcs else "",
                release_country=release_metadata.release_country,
                release_type=release_metadata.release_type,
                media=media_format,
                length_ms=int(track.get("length") or recording.get("length") or 0),
                musicbrainz_release_id=release_metadata.musicbrainz_release_id,
                musicbrainz_release_group_id=release_metadata.musicbrainz_release_group_id,
                musicbrainz_trackid=track.get("id", ""),
                musicbrainz_recordingid=recording.get("id", ""),
                musicbrainz_artistid=artist_credit_ids(track_artist_credit),
                musicbrainz_release_artist_id=release_metadata.musicbrainz_release_artist_id,
                barcode=release_metadata.barcode,
                script=release_metadata.script,
                originaldate=release_metadata.originaldate,
                label=release_metadata.label,
                catalognumber=release_metadata.catalognumber,
                genre=track_genre,
            )
            tracks.append(
                ReleaseTrack(
                    release_id=release_metadata.musicbrainz_release_id,
                    release_group_id=release_metadata.musicbrainz_release_group_id,
                    track_id=metadata.musicbrainz_trackid,
                    recording_id=metadata.musicbrainz_recordingid,
                    metadata=metadata,
                    release_artists=release_artists,
                    artists=parse_artist_credits(track_artist_credit),
                    track_credits=track_credits,
                    release_credits=release_credits,
                    cover_art=cover_art,
                )
            )
    return tracks


def tagged_metadata_for_assignment(release_track: ReleaseTrack) -> dict[str, str]:
    data = asdict(release_track.metadata)
    result: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            result[key] = "1" if value else "0"
        elif value != "":
            result[key] = str(value)
    return result


RELEASE_TAG_KEYS: frozenset[str] = frozenset(
    {
        "release",
        "date",
        "originaldate",
        "release_country",
        "release_type",
        "musicbrainz_release_id",
        "musicbrainz_release_group_id",
        "musicbrainz_release_artist_id",
        "label",
        "catalognumber",
        "barcode",
        "script",
    }
)

# Flat tags superseded by structured siblings on the response
# (`release_artist`/`musicbrainz_release_artist_id` → `release_artists`,
# `artist`/`musicbrainz_artistid` → `artists`). Suppressed from both release-
# and track-level dicts so callers don't see redundant data.
SUPERSEDED_TAG_KEYS: frozenset[str] = frozenset(
    {"release_artist", "musicbrainz_release_artist_id", "artist", "musicbrainz_artistid"}
)


def split_release_track_tags(
    release_track: ReleaseTrack,
) -> tuple[dict[str, str], dict[str, str]]:
    """Materialise tags and partition into (release-level, track-level)."""
    all_tags = {
        k: v
        for k, v in tagged_metadata_for_assignment(release_track).items()
        if k not in SUPERSEDED_TAG_KEYS
    }
    release_tags = {k: v for k, v in all_tags.items() if k in RELEASE_TAG_KEYS}
    track_tags = {k: v for k, v in all_tags.items() if k not in RELEASE_TAG_KEYS}
    return release_tags, track_tags


_MAX_GENRES = 5
_MIN_GENRE_USAGE_PCT = 90


def _extract_genres(*nodes: dict) -> str:
    """Aggregate MB genre arrays from one or more nodes into a joined string.

    Mirrors Picard's defaults: cumulative counts across nodes, filter by
    >= 90% of max count, top 5, title-cased, alphabetical, joined with '; '.
    """
    counter: Counter[str] = Counter()
    for node in nodes:
        for entry in node.get("genres") or []:
            if not entry:
                continue
            name = entry.get("name")
            if not name:
                continue
            count = entry.get("count")
            counter[name] += int(count or 1)
    if not counter:
        return ""
    max_count = max(counter.values())
    threshold = max_count * (_MIN_GENRE_USAGE_PCT / 100.0)
    filtered = [(name, c) for name, c in counter.items() if c >= threshold]
    top = sorted(filtered, key=lambda kv: (-kv[1], kv[0]))[: _MAX_GENRES]
    names = sorted(name.title() for name, _ in top)
    return "; ".join(names)


def _artist_credit_from_rel(rel: dict) -> ArtistCredit | None:
    artist = rel.get("artist") or {}
    name = str(artist.get("name") or "")
    if not name:
        return None
    return ArtistCredit(
        name=name,
        sort_name=str(artist.get("sort-name") or ""),
        musicbrainz_artistid=str(artist.get("id") or ""),
        type=str(artist.get("type") or ""),
        disambiguation=str(artist.get("disambiguation") or ""),
    )


def _performer_from_rel(rel: dict) -> Performer | None:
    artist = rel.get("artist") or {}
    name = str(artist.get("name") or "")
    if not name:
        return None
    attrs = tuple(str(a) for a in (rel.get("attributes") or []))
    return Performer(
        name=name,
        sort_name=str(artist.get("sort-name") or ""),
        musicbrainz_artistid=str(artist.get("id") or ""),
        type=str(artist.get("type") or ""),
        disambiguation=str(artist.get("disambiguation") or ""),
        attributes=attrs,
    )


_PERFORMER_REL_TYPES: frozenset[str] = frozenset(
    {"vocal", "performer", "performing orchestra"}
)


def _bucket_artist_rel(
    rel: dict,
    buckets: dict[str, list[ArtistCredit]],
    performers: list[Performer],
    instruments: list[Performer] | None = None,
) -> None:
    rel_type = rel.get("type", "")
    if rel_type == "instrument":
        # Instrument relations are exposed as a dedicated, person-independent
        # `instruments` list (the relation's `attributes` hold the instrument
        # names) rather than mixed into `performers`.
        if instruments is not None:
            performer = _performer_from_rel(rel)
            if performer is not None:
                instruments.append(performer)
        return
    if rel_type in _PERFORMER_REL_TYPES:
        performer = _performer_from_rel(rel)
        if performer is not None:
            performers.append(performer)
        return
    bucket_key = {
        "producer": "producers",
        "engineer": "engineers",
        "mix": "mixers",
        "conductor": "conductors",
        "arranger": "arrangers",
    }.get(rel_type)
    if bucket_key is None:
        return
    credit = _artist_credit_from_rel(rel)
    if credit is not None:
        buckets[bucket_key].append(credit)


_TRACK_WORK_REL_BUCKETS = {
    "composer": "composers",
    "lyricist": "lyricists",
    "writer": "writers",
    "arranger": "arrangers",
}


def _extract_track_credits(recording: dict) -> TrackCredits:
    buckets: dict[str, list[ArtistCredit]] = {
        "producers": [],
        "engineers": [],
        "mixers": [],
        "conductors": [],
        "arrangers": [],
        "composers": [],
        "lyricists": [],
        "writers": [],
    }
    performers: list[Performer] = []
    instruments: list[Performer] = []
    works: list[Work] = []

    for rel in recording.get("relations") or []:
        target_type = rel.get("target-type", "")
        if target_type == "artist":
            _bucket_artist_rel(rel, buckets, performers, instruments)
        elif target_type == "work" and rel.get("type") == "performance":
            work = rel.get("work") or {}
            title = str(work.get("title") or "")
            work_id = str(work.get("id") or "")
            if title or work_id:
                works.append(Work(title=title, musicbrainz_id=work_id))
            for work_rel in work.get("relations") or []:
                if work_rel.get("target-type") != "artist":
                    continue
                bucket_key = _TRACK_WORK_REL_BUCKETS.get(work_rel.get("type", ""))
                if bucket_key is None:
                    continue
                credit = _artist_credit_from_rel(work_rel)
                if credit is not None:
                    buckets[bucket_key].append(credit)

    return TrackCredits(
        composers=tuple(buckets["composers"]),
        lyricists=tuple(buckets["lyricists"]),
        writers=tuple(buckets["writers"]),
        arrangers=tuple(buckets["arrangers"]),
        producers=tuple(buckets["producers"]),
        engineers=tuple(buckets["engineers"]),
        mixers=tuple(buckets["mixers"]),
        conductors=tuple(buckets["conductors"]),
        performers=tuple(performers),
        instruments=tuple(instruments),
        works=tuple(works),
    )


def _extract_release_credits(release: dict) -> ReleaseCredits:
    """Extract release-level artist relations (e.g. release-wide producer)."""
    buckets: dict[str, list[ArtistCredit]] = {
        "producers": [],
        "engineers": [],
        "mixers": [],
        "conductors": [],
        "arrangers": [],
    }
    performers: list[Performer] = []
    instruments: list[Performer] = []
    for rel in release.get("relations") or []:
        if rel.get("target-type") != "artist":
            continue
        _bucket_artist_rel(rel, buckets, performers, instruments)
    return ReleaseCredits(
        producers=tuple(buckets["producers"]),
        engineers=tuple(buckets["engineers"]),
        mixers=tuple(buckets["mixers"]),
        conductors=tuple(buckets["conductors"]),
        arrangers=tuple(buckets["arrangers"]),
        performers=tuple(performers),
        instruments=tuple(instruments),
    )


def _preferred_country_score(release: dict, preferred_countries: list[str]) -> float:
    total = len(preferred_countries)
    if not total:
        return 0.0
    release_country = release.get("country", "")
    if not release_country:
        return 0.0
    try:
        index = preferred_countries.index(release_country)
    except ValueError:
        return 0.0
    return float(total - index) / float(total)


def _release_countries(release: dict) -> list[str]:
    countries: list[str] = []
    for event in release.get("release-events") or []:
        area = event.get("area") or {}
        codes = area.get("iso-3166-1-codes") or []
        if codes:
            countries.append(codes[0])
    if not countries and release.get("country"):
        countries.append(release["country"])
    return countries


def _pick_release_country(release: dict, preferred_countries: list[str] | None) -> str:
    if preferred_countries:
        available = _release_countries(release)
        for country in preferred_countries:
            if country in available:
                return country
    return release.get("country", "")


def get_search_score(node: dict) -> float:
    score = node.get("score")
    if score is None:
        return 1.0
    try:
        return float(score) / 100.0
    except (TypeError, ValueError):
        return 1.0


def _date_match_factor(metadata_date: str, release_date: str) -> float:
    if not release_date:
        return 0.25
    if metadata_date == release_date:
        return 1.0
    release_year = extract_year_from_date(release_date)
    metadata_year = extract_year_from_date(metadata_date)
    if release_year is None or metadata_year is None:
        return 0.0
    if release_year == metadata_year:
        return 0.95
    if abs(release_year - metadata_year) <= 2:
        return 0.85
    return 0.0


def _safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
