from contextlib import nullcontext
from threading import Event

from smu_core.services.local_carousel_worker import (
    process_pending_once,
    run_local_carousel_worker,
)


class FakeApp:
    def app_context(self):
        return nullcontext()


class FakeLease:
    lease_seconds = 90

    def __init__(self, acquired=True):
        self.acquired = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire_or_renew(self):
        self.acquire_calls += 1
        return self.acquired

    def release(self):
        self.release_calls += 1


def test_process_pending_once_runs_existing_generation_service_with_lease():
    lease = FakeLease()
    calls = []

    result = process_pending_once(
        app=FakeApp(),
        lease_coordinator=lease,
        generate_pending_images=lambda: calls.append("provider") or {"processed_count": 1},
    )

    assert calls == ["provider"]
    assert result == {"status": "processed", "result": {"processed_count": 1}}


def test_process_pending_once_stands_by_when_another_worker_owns_lease():
    lease = FakeLease(acquired=False)
    calls = []

    result = process_pending_once(
        app=FakeApp(),
        lease_coordinator=lease,
        generate_pending_images=lambda: calls.append("provider"),
    )

    assert result == {"status": "standby"}
    assert calls == []


def test_local_worker_processes_immediately_once_and_releases_lease():
    lease = FakeLease()
    stop_event = Event()
    calls = []

    def generate():
        calls.append("provider")
        stop_event.set()
        return {"processed_count": 1}

    run_local_carousel_worker(
        app=FakeApp(),
        lease_coordinator=lease,
        generate_pending_images=generate,
        stop_event=stop_event,
        interval_seconds=0,
    )

    assert calls == ["provider"]
    assert lease.release_calls == 1
