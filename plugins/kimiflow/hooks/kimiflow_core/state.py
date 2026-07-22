"""STATE.md parsing helpers."""

import re


_LIST_PREFIX = re.compile(r"^\s*-\s*")


def normalize_state_line(line):
    line = line.replace("\r", "")
    line = line.replace("**", "")
    return _LIST_PREFIX.sub("", line)


def state_value_text(text, key):
    wanted = key.strip().lower()
    if not wanted:
        return ""
    for raw in text.splitlines():
        line = normalize_state_line(raw)
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        if label.strip().lower() == wanted:
            return value.strip()
    return ""


def state_value(path, key):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return state_value_text(handle.read(), key)
    except OSError:
        return ""
