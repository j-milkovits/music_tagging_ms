"""Shared pytest fixtures and VCR configuration.

VCR is used to replay HTTP responses from MusicBrainz and AcoustID. The
default --record-mode=none (set in pyproject.toml) replays only — no live
calls in CI. To re-record a cassette delete it and run the test with
TAGGING_MS_RECORD_LIVE=1 set.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ----- pytest-recording / vcrpy configuration -----


@pytest.fixture(scope="session")
def vcr_config() -> dict:
    return {
        "filter_headers": [
            ("authorization", "REDACTED"),
            ("user-agent", "tagging-ms-tests/0.2"),
            ("cookie", None),
        ],
        # The AcoustID API key arrives as a POST body field `client`.
        "filter_post_data_parameters": [
            ("client", "REDACTED"),
            ("clientversion", "REDACTED"),
        ],
        "filter_query_parameters": [
            ("client", "REDACTED"),
            ("clientversion", "REDACTED"),
        ],
        "decode_compressed_response": True,
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
    }


@pytest.fixture(scope="session", autouse=True)
def _test_env() -> Iterable[None]:
    """Provide deterministic env values for tests."""
    previous = {}
    overrides = {
        "TAGGING_MS_API_KEY": "test-bearer-token",
        "TAGGING_MS_ACOUSTID_API_KEY": "test-acoustid-key",
        "TAGGING_MS_USER_AGENT": "tagging-ms-tests/0.2",
        "GIT_SHA": "test-sha",
    }
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    yield
    for key, prior in previous.items():
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


# ----- shared fixture loaders -----


@pytest.fixture(scope="session")
def fingerprints() -> list[dict]:
    return json.loads((FIXTURES_DIR / "fingerprints.json").read_text())


@pytest.fixture(scope="session")
def carmen_release() -> dict:
    return json.loads((FIXTURES_DIR / "sample_release_carmen.json").read_text())


@pytest.fixture(scope="session")
def karajan_release() -> dict:
    return json.loads((FIXTURES_DIR / "sample_release_karajan.json").read_text())
