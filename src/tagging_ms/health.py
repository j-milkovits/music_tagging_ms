"""Mirror health for /api/health.

Two signals: can the MusicBrainz mirror be reached, and how far behind is its
replication. The replication figures come from a small JSON file the VM's
cron writes from ``replication_control`` (see the migration runbook, 3.6) —
the service deliberately has no route to Postgres itself.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .musicbrainz import PUBLIC_API_HOST_SUFFIX, MusicBrainzClient

logger = logging.getLogger(__name__)

# Any release known to exist; the probe only cares that ws/2 answers.
PROBE_RELEASE_ID = "2dc5dfc7-61df-4948-8c6b-e25a4cd039c8"
PROBE_TIMEOUT_S = 2.0
DEFAULT_STATUS_FILE = "/run/mirror/replication-status.json"
MAX_LAG_HOURS = 48.0


@dataclass(frozen=True, slots=True)
class MirrorHealth:
    reachable: bool
    schema_sequence: int | None
    replication_sequence: int | None
    last_replication: str | None
    lag_hours: float | None

    @property
    def degraded(self) -> bool:
        return not self.reachable or self.lag_hours is None or self.lag_hours > MAX_LAG_HOURS


def is_mirror(client: MusicBrainzClient) -> bool:
    host = urllib.parse.urlsplit(client.base_url).hostname or ""
    return not (host == PUBLIC_API_HOST_SUFFIX or host.endswith("." + PUBLIC_API_HOST_SUFFIX))


def probe(client: MusicBrainzClient) -> bool:
    """Direct request, bypassing the rate limiter — this is a liveness check."""
    url = f"{client.base_url}/release/{PROBE_RELEASE_ID}?fmt=json"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": client.user_agent or "tagging-ms"},
    )
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as response:
            return bool(response.status == 200)
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        logger.warning("mirror probe failed: %s", exc)
        return False


def read_replication_status(path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    """Contents of the replication status file, or ``{}`` if absent/unreadable."""
    if path is None:
        path = os.getenv("TAGGING_MS_REPLICATION_STATUS_FILE", DEFAULT_STATUS_FILE)
    file = Path(path)
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("replication status unreadable (%s): %s", file, exc)
        return {}
    return data if isinstance(data, dict) else {}


def lag_hours(last_replication: object, now: datetime | None = None) -> float | None:
    if not isinstance(last_replication, str):
        return None
    try:
        then = datetime.fromisoformat(last_replication.replace(" ", "T", 1))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return round((now - then).total_seconds() / 3600, 2)


def mirror_health(client: MusicBrainzClient, status_path: str | None = None) -> MirrorHealth:
    status = read_replication_status(status_path)
    last = status.get("last_replication")
    return MirrorHealth(
        reachable=probe(client),
        schema_sequence=_int_or_none(status.get("schema_sequence")),
        replication_sequence=_int_or_none(status.get("replication_sequence")),
        last_replication=last if isinstance(last, str) else None,
        lag_hours=lag_hours(last),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
