from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from mutagen import File as MutagenFile

from .models import AudioMetadata, InputFile

_TRACK_NUMBER_RE = re.compile(r"^\s*(\d+)(?:\s*/\s*(\d+))?\s*$")
_FILENAME_SANITIZE_RE = re.compile(r'[\\/:*?"<>|]+')

_TAG_KEY_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "albumartist": "albumartist",
    "tracknumber": "tracknumber",
    "totaltracks": "totaltracks",
    "discnumber": "discnumber",
    "totaldiscs": "totaldiscs",
    "date": "date",
    "isrc": "isrc",
    "releasecountry": "releasecountry",
    "releasetype": "releasetype",
    "media": "media",
    "musicbrainz_albumid": "musicbrainz_albumid",
    "musicbrainz_trackid": "musicbrainz_trackid",
    "musicbrainz_recordingid": "musicbrainz_recordingid",
    "musicbrainz_releasegroupid": "musicbrainz_releasegroupid",
    "musicbrainz_artistid": "musicbrainz_artistid",
    "musicbrainz_albumartistid": "musicbrainz_albumartistid",
    "musicbrainz_workid": "musicbrainz_workid",
    "label": "label",
    "catalognumber": "catalognumber",
    "barcode": "barcode",
    "script": "script",
    "originaldate": "originaldate",
    "work": "work",
    "composer": "composer",
    "lyricist": "lyricist",
    "writer": "writer",
    "arranger": "arranger",
    "producer": "producer",
    "engineer": "engineer",
    "mixer": "mixer",
    "conductor": "conductor",
    "performers": "performers",
    "genre": "genre",
}


def load_input_files(paths: list[str]) -> list[InputFile]:
    return [InputFile(path=path, metadata=read_audio_metadata(path)) for path in paths]


def read_audio_metadata(path: str) -> AudioMetadata:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported audio file: {path}")

    metadata = AudioMetadata()
    metadata.title = _first(audio.tags, "title")
    metadata.artist = _first(audio.tags, "artist")
    metadata.album = _first(audio.tags, "album")
    metadata.albumartist = _first(audio.tags, "albumartist")
    metadata.date = _first(audio.tags, "date")
    metadata.isrc = _first(audio.tags, "isrc")
    metadata.releasecountry = _first(audio.tags, "releasecountry")
    metadata.releasetype = _first(audio.tags, "releasetype")
    metadata.media = _first(audio.tags, "media")
    metadata.musicbrainz_albumid = _first(audio.tags, "musicbrainz_albumid")
    metadata.musicbrainz_trackid = _first(audio.tags, "musicbrainz_trackid")
    metadata.musicbrainz_recordingid = _first(audio.tags, "musicbrainz_recordingid")
    metadata.musicbrainz_releasegroupid = _first(
        audio.tags, "musicbrainz_releasegroupid"
    )
    metadata.musicbrainz_artistid = _first(audio.tags, "musicbrainz_artistid")
    metadata.musicbrainz_albumartistid = _first(audio.tags, "musicbrainz_albumartistid")
    metadata.musicbrainz_workid = _first(audio.tags, "musicbrainz_workid")
    metadata.label = _first(audio.tags, "label")
    metadata.catalognumber = _first(audio.tags, "catalognumber")
    metadata.barcode = _first(audio.tags, "barcode")
    metadata.script = _first(audio.tags, "script")
    metadata.originaldate = _first(audio.tags, "originaldate")
    metadata.work = _first(audio.tags, "work")
    metadata.composer = _first(audio.tags, "composer")
    metadata.lyricist = _first(audio.tags, "lyricist")
    metadata.writer = _first(audio.tags, "writer")
    metadata.arranger = _first(audio.tags, "arranger")
    metadata.producer = _first(audio.tags, "producer")
    metadata.engineer = _first(audio.tags, "engineer")
    metadata.mixer = _first(audio.tags, "mixer")
    metadata.conductor = _first(audio.tags, "conductor")
    metadata.performers = _first(audio.tags, "performers")

    tracknumber = _first(audio.tags, "tracknumber")
    metadata.tracknumber, metadata.totaltracks = _split_number_pair(tracknumber)

    discnumber = _first(audio.tags, "discnumber")
    metadata.discnumber, metadata.totaldiscs = _split_number_pair(discnumber)
    if not metadata.totaltracks:
        metadata.totaltracks = _first(audio.tags, "totaltracks")
    if not metadata.totaldiscs:
        metadata.totaldiscs = _first(audio.tags, "totaldiscs")

    info = getattr(audio, "info", None)
    metadata.length_ms = int(getattr(info, "length", 0) * 1000)
    metadata.format_name = audio.__class__.__name__.replace("Easy", "")
    return metadata


def write_audio_metadata(
    source_path: str, tags: dict[str, str], output_dir: str | None = None
) -> str:
    target_path = source_path
    if output_dir:
        target_path = _copy_target_path(source_path, tags, output_dir)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_path, target_path)

    audio = MutagenFile(target_path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported audio file: {target_path}")
    if audio.tags is None:
        audio.add_tags()

    for key, value in tags.items():
        tag_key = _TAG_KEY_MAP.get(key)
        if not tag_key or value == "":
            continue
        try:
            audio[tag_key] = [str(value)]
        except Exception:
            continue

    audio.save()
    return target_path


def _first(tags, key: str) -> str:
    if not tags or key not in tags:
        return ""
    value = tags[key]
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def _split_number_pair(value: str) -> tuple[str, str]:
    match = _TRACK_NUMBER_RE.match(value or "")
    if not match:
        return value or "", ""
    return match.group(1) or "", match.group(2) or ""


def _copy_target_path(source_path: str, tags: dict[str, str], output_dir: str) -> str:
    source = Path(source_path)
    tracknumber = tags.get("tracknumber", "0")
    title = tags.get("title", source.stem)
    safe_title = _FILENAME_SANITIZE_RE.sub("_", title).strip() or source.stem
    filename = (
        f"{int(tracknumber):02d} - {safe_title}{source.suffix}"
        if tracknumber.isdigit()
        else f"{safe_title}{source.suffix}"
    )
    return str(Path(output_dir) / filename)
