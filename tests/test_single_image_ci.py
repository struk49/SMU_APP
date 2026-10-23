"""CI preflight checks only; local tests do not establish Linux compatibility."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


SUPPORT = Path(__file__).resolve().parents[1] / "scripts" / "ci"
spec = importlib.util.spec_from_file_location("single_image_checks", SUPPORT / "single_image_checks.py")
checks = importlib.util.module_from_spec(spec)
# linux_smoke is a sibling import in the actual CI invocation.
sys.path.insert(0, str(SUPPORT))
spec.loader.exec_module(checks)
sys.path.remove(str(SUPPORT))


@pytest.fixture()
def preflight(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sitecustomize", SimpleNamespace(ACTIVE=True))
    monkeypatch.setattr(checks.sys, "platform", "linux")
    monkeypatch.setattr(checks.platform, "python_version", lambda: "3.14.3")
    monkeypatch.setattr(checks.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(checks, "application_revision", lambda root: "a" * 40)
    return tmp_path


def test_ci_preflight_accepts_explicit_isolated_revision(preflight):
    assert checks.validate_environment(preflight, preflight) == "a" * 40


@pytest.mark.parametrize("fault", ["guard", "os", "python", "network", "database", "revision"])
def test_ci_preflight_fails_closed(preflight, monkeypatch, fault):
    if fault == "guard":
        monkeypatch.setitem(sys.modules, "sitecustomize", SimpleNamespace(ACTIVE=False))
    elif fault == "os":
        monkeypatch.setattr(checks.sys, "platform", "win32")
    elif fault == "python":
        monkeypatch.setattr(checks.platform, "python_version", lambda: "3.13.0")
    elif fault == "network":
        monkeypatch.setattr(checks.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    elif fault == "database":
        (preflight / "smoke.sqlite").touch()
    else:
        def invalid(root):
            raise ValueError("Unexpected revision")
        monkeypatch.setattr(checks, "application_revision", invalid)
    with pytest.raises((RuntimeError, ValueError)):
        checks.validate_environment(preflight, preflight)
