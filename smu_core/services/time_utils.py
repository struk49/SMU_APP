from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_iso_z():
    return utc_now().isoformat() + "Z"
