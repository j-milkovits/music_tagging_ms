"""/api/health mirror reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import tagging_ms.api as api_module
from tagging_ms import health
from tagging_ms.musicbrainz import MusicBrainzClient

MIRROR = "http://musicbrainz:5000/ws/2"


def _status_file(tmp_path: Path, last: datetime | None, **extra: object) -> Path:
    payload: dict[str, object] = {"schema_sequence": 30, "replication_sequence": 123, **extra}
    if last is not None:
        payload["last_replication"] = last.isoformat()
    file = tmp_path / "replication-status.json"
    file.write_text(json.dumps(payload))
    return file


def test_public_api_is_not_a_mirror() -> None:
    assert not health.is_mirror(MusicBrainzClient(base_url="https://musicbrainz.org/ws/2"))
    assert health.is_mirror(MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0))


def test_lag_hours_parses_postgres_timestamps() -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert health.lag_hours("2026-09-19T03:00:00+00:00", now) == 9.0
    assert health.lag_hours("2026-09-19 03:00:00.123456+00", now) == 9.0
    assert health.lag_hours("2026-09-19T05:00:00", now) == 7.0  # naive -> UTC
    assert health.lag_hours(None, now) is None
    assert health.lag_hours("garbage", now) is None


def test_fresh_reachable_mirror_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _status_file(tmp_path, datetime.now(UTC) - timedelta(hours=6))
    monkeypatch.setattr(health, "probe", lambda client: True)
    m = health.mirror_health(MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0), str(file))
    assert m.reachable and not m.degraded
    assert (m.schema_sequence, m.replication_sequence) == (30, 123)
    assert m.lag_hours is not None and 5.9 < m.lag_hours < 6.1


def test_stale_replication_is_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _status_file(tmp_path, datetime.now(UTC) - timedelta(hours=49))
    monkeypatch.setattr(health, "probe", lambda client: True)
    m = health.mirror_health(MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0), str(file))
    assert m.degraded


def test_missing_status_file_is_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "probe", lambda client: True)
    m = health.mirror_health(MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0), str(tmp_path / "nope"))
    assert m.degraded and m.lag_hours is None


def test_unreachable_mirror_is_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _status_file(tmp_path, datetime.now(UTC))
    monkeypatch.setattr(health, "probe", lambda client: False)
    m = health.mirror_health(MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0), str(file))
    assert m.degraded and not m.reachable


def test_endpoint_reports_mirror_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _status_file(tmp_path, datetime.now(UTC) - timedelta(hours=1))
    monkeypatch.setenv("TAGGING_MS_REPLICATION_STATUS_FILE", str(file))
    monkeypatch.setattr(health, "probe", lambda client: True)
    monkeypatch.setattr(
        api_module.service, "client", MusicBrainzClient(base_url=MIRROR, rate_limit_ms=0)
    )
    body = TestClient(api_module.app).get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mirror"]["reachable"] is True
    assert body["mirror"]["replication_sequence"] == 123


def test_endpoint_without_mirror_stays_minimal() -> None:
    assert TestClient(api_module.app).get("/api/health").json() == {"status": "ok"}
