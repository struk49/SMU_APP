"""Isolated Linux full-suite comparison; never a deployment entrypoint."""

import argparse
import ast
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import platform
import sys


BASELINE = "b84ce74b94d280fd24a1bd3512fdae414f668873"
LEGACY_VISUAL_TEST = "tests/test_social_text_service.py::test_structured_legacy_typography_and_render_match_c6b82fa_snapshot"
KNOWN_FAILURES = {
    # Both Linux revisions reproduce the stored snapshot's three-pixel bounds
    # mismatch. Require identical full render evidence below; do not alter it.
    LEGACY_VISUAL_TEST,
    "tests/test_linkedin_publishing.py::test_create_route_rejects_blank_linkedin_text_only_caption",
    "tests/test_public_landing.py::test_landing_sections_render_current_product_capabilities",
    "tests/test_scheduler_service.py::test_scheduler_defaults_on_for_local_development",
    "tests/test_usage_credit_limits.py::test_image_limit_blocks_generation_without_openai_call",
    "tests/test_usage_credit_limits.py::test_failed_ai_image_generation_releases_reserved_credit",
    "tests/test_usage_credit_limits.py::test_tiktok_single_draft_db_failure_releases_reserved_credit",
    *("tests/test_local_startup.py::test_explicit_sqlite_false_configuration_starts_isolated_app["
      + source + "-launcher]" for source in ("environment", "dotenv", "matching", "interpolation")),
}
# The security patch deliberately replaces the old invalid-artwork canvas fallback.
REPLACED_TEST = "tests/test_social_text_service.py::test_invalid_viral_artwork_falls_back_to_safe_local_canvas"


def legacy_visual_evidence(root):
    """Measure/render the unchanged snapshot fixture without replacing its test."""
    from PIL import Image, features
    from smu_core.services import social_text

    source = ast.parse((root / "tests/test_social_text_service.py").read_text(encoding="utf-8"))
    test = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                and node.name == LEGACY_VISUAL_TEST.split("::")[1])
    arguments = next(ast.literal_eval(node.value) for node in test.body
                     if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "arguments" for target in node.targets))
    measurement = social_text.preflight_viral_carousel_text(**arguments)
    image = Image.new("RGB", (1024, 1024), (35, 57, 89))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    rendered = social_text.render_social_text(
        buffer.getvalue(), campaign_style="photorealistic",
        campaign_palette="warm_sunset", **arguments,
    )
    return {
        "test": LEGACY_VISUAL_TEST, "measurement": measurement,
        "render_sha256": hashlib.sha256(rendered).hexdigest(),
        "font_sha256": hashlib.sha256(social_text.FONT_PATH.read_bytes()).hexdigest(),
        "freetype": features.version("freetype2"),
    }


