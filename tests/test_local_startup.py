"""Real isolated entrypoint subprocesses; fake app/worker, no providers or DB servers."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
POSTGRES = "postgresql://secret-user:secret-password@invalid.invalid/private"


@pytest.fixture
def sandbox(tmp_path):
    for name in ("local_carousel_worker.py", "local_startup.py"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/start_local.ps1", tmp_path / "scripts/start_local.ps1")
    (tmp_path / ".env").write_text("")  # prevent dotenv discovery outside sandbox
    (tmp_path / "app.py").write_text("""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
Path('app-imported').touch()
assert os.environ.get('ALLOW_FAKE_APP') == 'yes', 'app imported before rejection'
import sqlite3
from sqlalchemy.engine import make_url
with sqlite3.connect(make_url(os.environ['DATABASE_URL']).database) as connection:
    connection.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER)')
Path('database-initialized').touch()
from types import SimpleNamespace
app = SimpleNamespace()
def generate_pending_carousel_images():
    raise AssertionError('No image calls allowed')
if __name__ == '__main__':
    Path('web-started').touch()
""")
    stubs = {
        "smu_core/__init__.py": "",
        "smu_core/extensions.py": "from types import SimpleNamespace\ndb = SimpleNamespace(session=None)\n",
        "smu_core/models/__init__.py": "",
        "smu_core/models/scheduler_lease.py": "SchedulerLease = object\n",
        "smu_core/services/__init__.py": "",
        "smu_core/services/scheduler_lease.py": "class SchedulerLeaseCoordinator:\n    def __init__(self, **kwargs): pass\n",
        "smu_core/services/local_carousel_worker.py": "from pathlib import Path\ndef run_local_carousel_worker(**kwargs):\n    Path('worker-started').touch()\n",
    }
    for name, source in stubs.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return tmp_path


def environment(**settings):
    # Do not carry local credentials or dotenv toggles into acceptance subprocesses.
    result = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT",
    }}
    result.update(settings)
    return result


def run_worker(sandbox, env, *args):
    return subprocess.run([sys.executable, str(sandbox / "local_carousel_worker.py"), *args],
                          cwd=sandbox, env=env, capture_output=True, text=True, timeout=20)


def run_launcher(sandbox, env):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell unavailable; launcher subprocess checks require it")
    # Real script/control flow, with a trap replacing its worker process creation.
    harness = sandbox / "launch-audit.ps1"
    harness.write_text("""
