"""UTC clock helpers (ports of Bash iso_now / date_now). Nondeterministic by nature."""
from datetime import datetime, timedelta, timezone


def iso_now():
    # Bash: date -u +"%Y-%m-%dT%H:%M:%SZ"
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_now():
    # Bash: date -u +"%Y-%m-%d"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def date_compact():
    # Bash: date -u +%Y%m%d  (compact YYYYMMDD; used only for the learning-row id)
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def date_days_ago(days):
    # Bash date_days_ago: `date -u -v-${days}d` (BSD) or `date -u -d "$days days ago"`
    # (GNU), both = today_utc - days; Bash prints '' if neither works (non-numeric days).
    try:
        return (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
