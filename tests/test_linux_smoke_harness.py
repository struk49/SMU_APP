"""Local harness/guard regressions; these do not assert Linux/Gunicorn compatibility."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "scripts/ci"


def run_guard(tmp_path, code, state_exists=True):
    state = tmp_path / "state"
    if state_exists:
        state.mkdir()
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT",
    }}
    env.update(SMU_SMOKE_MODE="1", SMU_SMOKE_ROOT=str(ROOT),
               SMU_SMOKE_STATE=str(state), PYTHONPATH=str(SUPPORT),
               DATABASE_URL="postgresql://invalid.invalid/never-connect",
               SMU_SCHEDULER_ENABLED="true", OPENAI_API_KEY="inherited-must-be-replaced")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=45)
    return result, state


def test_guard_fails_closed_before_script_when_state_missing(tmp_path):
    result, _ = run_guard(tmp_path, "raise AssertionError('script must not run')", False)
    assert result.returncode == 78
    assert "guard could not initialize" in result.stderr
    assert "script must not run" not in result.stderr


def test_guard_replaces_ambient_configuration_without_app_import(tmp_path):
    result, state = run_guard(tmp_path, """
import os, sys, sitecustomize, dotenv
assert sitecustomize.ACTIVE
assert 'app' not in sys.modules
assert os.environ['DATABASE_URL'].startswith('sqlite:///')
assert os.environ['SMU_SCHEDULER_ENABLED'] == 'false'
assert os.environ['OPENAI_API_KEY'] == 'sk-smoke-dummy-not-valid'
assert os.environ['MAKE_WEBHOOK_SINGLE'] == ''
assert dotenv.load_dotenv() is False
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (state / "smoke.sqlite").exists()


def test_external_network_and_scheduler_attempts_are_blocked_and_recorded(tmp_path):
    result, state = run_guard(tmp_path, """
import socket
from apscheduler.schedulers.background import BackgroundScheduler
operations = [
    lambda: socket.create_connection(('203.0.113.1', 443)),
    lambda: socket.getaddrinfo('provider.invalid', 443),
    lambda: socket.socket().connect_ex(('203.0.113.1', 443)),
    lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', ('203.0.113.1', 53)),
    lambda: BackgroundScheduler().start(),
]
for operation in operations:
    try:
        operation()
    except RuntimeError:
        continue
    raise AssertionError('prohibited operation was allowed')
""")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = (state / "prohibited-attempts.log").read_text().splitlines()
    assert len(lines) == 5
    assert all("provider.invalid" not in line and "203.0.113.1" not in line for line in lines)


def test_fixture_auth_http_and_worker_import_with_blocked_services(tmp_path):
    result, state = run_guard(tmp_path, """
import sys, threading
sys.path.insert(0, '.')
import linux_smoke
import app
from werkzeug.serving import make_server
ids = linux_smoke.seed_fixtures()
linux_smoke.check_worker('.')
server = make_server('127.0.0.1', 0, app.app, threaded=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    linux_smoke.check_http('http://127.0.0.1:' + str(server.server_port), ids)
finally:
    server.shutdown()
    thread.join(timeout=5)
assert not app.scheduler.running
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "smoke.sqlite").exists()
    assert not (state / "prohibited-attempts.log").exists()


def test_failure_diagnostics_redact_connection_strings_and_dummy_credentials(tmp_path):
    result, _ = run_guard(tmp_path, """
import os
from pathlib import Path
import linux_smoke
state = Path(os.environ['SMU_SMOKE_STATE'])
(state / 'gunicorn.log').write_text('failure sqlite:///temporary.db ' + os.environ['OPENAI_API_KEY'] + ' https://user:password@invalid.invalid/path')
output = linux_smoke.diagnostics(state)
assert 'sqlite:///' not in output and 'https://' not in output
assert 'password' not in output and os.environ['OPENAI_API_KEY'] not in output
assert 'failure' in output and '[redacted-url]' in output
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_application_revision_is_explicit_and_matches_checkout(tmp_path):
    result, _ = run_guard(tmp_path, """
import os, subprocess
from pathlib import Path
import linux_smoke
root = Path(os.environ['SMU_SMOKE_ROOT'])
sha = subprocess.check_output(['git', '-c', f'safe.directory={root}', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
os.environ['SMU_SMOKE_SUBJECT'] = sha
assert linux_smoke.application_revision(root) == sha
for invalid in ('0' * 40, 'not-a-sha'):
    os.environ['SMU_SMOKE_SUBJECT'] = invalid
    try:
        linux_smoke.application_revision(root)
    except ValueError:
        continue
    raise AssertionError('Incorrect application revision accepted')
""")
    assert result.returncode == 0, result.stdout + result.stderr
