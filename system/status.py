from datetime import datetime, timezone

# Recorded once at process startup
START_TIME: datetime = datetime.now(tz=timezone.utc)


def uptime() -> str:
    """Return human-readable uptime string, e.g. '3h 14m 52s'."""
    delta = datetime.now(tz=timezone.utc) - START_TIME
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def uptime_seconds() -> int:
    """Return uptime in raw seconds."""
    return int((datetime.now(tz=timezone.utc) - START_TIME).total_seconds())