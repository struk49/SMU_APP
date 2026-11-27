"""CI comparison policy tests; these do not establish Linux compatibility."""

from copy import deepcopy
import builtins
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("security_image_checks", ROOT / "scripts/ci/security_image_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
HEAD = "a" * 40


@pytest.fixture()
def results():
    failure = sorted(checks.KNOWN_FAILURES)[0]
    old = {
        "status": "completed", "python": "3.14.3", "platform": "linux", "pillow": "11.3.0",
        "subject": checks.BASELINE, "pytest_exit_code": 1,
        "collected": [failure, checks.REPLACED_TEST], "failed": [failure], "skipped": [],
        "passed": 1, "blocked_attempts_by_test": {},
        "legacy_visual_evidence": {
            "test": checks.LEGACY_VISUAL_TEST, "measurement": {"fits": True},
            "render_sha256": "1" * 64, "font_sha256": "2" * 64, "freetype": "fixture",
        },
    }
    new = deepcopy(old)
    new.update(subject=HEAD, passed=91,
        collected=[failure] + [f"tests/test_image_upload_security.py::case[{n}]" for n in range(91)])
    return old, new


def test_comparison_reports_known_failures_without_claiming_full_suite_green(results):
    report = checks.compare(*results, HEAD)
    assert report["subject"] == HEAD and report["baseline"] == checks.BASELINE
    assert report["new_security_cases"] == 91
    assert report["full_suite_green"] is False
    assert report["known_failures_remaining"] == results[0]["failed"]


@pytest.mark.parametrize("fault", [
    "baseline_sha", "head_sha", "same_sha", "runtime", "platform", "pillow", "incomplete",
    "unknown_baseline_failure", "new_failure", "missing_old_test", "too_few_security_tests",
    "skipped_security_test", "changed_old_skips", "service_attempt", "missing_outcome",
])
def test_comparison_fails_closed(results, fault):
    old, new = results
    if fault == "baseline_sha":
        old["subject"] = "b" * 40
    elif fault == "head_sha":
        new["subject"] = "b" * 40
    elif fault == "same_sha":
        new["subject"] = checks.BASELINE
    elif fault in ("runtime", "platform", "pillow", "incomplete"):
        key, value = {"runtime": ("python", "3.13.14"), "platform": ("platform", "win32"),
                      "pillow": ("pillow", "13.0.0"), "incomplete": ("pytest_exit_code", 2)}[fault]
        new[key] = value
    elif fault == "unknown_baseline_failure":
        for result in (old, new):
            result["collected"].append("tests/unknown.py::failure")
            result["failed"].append("tests/unknown.py::failure")
    elif fault == "new_failure":
        new["failed"].append(new["collected"][-1])
        new["passed"] -= 1
    elif fault == "missing_old_test":
        old["collected"].append("tests/old.py::required")
        old["passed"] += 1
    elif fault == "too_few_security_tests":
        new["collected"].pop()
        new["passed"] -= 1
    elif fault == "skipped_security_test":
        new["skipped"] = [new["collected"][-1]]
        new["passed"] -= 1
    elif fault == "changed_old_skips":
        for result in (old, new):
            result["collected"].append("tests/old.py::required")
            result["passed"] += 1
        new["skipped"] = ["tests/old.py::required"]
        new["passed"] -= 1
    elif fault == "service_attempt":
        new["blocked_attempts_by_test"] = {new["collected"][-1]: ["external DNS"]}
    else:
        new["passed"] -= 1
    with pytest.raises(ValueError):
        checks.compare(old, new, HEAD)


def test_attempt_tracking_records_kind_and_test_id_only(tmp_path):
    records = checks.Results(tmp_path)
    records.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid="tests/local.py::case")]))
    records.pytest_runtest_logreport(SimpleNamespace(nodeid="tests/local.py::case", failed=False,
                                                   skipped=False, when="call"))
    (tmp_path / "prohibited-attempts.log").write_text("external DNS\n")
    records.pytest_runtest_logfinish("tests/local.py::case", None)
    assert records.passed == {"tests/local.py::case"}
    assert records.blocked == {"tests/local.py::case": ["external DNS"]}


def test_runner_rejects_failed_isolation_before_app_import(tmp_path, monkeypatch):
    monkeypatch.setenv("SMU_SMOKE_ROOT", str(tmp_path))
    monkeypatch.setenv("SMU_SMOKE_STATE", str(tmp_path))
    def reject(*args):
        raise RuntimeError("Isolation rejected")
    monkeypatch.setitem(checks.sys.modules, "single_image_checks", SimpleNamespace(validate_environment=reject))
    original_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name == "app":
            raise AssertionError("Application import must follow isolation checks")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", checked_import)
    assert checks.run() == 1
    assert json.loads((tmp_path / "security-result.json").read_text()) == {
        "status": "infrastructure_failed", "error_type": "RuntimeError",
    }


def test_proven_linux_snapshot_failure_requires_matching_render_evidence(results):
    old, new = results
    for result in (old, new):
        result["collected"].append(checks.LEGACY_VISUAL_TEST)
        result["failed"].append(checks.LEGACY_VISUAL_TEST)
    report = checks.compare(old, new, HEAD)
    assert checks.LEGACY_VISUAL_TEST in report["known_failures_remaining"]
    assert report["full_suite_green"] is False
    assert report["legacy_visual_evidence"] == old["legacy_visual_evidence"]


@pytest.mark.parametrize("field,value", [
    ("measurement", {"fits": False}), ("render_sha256", "3" * 64),
    ("font_sha256", "4" * 64), ("freetype", "different"),
])
def test_existing_snapshot_failure_does_not_hide_visual_regressions(results, field, value):
    old, new = results
    new["legacy_visual_evidence"][field] = value
    with pytest.raises(ValueError, match="Legacy visual output"):
        checks.compare(old, new, HEAD)
