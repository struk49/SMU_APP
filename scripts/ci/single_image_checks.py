"""Run mocked single-image/carousel regressions inside the Linux smoke isolation."""

import json
import os
from pathlib import Path
import platform
import socket
import sys

from linux_smoke import application_revision


SUITES = (
    "test_single_image_workflows.py", "test_post_create_blueprint.py",
    "test_content_pack_blueprint.py", "test_tiktok_blueprint.py",
    "test_carousel_generation_service.py", "test_generation_contract.py",
    "test_content_pack_output.py", "test_text_model_migration.py",
    "test_tiktok_service.py", "test_usage_service.py",
    "test_post_detail_blueprint.py", "test_content_service.py",
    "test_image_generation_service.py", "test_linux_smoke_harness.py",
    "test_single_image_ci.py",
)


def validate_environment(root, state):
    import sitecustomize

    if not sitecustomize.ACTIVE:
        raise RuntimeError("Active isolation guard required")
    if sys.platform != "linux" or platform.python_version() != "3.14.3":
        raise RuntimeError("Linux and production Python 3.14.3 required")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("Loopback-only network namespace required")
    if (state / "smoke.sqlite").exists():
        raise RuntimeError("Fresh regression SQLite state required")
    return application_revision(root)


def main():
    root = Path(os.environ["SMU_SMOKE_ROOT"]).resolve(strict=True)
    state = Path(os.environ["SMU_SMOKE_STATE"]).resolve(strict=True)
    result = {"status": "failed", "python": platform.python_version(), "suites": list(SUITES)}
    try:
        result["subject"] = validate_environment(root, state)
        # Import the test runner/application only after all isolation checks pass.
        sys.path.insert(0, str(root))
        import pytest

        code = int(pytest.main([
            *(str(root / "tests" / suite) for suite in SUITES),
            "-q", "--tb=short", "--junitxml=" + str(state / "regressions.xml"),
        ]))
        result["pytest_exit_code"] = code
        result["status"] = "passed" if code == 0 else "failed"
        result["external_services"] = "blocked by network namespace and Python guard"
        return code
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        print("Isolated image regressions aborted; inspect isolation/revision configuration.", file=sys.stderr)
        return 1
    finally:
        (state / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
