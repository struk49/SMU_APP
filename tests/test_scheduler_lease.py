from datetime import datetime, timedelta
from threading import Barrier, Thread

from sqlalchemy.orm import sessionmaker
from smu_core.models import SchedulerLease
from smu_core.services.scheduler_lease import SchedulerLeaseCoordinator


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def coordinator(module, owner_id, clock, lease_seconds=90):
    return SchedulerLeaseCoordinator(
        lease_model=module.SchedulerLease,
        db_session=module.db.session,
        owner_id=owner_id,
        lease_seconds=lease_seconds,
        now_provider=clock,
    )


def test_first_owner_acquires_and_second_cannot_take_live_lease(app, module):
    clock = Clock(datetime(2026, 9, 3, 12, 0))
    first = coordinator(module, "owner-one", clock)
    second = coordinator(module, "owner-two", clock)

    assert first.acquire_or_renew() is True
    assert second.acquire_or_renew() is False

    lease = module.db.session.get(SchedulerLease, "background_jobs")
    assert lease.owner_id == "owner-one"
    assert lease.lease_expires_at == clock.now + timedelta(seconds=90)


def test_two_sessions_racing_produce_only_one_owner(app, module):
    clock = Clock(datetime(2026, 9, 3, 12, 0))
    make_session = sessionmaker(bind=module.db.engine)
    barrier = Barrier(2)
    results = []

    def compete(owner_id):
        session = make_session()
        lease = SchedulerLeaseCoordinator(
            lease_model=module.SchedulerLease,
            db_session=session,
            owner_id=owner_id,
            now_provider=clock,
        )
        try:
            barrier.wait()
            results.append((owner_id, lease.acquire_or_renew()))
        finally:
            session.close()

    threads = [Thread(target=compete, args=(owner_id,)) for owner_id in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result for owner_id, result in results) == [False, True]
    winning_owner = next(owner_id for owner_id, result in results if result)
    assert module.db.session.get(SchedulerLease, "background_jobs").owner_id == winning_owner


def test_expired_lease_can_be_taken_over(app, module):
    clock = Clock(datetime(2026, 9, 3, 12, 0))
    first = coordinator(module, "owner-one", clock)
    second = coordinator(module, "owner-two", clock)
    assert first.acquire_or_renew() is True

    clock.now += timedelta(seconds=91)

    assert second.acquire_or_renew() is True
    assert module.db.session.get(SchedulerLease, "background_jobs").owner_id == "owner-two"


def test_owner_renews_but_wrong_owner_cannot_renew(app, module):
    clock = Clock(datetime(2026, 9, 3, 12, 0))
    first = coordinator(module, "owner-one", clock)
    second = coordinator(module, "owner-two", clock)
    assert first.acquire_or_renew() is True
    original_expiry = module.db.session.get(
        SchedulerLease,
        "background_jobs",
    ).lease_expires_at

    clock.now += timedelta(seconds=15)

    assert second.acquire_or_renew() is False
    assert first.acquire_or_renew() is True
    lease = module.db.session.get(SchedulerLease, "background_jobs")
    assert lease.owner_id == "owner-one"
    assert lease.lease_expires_at > original_expiry


def test_only_current_owner_can_release_lease(app, module):
    clock = Clock(datetime(2026, 9, 3, 12, 0))
    first = coordinator(module, "owner-one", clock)
    second = coordinator(module, "owner-two", clock)
    assert first.acquire_or_renew() is True

    assert second.release() is False
    assert first.release() is True
    assert second.acquire_or_renew() is True


def test_database_failure_fails_closed(module):
    class FailingSession:
        def execute(self, statement):
            raise RuntimeError("database unavailable")

        def rollback(self):
            pass

    lease = SchedulerLeaseCoordinator(
        lease_model=module.SchedulerLease,
        db_session=FailingSession(),
        owner_id="owner-one",
        now_provider=lambda: datetime(2026, 9, 3, 12, 0),
    )

    assert lease.acquire_or_renew() is False
