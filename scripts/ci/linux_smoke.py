"""Smoke committed restoration under real Linux Gunicorn, with no external services."""

import http.cookiejar
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

EMAIL = "linux-smoke@example.test"
PASSWORD = "local-fixture-password"
CAPTION = "Isolated Linux smoke fixture"


class CsrfParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "csrf_token":
            self.token = values.get("value")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def client():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        NoRedirect(),
    )


def request(opener, base, path, data=None):
    payload = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        response = opener.open(base + path, data=payload, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read().decode("utf-8")


def seed_fixtures():
    import app as module
    from werkzeug.security import generate_password_hash

    assert module.app.config["SMU_SCHEDULER_ENABLED"] is False
    assert not module.scheduler.running and not module.scheduler.get_jobs()
    endpoints = {rule.endpoint for rule in module.app.url_map.iter_rules()}
    assert {"view_post", "carousel_generation_status", "login", "landing_page"} <= endpoints
    with module.app.app_context():
        owner = module.User(email=EMAIL, password_hash=generate_password_hash(PASSWORD))
        other = module.User(email="other-smoke@example.test", password_hash="unused")
        module.db.session.add_all([owner, other])
        module.db.session.flush()
        rows = []
        for user, status, order in ((owner, "draft", 0), (owner, "generating", 1), (other, "draft", 2)):
            row = module.Post(
                user_id=user.id, file_url="/static/generating-image.svg", file_type="image",
                caption=CAPTION, prompt="Local fixture; never generate", status=status,
                group_id="linux-smoke-shared-group", post_type="carousel",
                platforms="instagram", sort_order=order, is_cover=order == 0,
            )
            module.db.session.add(row)
            rows.append(row)
        module.db.session.commit()
        ids = {name: row.id for name, row in zip(("draft", "pending", "foreign"), rows)}
    return ids


def check_http(base, ids):
    anonymous = client()
    assert request(anonymous, base, "/landing")[0] == 200
    for path in (f"/post/{ids['draft']}", f"/post/{ids['draft']}/generation-status"):
        status, headers, _ = request(anonymous, base, path)
        assert status == 302 and "/login" in headers["Location"]

    authenticated = client()
    status, _, html = request(authenticated, base, "/login")
    assert status == 200
    csrf = CsrfParser()
    csrf.feed(html)
    assert csrf.token
    status, _, _ = request(authenticated, base, "/login", {
        "email": EMAIL, "password": PASSWORD, "csrf_token": csrf.token,
    })
    assert status == 302
    status, _, html = request(authenticated, base, f"/post/{ids['draft']}")
    assert status == 200 and CAPTION in html and "Generating" in html
    assert f"/post/{ids['draft']}/generation-status" in html
    status, headers, body = request(authenticated, base, f"/post/{ids['draft']}/generation-status")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    payload = json.loads(body)
    assert payload == {"processing": True, "slides": [
        {"id": ids["draft"], "status": "draft", "file_url": "/static/generating-image.svg"},
        {"id": ids["pending"], "status": "generating", "file_url": None},
    ]}
    assert request(authenticated, base, f"/post/{ids['foreign']}/generation-status")[0] == 404


def check_worker(root):
    result = subprocess.run([
        sys.executable, "-c",
        "import sitecustomize; assert sitecustomize.ACTIVE; "
        "import scheduler_worker, app; "
        "assert callable(scheduler_worker.main); "
        "assert app.app.config['SMU_SCHEDULER_ENABLED'] is False; "
        "assert not app.scheduler.running; assert not app.scheduler.get_jobs()",
    ], cwd=root, capture_output=True, timeout=30)
    assert result.returncode == 0, "Production worker import failed"


def gunicorn_checks(root, state, ids):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with (state / "gunicorn.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "gunicorn", "--config", "gunicorn.conf.py",
            "--bind", f"127.0.0.1:{port}", "--workers", "1", "app:app",
        ], cwd=root, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while True:
                assert process.poll() is None, "Gunicorn exited before serving HTTP"
                try:
                    if request(client(), base, "/landing")[0] == 200:
                        break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    pass
                assert time.monotonic() < deadline, "Gunicorn readiness timed out"
                time.sleep(0.2)
            check_http(base, ids)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def diagnostics(state):
    log = state / "gunicorn.log"
    if not log.exists():
        return ""
    tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
    for key in ("SECRET_KEY", "OPENAI_API_KEY", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        value = os.environ.get(key)
        if value:
            tail = tail.replace(value, "[redacted]")
    return re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "[redacted-url]", tail)


def application_revision(root):
    subject = os.environ["SMU_SMOKE_SUBJECT"]
    if re.fullmatch(r"[0-9a-f]{40}", subject) is None:
        raise ValueError("Explicit application SHA required")
    actual = subprocess.check_output(["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if actual != subject:
        raise ValueError("Application checkout differs from the reported SHA")
    return actual


def main():
    import sitecustomize

    assert sitecustomize.ACTIVE, "Smoke guard must be active before any app import"
    root = Path(os.environ["SMU_SMOKE_ROOT"]).resolve(strict=True)
    state = Path(os.environ["SMU_SMOKE_STATE"]).resolve(strict=True)
    subject = application_revision(root)
    result = {"status": "failed", "subject": subject, "python": platform.python_version(), "checks": []}
    phase = "Linux network isolation"
    try:
        assert sys.platform == "linux", "This smoke requires Linux"
        assert {name for _, name in socket.if_nameindex()} == {"lo"}, "Loopback-only namespace required"
        assert not (state / "smoke.sqlite").exists(), "Fresh temporary SQLite required"
        result["checks"].append("loopback-only Linux namespace")
        sys.path.insert(0, str(root))
        phase = "app initialization and local fixtures"
        ids = seed_fixtures()
        result["checks"].append("app and authenticated route registration")
        phase = "production worker import"
        check_worker(root)
        result["checks"].append("worker import without scheduler or jobs")
        phase = "Gunicorn and authenticated HTTP"
        gunicorn_checks(root, state, ids)
        result["checks"].extend(["Gunicorn production config and app:app", "public HTTP", "CSRF login and post rendering", "owner-scoped generation status"])
        phase = "external service and scheduler audit"
        assert not (state / "prohibited-attempts.log").exists(), "Unexpected prohibited service/job attempt"
        result.update(status="passed", external_service_attempts=0, scheduler_starts=0)
    except Exception as exc:
        result["failed_phase"] = phase
        result["error_type"] = type(exc).__name__
        print("Smoke failed: " + phase + " (" + type(exc).__name__ + ")", file=sys.stderr)
        print(diagnostics(state), file=sys.stderr)
        return 1
    finally:
        (state / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
