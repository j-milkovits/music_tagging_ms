"""Per-host adaptive rate limiter ported from Picard's webservice/ratecontrol.

Original: picard/webservice/ratecontrol.py (GPL-2.0-or-later, MusicBrainz Picard).
Adapted to use stdlib logging, urllib for host parsing, and a threading.Lock so
the limiter is safe to share across threads (FastAPI sync handlers run in a
thread pool).
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


HostKey = tuple[str, int]


REQUEST_DELAY_MINIMUM: dict[HostKey, int] = defaultdict(lambda: 1000)
REQUEST_DELAY: dict[HostKey, int] = defaultdict(lambda: 1000)
REQUEST_DELAY_EXPONENT: dict[HostKey, int] = defaultdict(lambda: 0)
CONGESTION_UNACK: dict[HostKey, int] = defaultdict(lambda: 0)
CONGESTION_WINDOW_SIZE: dict[HostKey, float] = defaultdict(lambda: 1.0)
CONGESTION_SSTHRESH: dict[HostKey, int] = defaultdict(lambda: 0)
LAST_REQUEST_TIMES: dict[HostKey, float] = defaultdict(lambda: 0.0)


_lock = threading.Lock()


def hostkey_from_url(url: str) -> HostKey:
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return (parts.hostname or "", port)


def set_minimum_delay(hostkey: HostKey, delay_ms: int) -> None:
    with _lock:
        REQUEST_DELAY_MINIMUM[hostkey] = int(delay_ms)


def set_minimum_delay_for_url(url: str, delay_ms: int) -> None:
    set_minimum_delay(hostkey_from_url(url), delay_ms)


def current_delay(hostkey: HostKey) -> int:
    with _lock:
        return REQUEST_DELAY[hostkey]


def get_delay_to_next_request(hostkey: HostKey) -> tuple[bool, int]:
    """Calculate delay to next request to hostkey.

    Returns ``(wait, delay_ms)``: ``wait`` is True if a delay is needed and
    ``delay_ms`` is how long the caller should sleep.
    """
    with _lock:
        if CONGESTION_UNACK[hostkey] >= int(CONGESTION_WINDOW_SIZE[hostkey]):
            return (True, sys.maxsize)

        interval = REQUEST_DELAY[hostkey]
        if not interval:
            logger.debug("%s: Starting another request without delay", hostkey)
            return (False, 0)
        last_request = LAST_REQUEST_TIMES[hostkey]
        if not last_request:
            logger.debug("%s: First request", hostkey)
            _remember_request_time(hostkey)
            return (False, interval)
        elapsed = (time.time() - last_request) * 1000
        if elapsed >= interval:
            logger.debug(
                "%s: Last request was %d ms ago, starting another one",
                hostkey,
                elapsed,
            )
            return (False, interval)
        delay = int(math.ceil(interval - elapsed))
        logger.debug(
            "%s: Last request was %d ms ago, waiting %d ms before starting another one",
            hostkey,
            elapsed,
            delay,
        )
        return (True, delay)


def _remember_request_time(hostkey: HostKey) -> None:
    if REQUEST_DELAY[hostkey]:
        LAST_REQUEST_TIMES[hostkey] = time.time()


def increment_requests(hostkey: HostKey) -> None:
    with _lock:
        _remember_request_time(hostkey)
        CONGESTION_UNACK[hostkey] += 1
        logger.debug(
            "%s: Incrementing requests to: %d", hostkey, CONGESTION_UNACK[hostkey]
        )


def decrement_requests(hostkey: HostKey) -> None:
    with _lock:
        assert CONGESTION_UNACK[hostkey] > 0
        CONGESTION_UNACK[hostkey] -= 1
        logger.debug(
            "%s: Decrementing requests to: %d", hostkey, CONGESTION_UNACK[hostkey]
        )


def copy_minimal_delay(from_hostkey: HostKey, to_hostkey: HostKey) -> None:
    with _lock:
        if (
            from_hostkey in REQUEST_DELAY_MINIMUM
            and to_hostkey not in REQUEST_DELAY_MINIMUM
        ):
            REQUEST_DELAY_MINIMUM[to_hostkey] = REQUEST_DELAY_MINIMUM[from_hostkey]
            logger.debug(
                "%s: Copy minimum delay from %s, setting it to %dms",
                to_hostkey,
                from_hostkey,
                REQUEST_DELAY_MINIMUM[to_hostkey],
            )


def adjust(hostkey: HostKey, slow_down: bool) -> None:
    """Adjust REQUEST and CONGESTION metrics when an HTTP request completes."""
    with _lock:
        if slow_down:
            _slow_down(hostkey)
        elif CONGESTION_UNACK[hostkey] <= CONGESTION_WINDOW_SIZE[hostkey]:
            _out_of_backoff(hostkey)


def _slow_down(hostkey: HostKey) -> None:
    delay = max(
        pow(2, REQUEST_DELAY_EXPONENT[hostkey]) * 1000,
        REQUEST_DELAY_MINIMUM[hostkey],
    )

    REQUEST_DELAY_EXPONENT[hostkey] = min(REQUEST_DELAY_EXPONENT[hostkey] + 1, 5)

    CONGESTION_SSTHRESH[hostkey] = int(CONGESTION_WINDOW_SIZE[hostkey] / 2.0)
    CONGESTION_WINDOW_SIZE[hostkey] = 1.0

    logger.debug(
        "%s: slowdown; delay: %dms -> %dms; ssthresh: %d; cws: %.3f",
        hostkey,
        REQUEST_DELAY[hostkey],
        delay,
        CONGESTION_SSTHRESH[hostkey],
        CONGESTION_WINDOW_SIZE[hostkey],
    )

    REQUEST_DELAY[hostkey] = delay


def _out_of_backoff(hostkey: HostKey) -> None:
    REQUEST_DELAY_EXPONENT[hostkey] = 0

    delay = max(int(REQUEST_DELAY[hostkey] / 2), REQUEST_DELAY_MINIMUM[hostkey])

    cws = CONGESTION_WINDOW_SIZE[hostkey]
    sst = CONGESTION_SSTHRESH[hostkey]

    if sst and cws >= sst:
        phase = "congestion avoidance"
        cws = cws + (1.0 / cws)
    else:
        phase = "slow start"
        cws += 1

    if REQUEST_DELAY[hostkey] != delay or CONGESTION_WINDOW_SIZE[hostkey] != cws:
        logger.debug(
            "%s: oobackoff; delay: %dms -> %dms; %s; window size %.3f -> %.3f",
            hostkey,
            REQUEST_DELAY[hostkey],
            delay,
            phase,
            CONGESTION_WINDOW_SIZE[hostkey],
            cws,
        )

        CONGESTION_WINDOW_SIZE[hostkey] = cws
        REQUEST_DELAY[hostkey] = delay


set_minimum_delay_for_url("https://musicbrainz.org", 1000)
set_minimum_delay_for_url("https://api.acoustid.org", 333)


TEMP_ERRORS_RETRIES = 5
_WINDOW_FULL_BACKOFF_SECONDS = 0.05


def send_json(
    request_factory: Callable[[], urllib.request.Request],
    url: str,
    timeout: int = 30,
) -> Any:
    """Send a request through the rate limiter and return the parsed JSON.

    ``request_factory`` is called for each attempt so it can build a fresh
    ``urllib.request.Request`` (urlopen consumes the request stream). The loop
    retries up to ``TEMP_ERRORS_RETRIES`` times on 503/429, mirroring Picard's
    ``_handle_reply`` path in ``picard/webservice/__init__.py``.
    """
    hostkey = hostkey_from_url(url)
    last_error: urllib.error.HTTPError | None = None
    for attempt in range(TEMP_ERRORS_RETRIES + 1):
        wait, delay_ms = get_delay_to_next_request(hostkey)
        if wait:
            if delay_ms == sys.maxsize:
                time.sleep(_WINDOW_FULL_BACKOFF_SECONDS)
                continue
            time.sleep(delay_ms / 1000)
        increment_requests(hostkey)
        slow_down = False
        try:
            with urllib.request.urlopen(request_factory(), timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (503, 429):
                slow_down = True
                last_error = exc
                if attempt < TEMP_ERRORS_RETRIES:
                    logger.debug(
                        "%s: %d response, retrying (#%d)",
                        hostkey,
                        exc.code,
                        attempt + 1,
                    )
                    continue
            raise
        finally:
            decrement_requests(hostkey)
            adjust(hostkey, slow_down)
    assert last_error is not None
    raise last_error
