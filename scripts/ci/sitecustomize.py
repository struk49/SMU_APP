"""CI subprocess guard, activated only by the isolated pull-request smoke workflow."""

import os
import sys

ACTIVE = False


def activate():
    global ACTIVE
    from pathlib import Path
    import socket
    import dotenv
    import dotenv.main
    from apscheduler.schedulers.background import BackgroundScheduler

    state = Path(os.environ["SMU_SMOKE_STATE"]).resolve(strict=True)
    root = Path(os.environ["SMU_SMOKE_ROOT"]).resolve(strict=True)
    if not state.is_dir() or not root.is_dir():
        raise RuntimeError("Invalid smoke directories")
    os.environ.update(
        DATABASE_URL="sqlite:///" + str(state / "smoke.sqlite"),
        SECRET_KEY="isolated-smoke-session-key-not-a-secret",
        OPENAI_API_KEY="sk-smoke-dummy-not-valid",
        OPENAI_TEXT_MODEL="gpt-6-luna",
        SMU_SCHEDULER_ENABLED="false",
        PYTHON_DOTENV_DISABLED="1",
        REGISTRATION_MODE="open",
        SMU_ADMIN_EMAILS="",
        CLOUDINARY_CLOUD_NAME="smoke-dummy",
        CLOUDINARY_API_KEY="smoke-dummy",
        CLOUDINARY_API_SECRET="smoke-dummy",
    )
    for key in (
        "MAKE_WEBHOOK_SINGLE", "MAKE_WEBHOOK_CAROUSEL", "STRIPE_SECRET_KEY",
        "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_STARTER",
        "STRIPE_PRICE_PRO", "STRIPE_PRICE_BUSINESS", "STRIPE_PRICE_ID",
        "ZERNIO_API_KEY", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET",
    ):
        os.environ[key] = ""
    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = dotenv.load_dotenv

    def prohibited(kind):
        # Never log a destination, connection string or configuration value.
        with (state / "prohibited-attempts.log").open("a", encoding="utf-8") as log:
            log.write(kind + "\n")
        raise RuntimeError("Isolated smoke prohibits " + kind)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def local_address(address):
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}

    def connect(sock, address):
        if not local_address(address):
            return prohibited("external connection")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not local_address(address):
            return prohibited("external connection")
        return original_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1"}:
            return prohibited("external DNS")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = lambda *args, **kwargs: prohibited("UDP send")
    socket.getaddrinfo = getaddrinfo
    BackgroundScheduler.start = lambda *args, **kwargs: prohibited("scheduler start")
    ACTIVE = True


if os.environ.get("SMU_SMOKE_MODE") == "1":
    try:
        activate()
    except BaseException:
        # Python normally ignores sitecustomize errors: fail closed instead.
        sys.stderr.write("Isolated smoke guard could not initialize.\n")
        os._exit(78)
