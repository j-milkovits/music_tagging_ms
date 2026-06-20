from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable

from . import ratecontrol
from .models import ArtistCredit, AudioMetadata

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "tagging-ms/0.1"


class MusicBrainzClient:
    def __init__(
        self,
        base_url: str = "https://musicbrainz.org/ws/2",
        user_agent: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or os.getenv(
            "TAGGING_MS_USER_AGENT", DEFAULT_USER_AGENT
        )

    def find_tracks(self, metadata: AudioMetadata, limit: int = 10) -> list[dict]:
        query_args = self._build_track_query_args(metadata)
        if not query_args:
            raise ValueError(
                "At least one file metadata field is required for a MusicBrainz track lookup"
            )

        recordings = self._find_recordings(query_args, limit=limit)
        if recordings:
            return recordings

        fallback_args = dict(query_args)
        fallback_artist = _clean_artist_name(str(fallback_args.get("artist", "")))
        if fallback_artist and fallback_artist != fallback_args.get("artist"):
            fallback_args["artist"] = fallback_artist
            recordings = self._find_recordings(fallback_args, limit=limit)
            if recordings:
                return recordings

        relaxed_args = dict(fallback_args)
        relaxed_args.pop("release", None)
        if relaxed_args != fallback_args:
            recordings = self._find_recordings(relaxed_args, limit=limit)
        return recordings

    def find_releases(self, metadata: AudioMetadata, limit: int = 10) -> list[dict]:
        query_args = {}
        if metadata.release:
            query_args["release"] = metadata.release
        if metadata.release_artist:
            query_args["artist"] = metadata.release_artist
        if metadata.totaltracks:
            query_args["tracks"] = metadata.totaltracks
        if not query_args:
            raise ValueError(
                "release or release_artist is required for a MusicBrainz release lookup"
            )

        payload = self._get_json(
            "/release",
            {
                "query": _build_lucene_query(query_args),
                "fmt": "json",
                "limit": str(limit),
                "inc": "artist-credits+release-groups+media",
                "dismax": "true",
            },
        )
        return payload.get("releases", [])

    def get_release(self, release_id: str) -> dict:
        return self._get_json(
            f"/release/{release_id}",
            {
                "fmt": "json",
                # *-level-rels parameters control which entity gets a
                # `relations` array; the target-type filters (`artist-rels`,
                # `work-rels`) control which kinds of relations get included
                # in those arrays. All four are needed for the full chain
                # recording → work → composer/lyricist plus recording-level
                # producer/conductor/performer credits.
                "inc": (
                    "artists+artist-credits+recordings+release-groups+media"
                    "+isrcs+labels+genres"
                    "+recording-level-rels+work-level-rels+release-rels"
                    "+artist-rels+work-rels"
                ),
            },
        )

    def get_release_by_discid(self, discid: str, toc: str) -> dict:
        """Look up releases by CD DiscID and/or TOC (table of contents).

        Used by the CD-ripping flow, which has no fingerprint. Pass a real
        DiscID, or ``-`` (the default when empty) to do a fuzzy TOC-only
        lookup. Both forms return a top-level ``releases`` array. The
        ``inc=artist-credits`` keeps the summaries cheap while still carrying
        artist names for the candidate list. Returns ``{}`` when MusicBrainz
        has no release for the disc (404 / CD stub); a malformed DiscID or TOC
        surfaces as a 400 ``HTTPError`` for the caller to translate.
        """
        disc = discid.strip() or "-"
        # The TOC's `+` separators are URL-encoded spaces; accept either form
        # and let urlencode re-encode the spaces back to `+` on the wire.
        toc_value = " ".join(toc.replace("+", " ").split())
        params = {"fmt": "json", "inc": "artist-credits"}
        if toc_value:
            params["toc"] = toc_value
        try:
            return self._get_json(f"/discid/{disc}", params)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise

    def get_recording(self, recording_id: str) -> dict:
        return self._get_json(
            f"/recording/{recording_id}",
            {
                "fmt": "json",
                "inc": "artists+artist-credits+release-groups+releases+media+isrcs",
            },
        )

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"

        def factory() -> urllib.request.Request:
            return urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
            )

        try:
            return ratecontrol.send_json(factory, url)
        except urllib.error.URLError:
            logger.warning("[musicbrainz] request failed: %s", url)
            raise

    def _build_track_query_args(self, metadata: AudioMetadata) -> dict[str, str]:
        query_args: dict[str, str] = {}
        if metadata.title:
            query_args["track"] = metadata.title
        if metadata.artist:
            query_args["artist"] = metadata.artist
        if metadata.release:
            query_args["release"] = metadata.release
        if metadata.tracknumber:
            query_args["tnum"] = metadata.tracknumber
        if metadata.totaltracks:
            query_args["tracks"] = metadata.totaltracks
        if metadata.length_ms:
            query_args["qdur"] = str(metadata.length_ms // 2000)
        if metadata.isrc:
            query_args["isrc"] = metadata.isrc
        return query_args

    def _find_recordings(self, query_args: dict[str, str], limit: int) -> list[dict]:
        payload = self._get_json(
            "/recording",
            {
                "query": _build_lucene_query(query_args),
                "fmt": "json",
                "limit": str(limit),
                "inc": "releases+release-groups+artist-credits+isrcs",
                "dismax": "true",
            },
        )
        return payload.get("recordings", [])


_LUCENE_SPECIAL_CHARS_RE = re.compile(r'([+\-&|!(){}\[\]\^"~*?:\\/])')
_TOPIC_SUFFIX_RE = re.compile(r"\s+-\s+topic$", re.IGNORECASE)


def _escape_lucene_query(text: str) -> str:
    return _LUCENE_SPECIAL_CHARS_RE.sub(r"\\\1", text)


def _build_lucene_query(args: dict[str, str]) -> str:
    return " ".join(
        f"{key}:({_escape_lucene_query(value)})" for key, value in args.items() if value
    )


def _clean_artist_name(value: str) -> str:
    return _TOPIC_SUFFIX_RE.sub("", value).strip()


def artist_credit_name(node: Iterable[dict]) -> str:
    parts: list[str] = []
    for credit in node:
        if "name" in credit:
            parts.append(str(credit["name"]))
        elif "artist" in credit:
            parts.append(str(credit["artist"].get("name", "")))
        if "joinphrase" in credit:
            parts.append(str(credit["joinphrase"]))
    return "".join(parts).strip()


def artist_credit_ids(node: Iterable[dict]) -> str:
    ids: list[str] = []
    for credit in node:
        if "artist" in credit:
            artist_id = credit["artist"].get("id", "")
            if artist_id:
                ids.append(str(artist_id))
    return "; ".join(ids)


def parse_artist_credits(node: Iterable[dict]) -> tuple[ArtistCredit, ...]:
    out: list[ArtistCredit] = []
    for credit in node:
        artist = credit.get("artist") or {}
        name = str(credit.get("name") or artist.get("name") or "")
        if not name:
            continue
        out.append(
            ArtistCredit(
                name=name,
                sort_name=str(artist.get("sort-name") or ""),
                musicbrainz_artistid=str(artist.get("id") or ""),
                type=str(artist.get("type") or ""),
                disambiguation=str(artist.get("disambiguation") or ""),
            )
        )
    return tuple(out)
