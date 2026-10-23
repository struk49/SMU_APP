"""Fail-closed configuration for local entrypoints; never imports the app."""

import os
from pathlib import Path

from dotenv import find_dotenv
from dotenv.main import DotEnv
from dotenv.parser import parse_stream
from sqlalchemy.engine import make_url


class LocalStartupError(ValueError):
    """A local process cannot safely start with the supplied configuration."""


FALSE_VALUES = {"false", "0", "no", "off"}
SAFETY_KEYS = ("DATABASE_URL", "SMU_SCHEDULER_ENABLED")


def prepare_local_environment():
    """Match app dotenv precedence, reject ambiguity, then pin validated values.

    Existing environment variables win (python-dotenv override=False). A safety
    setting shadowed by a conflicting .env value is rejected, not ignored.
    No implicit database/scheduler defaults are safe for these local processes.
    """
    try:
        dotenv_path = find_dotenv()
        values = {}
        if dotenv_path:
            if os.environ.get("PYTHON_DOTENV_DISABLED", "").lower() in {
                "1", "true", "t", "yes", "y",
            }:
                raise LocalStartupError("Unsupported local dotenv configuration.")
            seen = set()
            with Path(dotenv_path).open(encoding="utf-8") as stream:
                for binding in parse_stream(stream):
                    if binding.error:
                        raise LocalStartupError("Malformed local .env configuration.")
                    key = binding.key
                    if key and key.upper() in SAFETY_KEYS:
                        if key not in SAFETY_KEYS:
                            raise LocalStartupError("Unsupported case variant of a local safety setting.")
                        if key in seen:
                            raise LocalStartupError("Duplicate local safety setting.")
                        seen.add(key)
            values = DotEnv(dotenv_path, override=False).dict()
        effective = {}
        for key in SAFETY_KEYS:
            inherited = os.environ.get(key)
            configured = values.get(key)
            if inherited is not None and key in values:
                left = inherited.strip()
                right = configured.strip() if isinstance(configured, str) else ""
                equivalent = left == right
                if key == "SMU_SCHEDULER_ENABLED":
                    equivalent = left.lower() in FALSE_VALUES and right.lower() in FALSE_VALUES
                if not equivalent:
                    raise LocalStartupError("Conflicting local safety settings in environment and .env.")
            value = inherited if inherited is not None else configured
            if not isinstance(value, str) or not value.strip():
                raise LocalStartupError("Set DATABASE_URL and SMU_SCHEDULER_ENABLED explicitly for local startup.")
            effective[key] = value.strip()
        if effective["SMU_SCHEDULER_ENABLED"].lower() not in FALSE_VALUES:
            raise LocalStartupError("Local startup requires SMU_SCHEDULER_ENABLED explicitly false.")
        url = make_url(effective["DATABASE_URL"])
        if (
            url.drivername != "sqlite" or url.host or url.username or url.password
            or url.port or url.query or not url.database or url.database == ":memory:"
        ):
            raise LocalStartupError("Local startup requires an explicit file-backed SQLite database.")
    except LocalStartupError:
        raise
    except Exception:
        # Parsers may include connection strings in their exceptions.
        raise LocalStartupError("Invalid local startup configuration.") from None

    # Pin the effective safety values before app.load_dotenv() can run again.
    os.environ.update(effective)
    return effective
