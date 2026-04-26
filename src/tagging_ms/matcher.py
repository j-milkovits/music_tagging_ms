from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from typing import TypeAliasType

from .models import AudioMetadata, InputFile, MatchCandidate, ReleaseTrack
from .musicbrainz import artist_credit_ids, artist_credit_name
from .similarity import (extract_year_from_date, length_score,
                         linear_combination_of_weights, similarity2,
                         trackcount_score)

FILE_COMPARISON_WEIGHTS = {
    "album": 5,
    "artist": 4,
    "date": 4,
    "format": 2,
    "isvideo": 2,
    "length": 10,
    "releasecountry": 2,
    "releasetype": 14,
    "title": 13,
    "totaltracks": 4,
}

CLUSTER_COMPARISON_WEIGHTS = {
    "album": 17,
    "albumartist": 6,
    "date": 4,
    "format": 2,
    "releasecountry": 2,
    "releasetype": 10,
    "totalalbumtracks": 5,
}

TRACK_ASSIGNMENT_WEIGHTS = {
    "title": 22,
    "artist": 6,
    "album": 12,
    "tracknumber": 6,
    "totaltracks": 5,
    "discnumber": 5,
    "totaldiscs": 4,
    "length": 8,
}

def best_match(candidates: Iterable[MatchCandidate]) -> MatchCandidate | None:
    return max(candidates, key=lambda candidate: candidate.similarity, default=None)


