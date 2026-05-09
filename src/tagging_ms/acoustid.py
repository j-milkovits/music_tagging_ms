from __future__ import annotations

import contextlib
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass

from . import ratecontrol
from .musicbrainz import DEFAULT_USER_AGENT, MusicBrainzClient

logger = logging.getLogger(__name__)

SOURCE_THRESHOLD_NO_METADATA = 0.25
MAX_NO_METADATA_RECORDINGS = 3
PICARD_ACOUSTID_LOOKUP_KEY = "v8pQ6oyB"


@dataclass(slots=True)
class AcoustIdLookupResult:
    acoustid_id: str | None
    recordings: list[dict]


class AcoustIdClient:
    def __init__(
        self,
        client_key: str | None = None,
        base_url: str = "https://api.acoustid.org/v2",
        user_agent: str | None = None,
        musicbrainz_client: MusicBrainzClient | None = None,
    ) -> None:
        self.client_key = (
            client_key
            or os.getenv("TAGGING_MS_ACOUSTID_API_KEY", "").strip()
            or PICARD_ACOUSTID_LOOKUP_KEY
        )
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or os.getenv(
            "TAGGING_MS_USER_AGENT", DEFAULT_USER_AGENT
        ) or DEFAULT_USER_AGENT
        self.musicbrainz_client = musicbrainz_client or MusicBrainzClient(
            user_agent=self.user_agent
        )

    def lookup_by_id(self, acoustid_id: str, limit: int = 10) -> AcoustIdLookupResult:
        acoustid_id = acoustid_id.strip()
        if not acoustid_id:
            raise ValueError("AcoustID lookup requires a non-empty acoustid_id")

        return self._lookup({"trackid": acoustid_id}, limit=limit)

    def lookup_by_fingerprint(
        self, fingerprint: str, duration: int, limit: int = 10
    ) -> AcoustIdLookupResult:
        fingerprint = fingerprint.strip()
        if not fingerprint:
            raise ValueError("AcoustID lookup requires a non-empty fingerprint")
        if duration <= 0:
            raise ValueError("AcoustID lookup requires a positive duration")

        return self._lookup(
            {
                "fingerprint": fingerprint,
                "duration": str(duration),
            },
            limit=limit,
        )

    def _lookup(self, params: dict[str, str], limit: int) -> AcoustIdLookupResult:
        if not self.client_key:
            raise ValueError(
                "TAGGING_MS_ACOUSTID_API_KEY is required for AcoustID lookup"
            )

        payload = self._post_json(
            "/lookup",
            {
                "client": self.client_key,
                "clientversion": self.user_agent,
                "format": "json",
                "meta": "recordings releasegroups releases tracks compress sources",
                **params,
            },
        )
        results = payload.get("results") or []
        recordings = self._normalize_results(results)
        return AcoustIdLookupResult(
            acoustid_id=results[0].get("id") if results else None,
            recordings=recordings[:limit],
        )

    def _post_json(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{path}"
        body = urllib.parse.urlencode(params).encode()

        def factory() -> urllib.request.Request:
            return urllib.request.Request(
                url,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": self.user_agent,
                },
            )

        try:
            return ratecontrol.send_json(factory, url)
        except urllib.error.HTTPError:
            logger.warning("[acoustid] request failed: %s", url)
            raise

    def _normalize_results(self, results: list[dict]) -> list[dict]:
        recording_map: dict[str, dict[str, tuple[dict, float, int]]] = defaultdict(dict)
        missing_metadata_counts: dict[str, int] = defaultdict(int)

        for result in results:
            result_score = _get_score(result)
            acoustid_id = str(result.get("id", "")).strip()
            recordings = result.get("recordings") or []
            max_sources = max(
                (int(recording.get("sources", 1)) for recording in recordings),
                default=1,
            )

            for recording in sorted(
                recordings,
                key=lambda item: int(item.get("sources", 1)),
                reverse=True,
            ):
                recording_id = str(recording.get("id", "")).strip()
                if not recording_id:
                    continue
                sources = int(recording.get("sources", 1))

                if _recording_has_metadata(recording):
                    parsed = _parse_recording(recording)
                elif (
                    sources / max_sources > SOURCE_THRESHOLD_NO_METADATA
                    and missing_metadata_counts[acoustid_id]
                    < MAX_NO_METADATA_RECORDINGS
                ):
                    parsed = self.musicbrainz_client.get_recording(recording_id)
                    missing_metadata_counts[acoustid_id] += 1
                else:
                    parsed = None

                if parsed is None:
                    continue

                existing = recording_map[acoustid_id].get(recording_id)
                if existing is None:
                    recording_map[acoustid_id][recording_id] = (
                        parsed,
                        result_score,
                        sources,
                    )
                else:
                    recording_map[acoustid_id][recording_id] = (
                        existing[0],
                        max(existing[1], result_score),
                        existing[2] + sources,
                    )

        normalized: list[dict] = []
        for acoustid_id, recordings in recording_map.items():
            max_sources = max(
                (sources for _, _, sources in recordings.values()), default=1
            )
            for parsed, result_score, sources in recordings.values():
                item = dict(parsed)
                item["score"] = min(sources / max_sources, 1.0) * 100 * result_score
                item["acoustid"] = acoustid_id
                item["sources"] = sources
                normalized.append(item)
        return normalized


