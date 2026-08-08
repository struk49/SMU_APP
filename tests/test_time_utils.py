from datetime import datetime, timezone
import inspect

from smu_core.services import time_utils


def test_utc_now_returns_datetime():
    assert isinstance(time_utils.utc_now(), datetime)


def test_utc_now_preserves_existing_naive_utc_database_semantics():
    value = time_utils.utc_now()

    assert value.tzinfo is None


def test_utc_now_represents_current_utc():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    value = time_utils.utc_now()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert before <= value <= after


def test_utc_now_does_not_call_deprecated_utcnow():
    source = inspect.getsource(time_utils.utc_now)

    assert "utcnow" not in source


def test_utc_now_iso_z_retains_iso_trailing_z_shape():
    value = time_utils.utc_now_iso_z()

    assert value.endswith("Z")
    parsed = datetime.fromisoformat(value.removesuffix("Z"))
    assert parsed.tzinfo is None
