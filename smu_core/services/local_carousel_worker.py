"""Local-only loop for processing queued carousel images."""

import threading


def process_pending_once(*, app, lease_coordinator, generate_pending_images):
    """Run one image batch only when this process owns the shared worker lease."""
    with app.app_context():
        if not lease_coordinator.acquire_or_renew():
            return {"status": "standby"}

    heartbeat_stop = threading.Event()
    heartbeat_interval = max(1, lease_coordinator.lease_seconds // 3)

    def renew_lease():
        while not heartbeat_stop.wait(heartbeat_interval):
            with app.app_context():
                if not lease_coordinator.acquire_or_renew():
                    return

    heartbeat = threading.Thread(target=renew_lease, daemon=True)
    heartbeat.start()
    try:
        with app.app_context():
            return {"status": "processed", "result": generate_pending_images()}
    finally:
        heartbeat_stop.set()
        heartbeat.join()


def run_local_carousel_worker(
    *,
    app,
    lease_coordinator,
    generate_pending_images,
    stop_event,
    interval_seconds=20,
):
    """Process immediately, then repeat until the local process is stopped."""
    try:
        while not stop_event.is_set():
            process_pending_once(
                app=app,
                lease_coordinator=lease_coordinator,
                generate_pending_images=generate_pending_images,
            )
            if stop_event.wait(interval_seconds):
                break
    finally:
        with app.app_context():
            lease_coordinator.release()