$ErrorActionPreference = 'Stop'
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $RedirectStandardOutput,
          $RedirectStandardError, $WindowStyle, [switch]$PassThru)
    New-Item -ItemType File -Path 'worker-launched' | Out-Null
    return [PSCustomObject]@{HasExited=$true}
}
& $PSScriptRoot/scripts/start_local.ps1 -PythonPath $env:AUDIT_PYTHON
""")
    env = dict(env, AUDIT_PYTHON=sys.executable)
    return subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(harness)], cwd=sandbox, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("entrypoint", ["worker", "launcher"])
@pytest.mark.parametrize("settings,dotenv", [
    ({"DATABASE_URL": POSTGRES, "SMU_SCHEDULER_ENABLED": "false"}, ""),
    ({}, f"DATABASE_URL={POSTGRES}\nSMU_SCHEDULER_ENABLED=false\n"),
    ({"DATABASE_URL": POSTGRES, "SMU_SCHEDULER_ENABLED": "false"}, "DATABASE_URL=sqlite:///audit.db\nSMU_SCHEDULER_ENABLED=false\n"),
    ({"DATABASE_URL": "sqlite:///audit.db", "SMU_SCHEDULER_ENABLED": "false"}, f"DATABASE_URL={POSTGRES}\nSMU_SCHEDULER_ENABLED=false\n"),
    ({"DATABASE_URL": "sqlite:///audit.db", "SMU_SCHEDULER_ENABLED": "true"}, "SMU_SCHEDULER_ENABLED=false\n"),
    ({"DATABASE_URL": "sqlite:///audit.db", "SMU_SCHEDULER_ENABLED": "false"}, "SMU_SCHEDULER_ENABLED=true\n"),
    ({}, ""),
    ({"DATABASE_URL": "sqlite:///audit.db"}, ""),
    ({"SMU_SCHEDULER_ENABLED": "false"}, ""),
    ({"DATABASE_URL": "", "SMU_SCHEDULER_ENABLED": "false"}, "DATABASE_URL=sqlite:///audit.db\n"),
    ({"DATABASE_URL": "sqlite:///audit.db", "SMU_SCHEDULER_ENABLED": "disabled"}, ""),
    ({"DATABASE_URL": "not-a-url secret-password", "SMU_SCHEDULER_ENABLED": "false"}, ""),
    ({"DATABASE_URL": "sqlite:///:memory:", "SMU_SCHEDULER_ENABLED": "false"}, ""),
    ({"DATABASE_URL": "sqlite:///audit.db?uri=true", "SMU_SCHEDULER_ENABLED": "false"}, ""),
    ({}, f"database_url={POSTGRES}\nDATABASE_URL=sqlite:///audit.db\nSMU_SCHEDULER_ENABLED=false\n"),
    ({}, "DATABASE_URL=sqlite:///audit.db\nDATABASE_URL=sqlite:///other.db\nSMU_SCHEDULER_ENABLED=false\n"),
    ({}, 'DATABASE_URL="unterminated\nSMU_SCHEDULER_ENABLED=false\n'),
    ({"PYTHON_DOTENV_DISABLED": "true"}, "DATABASE_URL=sqlite:///audit.db\nSMU_SCHEDULER_ENABLED=false\n"),
])
def test_invalid_configuration_rejected_before_app_database_or_launch(sandbox, entrypoint, settings, dotenv):
    (sandbox / ".env").write_text(dotenv)
    run = run_worker if entrypoint == "worker" else run_launcher
    result = run(sandbox, environment(**settings))
    assert result.returncode != 0
    for marker in ("app-imported", "database-initialized", "worker-launched", "web-started", "audit.db"):
        assert not (sandbox / marker).exists()
    output = result.stdout + result.stderr
    for secret in (POSTGRES, "secret-user", "secret-password", "invalid.invalid", "private"):
        assert secret not in output


@pytest.mark.parametrize("entrypoint", ["worker", "launcher"])
@pytest.mark.parametrize("source", ["environment", "dotenv", "matching", "interpolation"])
def test_explicit_sqlite_false_configuration_starts_isolated_app(sandbox, entrypoint, source):
    settings = {"ALLOW_FAKE_APP": "yes"}
    if source in {"environment", "matching"}:
        settings.update(DATABASE_URL="sqlite:///audit.db", SMU_SCHEDULER_ENABLED="false")
    if source == "matching":
        settings["SMU_SCHEDULER_ENABLED"] = "OFF"
    if source in {"dotenv", "matching"}:
        (sandbox / ".env").write_text("DATABASE_URL=sqlite:///audit.db\nSMU_SCHEDULER_ENABLED=false\n")
    if source == "interpolation":
        settings.update(LOCAL_DB="sqlite:///audit.db", LOCAL_DISABLED="false")
        (sandbox / ".env").write_text("LOCAL_DB=postgresql://invalid.invalid/shadowed\nDATABASE_URL=${LOCAL_DB}\nSMU_SCHEDULER_ENABLED=${LOCAL_DISABLED}\n")
    run = run_worker if entrypoint == "worker" else run_launcher
    result = run(sandbox, environment(**settings))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (sandbox / "app-imported").exists()
    assert (sandbox / "database-initialized").exists()
    assert (sandbox / "audit.db").exists()
    expected = "worker-started" if entrypoint == "worker" else "web-started"
    assert (sandbox / expected).exists()
    if entrypoint == "launcher":
        assert (sandbox / "worker-launched").exists()


def test_check_only_does_not_import_app_even_when_valid(sandbox):
    result = run_worker(sandbox, environment(DATABASE_URL="sqlite:///audit.db", SMU_SCHEDULER_ENABLED="false"), "--check-config")
    assert result.returncode == 0
    assert not (sandbox / "app-imported").exists()
    assert not (sandbox / "audit.db").exists()
