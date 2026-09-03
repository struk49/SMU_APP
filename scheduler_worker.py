import signal
import threading

from app import start_background_scheduler, stop_background_scheduler


stop_event = threading.Event()


def request_stop(signum, frame):
    stop_event.set()


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    start_background_scheduler()
    try:
        while not stop_event.wait(1):
            pass
    finally:
        stop_background_scheduler()


if __name__ == "__main__":
    main()
