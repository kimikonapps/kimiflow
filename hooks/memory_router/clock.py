"""UTC clock helpers (ports of Bash iso_now / date_now). Nondeterministic by nature."""
from datetime import datetime, timezone


def iso_now():
    # Bash: date -u +"%Y-%m-%dT%H:%M:%SZ"
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_now():
    # Bash: date -u +"%Y-%m-%d"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