def compare_release(
    metadata: AudioMetadata,
    release: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> float:
    return linear_combination_of_weights(
        compare_release_parts(metadata, release, weights, preferred_countries)
    ) * get_search_score(release)


def compare_release_parts(
    metadata: AudioMetadata,
    release: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> list[tuple[float, int]]:
    parts: list[tuple[float, int]] = []

    if metadata.album and "album" in weights:
        parts.append(
            (similarity2(metadata.album, release.get("title", "")), weights["album"])
        )

    if metadata.albumartist and "albumartist" in weights:
        parts.append(
            (
                similarity2(
                    metadata.albumartist,
                    artist_credit_name(release.get("artist-credit", [])),
                ),
                weights["albumartist"],
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

    if "totalalbumtracks" in weights:
        expected = _safe_int(metadata.totaltracks)
        actual = _safe_int(release.get("track-count"))
        if expected is not None and actual is not None:
            parts.append(
                (trackcount_score(expected, actual), weights["totalalbumtracks"])
            )

    if metadata.date and "date" in weights:
        parts.append(
            (
                _date_match_factor(metadata.date, release.get("date", "")),
                weights["date"],
            )
        )

    if "releasecountry" in weights and preferred_countries:
        parts.append(
            (
                _preferred_country_score(release, preferred_countries),
                weights["releasecountry"],
            )
        )

    if metadata.releasetype and "releasetype" in weights:
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
            (similarity2(metadata.releasetype, release_type), weights["releasetype"])
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


def compare_track(
    file_metadata: AudioMetadata,
    track: dict,
    weights: dict[str, int],
    preferred_countries: list[str] | None = None,
) -> float:
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

    if "isvideo" in weights:
        file_is_video = file_metadata.is_video
        track_is_video = bool(track.get("video"))
        parts.append(
            (1.0 if file_is_video == track_is_video else 0.0, weights["isvideo"])
        )

    releases = track.get("releases") or []
    if not releases:
        return linear_combination_of_weights(parts) * get_search_score(track)

    best = 0.0
    for release in releases:
        similarity = linear_combination_of_weights(
            parts
            + compare_release_parts(
                file_metadata, release, weights, preferred_countries
            )
        )
        best = max(best, similarity * get_search_score(track))
    return best


def aggregate_cluster_metadata(files: list[InputFile]) -> AudioMetadata:
    counter_title = Counter(
        file.metadata.album for file in files if file.metadata.album
    )
    counter_artist = Counter(
        file.metadata.albumartist or file.metadata.artist
        for file in files
        if file.metadata.artist
    )
    counter_date = Counter(file.metadata.date for file in files if file.metadata.date)
    counter_type = Counter(
        file.metadata.releasetype for file in files if file.metadata.releasetype
    )
    counter_format = Counter(
        file.metadata.format_name for file in files if file.metadata.format_name
    )
    metadata = AudioMetadata(
        album=counter_title.most_common(1)[0][0] if counter_title else "",
        albumartist=counter_artist.most_common(1)[0][0] if counter_artist else "",
        totaltracks=str(len(files)),
        date=counter_date.most_common(1)[0][0] if counter_date else "",
        releasetype=counter_type.most_common(1)[0][0] if counter_type else "",
        format_name=counter_format.most_common(1)[0][0] if counter_format else "",
    )
    return metadata


def release_to_album_metadata(
    release: dict, preferred_countries: list[str] | None = None
) -> AudioMetadata:
    release_group = release.get("release-group") or {}
    primary_type = str(release_group.get("primary-type", "")).lower()
    secondary_types = [
        str(item).lower() for item in release_group.get("secondary-types", [])
    ]
    label_infos = release.get("label-info") or []
    metadata = AudioMetadata(
        album=release.get("title", ""),
        albumartist=artist_credit_name(release.get("artist-credit", [])),
        date=release.get("date", ""),
        releasecountry=_pick_release_country(release, preferred_countries),
        releasetype="; ".join(
            part for part in [primary_type, *secondary_types] if part
        ),
        totaldiscs=str(len(release.get("media", []))),
        media=" / ".join(
            str(medium.get("format", ""))
            for medium in release.get("media", [])
            if medium.get("format")
        ),
        musicbrainz_albumid=release.get("id", ""),
        musicbrainz_releasegroupid=release_group.get("id", ""),
        musicbrainz_albumartistid=artist_credit_ids(release.get("artist-credit", [])),
        barcode=release.get("barcode") or "",
        script=release.get("text-representation", {}).get("script") or "",
        originaldate=release_group.get("first-release-date", ""),
        label="; ".join(
            info["label"]["name"]
            for info in label_infos
            if info.get("label", {}).get("name")
        ),
        catalognumber="; ".join(
            info["catalog-number"]
            for info in label_infos
            if info.get("catalog-number")
        ),
    )
    return metadata


def build_release_tracks(
    release: dict, preferred_countries: list[str] | None = None
) -> list[ReleaseTrack]:
    release_metadata = release_to_album_metadata(release, preferred_countries)
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
            rels = _extract_recording_relations(recording)
            metadata = AudioMetadata(
                title=track.get("title", recording.get("title", "")),
                artist=artist_credit_name(track_artist_credit),
                album=release_metadata.album,
                albumartist=release_metadata.albumartist,
                tracknumber=str(track.get("position", "")),
                totaltracks=totaltracks,
                discnumber=discnumber,
                totaldiscs=totaldiscs,
                date=release_metadata.date,
                isrc=recording_isrcs[0] if recording_isrcs else "",
                releasecountry=release_metadata.releasecountry,
                releasetype=release_metadata.releasetype,
                media=media_format,
                length_ms=int(track.get("length") or recording.get("length") or 0),
                musicbrainz_albumid=release_metadata.musicbrainz_albumid,
                musicbrainz_releasegroupid=release_metadata.musicbrainz_releasegroupid,
                musicbrainz_trackid=track.get("id", ""),
                musicbrainz_recordingid=recording.get("id", ""),
                musicbrainz_artistid=artist_credit_ids(track_artist_credit),
                musicbrainz_albumartistid=release_metadata.musicbrainz_albumartistid,
                musicbrainz_workid=rels["musicbrainz_workid"],
                barcode=release_metadata.barcode,
                script=release_metadata.script,
                originaldate=release_metadata.originaldate,
                label=release_metadata.label,
                catalognumber=release_metadata.catalognumber,
                work=rels["work"],
                composer=rels["composer"],
                lyricist=rels["lyricist"],
                writer=rels["writer"],
                arranger=rels["arranger"],
                producer=rels["producer"],
                engineer=rels["engineer"],
                mixer=rels["mixer"],
                conductor=rels["conductor"],
                performers=rels["performers"],
            )
            tracks.append(
                ReleaseTrack(
                    album_id=release_metadata.musicbrainz_albumid,
                    release_group_id=release_metadata.musicbrainz_releasegroupid,
                    track_id=metadata.musicbrainz_trackid,
                    recording_id=metadata.musicbrainz_recordingid,
                    metadata=metadata,
                )
            )
    return tracks


def assign_files_to_release(
    files: list[InputFile], release_tracks: list[ReleaseTrack], threshold: float
) -> list[tuple[InputFile, ReleaseTrack | None, float]]:
    assignments: list[tuple[InputFile, ReleaseTrack | None, float]] = []
    remaining_tracks = list(release_tracks)

    for file in files:
        direct_match = _match_by_mbid(file, remaining_tracks)
        if direct_match is not None:
            assignments.append((file, direct_match, 1.0))
            remaining_tracks.remove(direct_match)
            continue

        candidates = []
        for track in remaining_tracks:
            similarity = compare_file_to_album_track(file.metadata, track.metadata)
            if similarity >= threshold:
                candidates.append((similarity, track))
        if not candidates:
            assignments.append((file, None, 0.0))
            continue
        similarity, track = max(candidates, key=lambda item: item[0])
        assignments.append((file, track, similarity))
        remaining_tracks.remove(track)

    return assignments


def compare_file_to_album_track(
    file_metadata: AudioMetadata, track_metadata: AudioMetadata
) -> float:
    parts: list[tuple[float, int]] = []
    if file_metadata.length_ms and track_metadata.length_ms:
        parts.append(
            (
                length_score(file_metadata.length_ms, track_metadata.length_ms),
                TRACK_ASSIGNMENT_WEIGHTS["length"],
            )
        )

    for field_name in ("title", "artist", "album"):
        value_a = getattr(file_metadata, field_name)
        value_b = getattr(track_metadata, field_name)
        if value_a and value_b:
            parts.append(
                (similarity2(value_a, value_b), TRACK_ASSIGNMENT_WEIGHTS[field_name])
            )

    for field_name in ("tracknumber", "totaltracks", "discnumber", "totaldiscs"):
        value_a = getattr(file_metadata, field_name)
        value_b = getattr(track_metadata, field_name)
        if value_a and value_b:
            parts.append(
                (
                    1.0 if str(value_a) == str(value_b) else 0.0,
                    TRACK_ASSIGNMENT_WEIGHTS[field_name],
                )
            )

    return linear_combination_of_weights(parts)


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


def _extract_recording_relations(recording: dict) -> dict[str, str]:
    composers: list[str] = []
    lyricists: list[str] = []
    writers: list[str] = []
    arrangers: list[str] = []
    producers: list[str] = []
    engineers: list[str] = []
    mixers: list[str] = []
    conductors: list[str] = []
    performers: list[str] = []
    work_ids: list[str] = []
    work_titles: list[str] = []

    for rel in recording.get("relations") or []:
        rel_type = rel.get("type", "")
        target_type = rel.get("target-type", "")

        if target_type == "artist":
            name = (rel.get("artist") or {}).get("name", "")
            if not name:
                continue
            attrs = rel.get("attributes") or []
            if rel_type == "producer":
                producers.append(name)
            elif rel_type == "engineer":
                engineers.append(name)
            elif rel_type == "mix":
                mixers.append(name)
            elif rel_type == "conductor":
                conductors.append(name)
            elif rel_type == "arranger":
                arrangers.append(name)
            elif rel_type in ("instrument", "vocal", "performer"):
                if attrs:
                    performers.append(f"{name} ({', '.join(attrs)})")
                else:
                    performers.append(name)

        elif target_type == "work" and rel_type == "performance":
            work = rel.get("work") or {}
            if work.get("id"):
                work_ids.append(work["id"])
            if work.get("title"):
                work_titles.append(work["title"])
            for work_rel in work.get("relations") or []:
                if work_rel.get("target-type") != "artist":
                    continue
                name = (work_rel.get("artist") or {}).get("name", "")
                if not name:
                    continue
                wrt = work_rel.get("type", "")
                if wrt == "composer":
                    composers.append(name)
                elif wrt == "lyricist":
                    lyricists.append(name)
                elif wrt == "writer":
                    writers.append(name)
                elif wrt == "arranger":
                    arrangers.append(name)

    return {
        "musicbrainz_workid": "; ".join(work_ids),
        "work": "; ".join(work_titles),
        "composer": "; ".join(composers),
        "lyricist": "; ".join(lyricists),
        "writer": "; ".join(writers),
        "arranger": "; ".join(arrangers),
        "producer": "; ".join(producers),
        "engineer": "; ".join(engineers),
        "mixer": "; ".join(mixers),
        "conductor": "; ".join(conductors),
        "performers": "; ".join(performers),
    }


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


def select_release_by_country(
    releases: list[dict], preferred_countries: list[str] | None
) -> dict | None:
    if not releases:
        return None
    if preferred_countries:
        for country in preferred_countries:
            for release in releases:
                if release.get("country") == country:
                    return release
    return releases[0]


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
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _match_by_mbid(file: InputFile, tracks: list[ReleaseTrack]) -> ReleaseTrack | None:
    metadata = file.metadata
    for track in tracks:
        if (
            metadata.musicbrainz_recordingid
            and metadata.musicbrainz_recordingid == track.recording_id
        ):
            return track
        if (
            metadata.musicbrainz_trackid
            and metadata.musicbrainz_trackid == track.track_id
        ):
            return track
    return None