class Results:
    def __init__(self, state):
        self.state = state
        self.collected = []
        self.failed = set()
        self.skipped = set()
        self.passed = set()
        self.blocked = {}
        self.seen_attempts = 0

    def pytest_collection_finish(self, session):
        self.collected = sorted(item.nodeid for item in session.items)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.add(report.nodeid)
        elif report.skipped:
            self.skipped.add(report.nodeid)
        elif report.when == "call":
            self.passed.add(report.nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        log = self.state / "prohibited-attempts.log"
        lines = log.read_text().splitlines() if log.exists() else []
        if len(lines) > self.seen_attempts:
            # Guard records allowlisted kinds only, never destinations or errors.
            self.blocked[nodeid] = lines[self.seen_attempts:]
            self.seen_attempts = len(lines)


def run():
    root = Path(os.environ["SMU_SMOKE_ROOT"]).resolve(strict=True)
    state = Path(os.environ["SMU_SMOKE_STATE"]).resolve(strict=True)
    result = {"status": "infrastructure_failed"}
    try:
        from single_image_checks import validate_environment

        subject = validate_environment(root, state)
        if version("Pillow") != "11.3.0":
            raise RuntimeError("Committed Pillow version required")
        os.chdir(root)
        sys.path.insert(0, str(root))
        logging.getLogger("smu").addHandler(logging.NullHandler())
        # Validate the actual engine before fixtures can create/drop any rows.
        import app
        with app.app.app_context():
            url = app.db.engine.url
            if url.get_backend_name() != "sqlite" or Path(url.database).resolve() != state / "smoke.sqlite":
                raise RuntimeError("Isolated SQLite engine required")
        if app.app.config["SMU_SCHEDULER_ENABLED"] or app.scheduler.running:
            raise RuntimeError("Scheduler must remain disabled")
        import pytest

        records = Results(state)
        code = int(pytest.main([
            "tests", "-q", "--tb=short", "-p", "no:cacheprovider",
            "--junitxml=" + str(state / "tests.xml"),
        ], plugins=[records]))
        result.update(
            status="completed" if code in (0, 1) else "infrastructure_failed",
            subject=subject, python=platform.python_version(), platform=sys.platform,
            pillow=version("Pillow"), pytest_exit_code=code,
            collected=records.collected, failed=sorted(records.failed),
            skipped=sorted(records.skipped), passed=len(records.passed - records.failed),
            blocked_attempts_by_test=records.blocked,
            legacy_visual_evidence=legacy_visual_evidence(root),
        )
        # A test-failure exit is evaluated by compare(), never silently ignored.
        return 0 if code in (0, 1) else 1
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        print("Security comparison aborted before completion; inspect isolation checks.", file=sys.stderr)
        return 1
    finally:
        (state / "security-result.json").write_text(json.dumps(result, indent=2) + "\n")


def compare(baseline, patched, expected_head):
    for result in (baseline, patched):
        if (result["status"] != "completed" or result["python"] != "3.14.3"
                or result["platform"] != "linux" or result["pillow"] != "11.3.0"
                or result["pytest_exit_code"] not in (0, 1) or not result["collected"]):
            raise ValueError("Incomplete or incompatible comparison result")
        if (not (set(result["failed"]) | set(result["skipped"])) <= set(result["collected"])
                or result["passed"] + len(result["failed"]) + len(result["skipped"]) != len(result["collected"])):
            raise ValueError("Test outcomes do not cover the collected suite")
    if baseline["subject"] != BASELINE or patched["subject"] != expected_head or expected_head == BASELINE:
        raise ValueError("Comparison revisions differ from approved subjects")
    old_failures, new_failures = set(baseline["failed"]), set(patched["failed"])
    if not old_failures <= KNOWN_FAILURES or new_failures != old_failures:
        raise ValueError("Unexpected baseline failure or changed failure set")
    old_visual, new_visual = baseline["legacy_visual_evidence"], patched["legacy_visual_evidence"]
    if (old_visual != new_visual or old_visual.get("test") != LEGACY_VISUAL_TEST
            or not old_visual.get("measurement") or not old_visual.get("freetype")
            or any(len(old_visual.get(key, "")) != 64 for key in ("render_sha256", "font_sha256"))):
        raise ValueError("Legacy visual output differs or evidence is incomplete")
    old_tests, new_tests = set(baseline["collected"]), set(patched["collected"])
    if old_tests - new_tests - {REPLACED_TEST}:
        raise ValueError("Existing tests disappeared")
    added = new_tests - old_tests
    security_tests = {name for name in added if name.startswith("tests/test_image_upload_security.py::")}
    if len(security_tests) < 91 or added & (new_failures | set(patched["skipped"])):
        raise ValueError("New security/CI tests missing, failing or skipped")
    if set(baseline["skipped"]) != set(patched["skipped"]) & old_tests:
        raise ValueError("Existing test skip set changed")
    if baseline["blocked_attempts_by_test"] != patched["blocked_attempts_by_test"]:
        raise ValueError("Blocked service-attempt pattern changed")
    return {
        "status": "baseline parity and new security checks passed",
        "baseline": BASELINE, "subject": expected_head, "python": "3.14.3", "pillow": "11.3.0",
        "baseline_counts": {key: len(baseline[key]) for key in ("collected", "failed", "skipped")},
        "patched_counts": {key: len(patched[key]) for key in ("collected", "failed", "skipped")},
        "new_security_cases": len(security_tests),
        "known_failures_remaining": sorted(old_failures),
        "full_suite_green": not old_failures,
        "blocked_attempts_by_test": patched["blocked_attempts_by_test"],
        "legacy_visual_evidence": new_visual,
        "scope": "Explicit PR head; synthetic merge is not tested. No deployment.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "compare"))
    parser.add_argument("--baseline")
    parser.add_argument("--patched")
    parser.add_argument("--head")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.mode == "run":
        return run()
    try:
        result = compare(json.loads(Path(args.baseline).read_text()),
                         json.loads(Path(args.patched).read_text()), args.head)
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("Security baseline comparison failed (" + type(exc).__name__ + ").", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
