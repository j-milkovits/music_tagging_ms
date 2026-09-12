"""Bounded parallel release fetches in the lookup orchestrator."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from tagging_ms import ratecontrol
from tagging_ms.service import StandaloneTaggingService


class _StubClient:
    def __init__(self, concurrency: int, barrier: threading.Barrier | None = None) -> None:
        self.concurrency = concurrency
        self.barrier = barrier
        self.calls: list[str] = []

    def get_release(self, release_id: str) -> dict:
        if self.barrier is not None:
            # Only passes if `parties` calls are in flight at the same time.
            self.barrier.wait(timeout=2)
        self.calls.append(release_id)
        return {"id": release_id}


def test_fetches_run_in_parallel_and_keep_order() -> None:
    barrier = threading.Barrier(4)
    client = _StubClient(concurrency=4, barrier=barrier)
    service = StandaloneTaggingService(client=client, acoustid_client=MagicMock())  # type: ignore[arg-type]
    ids = ["r1", "r2", "r3", "r4"]
    assert service._fetch_releases(ids) == [{"id": rid} for rid in ids]
    assert sorted(client.calls) == ids


def test_sequential_when_concurrency_is_one() -> None:
    client = _StubClient(concurrency=1)
    service = StandaloneTaggingService(client=client, acoustid_client=MagicMock())  # type: ignore[arg-type]
    assert service._fetch_releases(["a", "b"]) == [{"id": "a"}, {"id": "b"}]
    assert client.calls == ["a", "b"]


def test_mock_client_without_concurrency_falls_back_to_sequential() -> None:
    service = StandaloneTaggingService(client=MagicMock(), acoustid_client=MagicMock())
    assert service.release_workers == 1


def test_parallel_fetches_report_into_the_request_counters() -> None:
    class CountingClient(_StubClient):
        def get_release(self, release_id: str) -> dict:
            ratecontrol._count("upstream_requests")
            return super().get_release(release_id)

    client = CountingClient(concurrency=3)
    service = StandaloneTaggingService(client=client, acoustid_client=MagicMock())  # type: ignore[arg-type]
    stats: dict[str, int] = {}
    token = ratecontrol.REQUEST_STATS.set(stats)
    try:
        service._fetch_releases(["a", "b", "c"])
    finally:
        ratecontrol.REQUEST_STATS.reset(token)
    assert stats == {"upstream_requests": 3}