def _coerce_date(value: object) -> str:
    """Normalise an AcoustID date (dict or str) to an ISO `YYYY[-MM[-DD]]` string."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        year = value.get("year")
        if year is None:
            return ""
        month = value.get("month")
        day = value.get("day")
        if month and day:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if month:
            return f"{int(year):04d}-{int(month):02d}"
        return f"{int(year):04d}"
    return str(value)


def _make_releases_node(recording: dict) -> list[dict]:
    release_list: list[dict] = []
    for release_group in recording.get("releasegroups") or []:
        for release in release_group.get("releases") or []:
            release_mb: dict[str, object] = {
                "id": release["id"],
                "release-group": {"id": release_group["id"]},
                "title": release.get("title") or release_group.get("title", ""),
                "media": [],
            }
            rg_node: dict[str, object] = release_mb["release-group"]  # type: ignore[assignment]
            if release_group.get("type"):
                rg_node["primary-type"] = release_group["type"]
            if release_group.get("secondarytypes"):
                rg_node["secondary-types"] = release_group["secondarytypes"]
            if release.get("country"):
                release_mb["country"] = release["country"]
            release_date = _coerce_date(release.get("date"))
            if release_date:
                release_mb["date"] = release_date
            if release.get("medium_count") is not None:
                release_mb["medium-count"] = release["medium_count"]
            if release.get("track_count") is not None:
                release_mb["track-count"] = release["track_count"]

            media_node: list[dict[str, object]] = release_mb["media"]  # type: ignore[assignment]
            for medium in release.get("mediums") or []:
                media_mb: dict[str, object] = {}
                if medium.get("format"):
                    media_mb["format"] = medium["format"]
                if medium.get("track_count") is not None:
                    media_mb["track-count"] = medium["track_count"]
                if medium.get("position") is not None:
                    media_mb["position"] = medium["position"]
                if medium.get("tracks"):
                    media_mb["tracks"] = medium["tracks"]
                media_node.append(media_mb)

            releaseevents = release.get("releaseevents") or []
            if releaseevents:
                # AcoustID may emit one release per event with different
                # country+date. Pick a single canonical variant per release id;
                # downstream country preference handling (selecting between
                # release variants by preferred_countries) is the orchestrator's
                # job, not the AcoustID parser's.
                primary_event = releaseevents[0]
                release_variant = dict(release_mb)
                if primary_event.get("country"):
                    release_variant["country"] = primary_event["country"]
                event_date = _coerce_date(primary_event.get("date"))
                if event_date:
                    release_variant["date"] = event_date
                release_list.append(release_variant)
            else:
                release_list.append(release_mb)
    return release_list


def _make_artist_credit_node(artists: list[dict]) -> list[dict]:
    artist_list: list[dict] = []
    for index, artist in enumerate(artists):
        node = {
            "artist": {
                "name": artist["name"],
                "sort-name": artist["name"],
                "id": artist["id"],
            },
            "name": artist["name"],
        }
        if index > 0:
            node["joinphrase"] = "; "
        artist_list.append(node)
    return artist_list


def _parse_recording(recording: dict) -> dict | None:
    if "id" not in recording:
        return None

    recording_mb: dict[str, object] = {"id": recording["id"]}
    if recording.get("title") is not None:
        recording_mb["title"] = recording["title"]
    if recording.get("artists"):
        recording_mb["artist-credit"] = _make_artist_credit_node(recording["artists"])
    if recording.get("releasegroups"):
        recording_mb["releases"] = _make_releases_node(recording)
    if recording.get("duration") is not None:
        with contextlib.suppress(TypeError, ValueError):
            recording_mb["length"] = int(recording["duration"]) * 1000
    if recording.get("sources") is not None:
        recording_mb["sources"] = recording["sources"]
    return recording_mb


def _recording_has_metadata(recording: dict) -> bool:
    return "id" in recording and recording.get("title") is not None


def _get_score(node: dict) -> float:
    try:
        return float(node.get("score", 1.0))
    except (TypeError, ValueError):
        return 1.0
