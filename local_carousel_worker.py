"""Opt-in local carousel image worker; never runs publishing jobs."""

import os
import signal
import threading
import uuid

from app import app, generate_pending_carousel_images
from smu_core.extensions import db
from smu_core.models.scheduler_lease import SchedulerLease
from smu_core.services.local_carousel_worker import run_local_carousel_worker
from smu_core.services.scheduler_lease import SchedulerLeaseCoordinator


stop_event = threading.Event()


def request_stop(signum, frame):
    stop_event.set()


def main():
    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not database_uri.startswith("sqlite:"):
        raise RuntimeError("The local carousel worker requires an isolated SQLite database.")
    if app.config.get("SMU_SCHEDULER_ENABLED", True):
        raise RuntimeError(
            "Set SMU_SCHEDULER_ENABLED=false before starting the local image worker."
        )

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    coordinator = SchedulerLeaseCoordinator(
        lease_model=SchedulerLease,
        db_session=db.session,
        owner_id=f"local-images:{os.getpid()}:{uuid.uuid4()}",
        lease_name="background_jobs",
        lease_seconds=90,
    )
    run_local_carousel_worker(
        app=app,
        lease_coordinator=coordinator,
        generate_pending_images=generate_pending_carousel_images,
        stop_event=stop_event,
    )


if __name__ == "__main__":
    main()
