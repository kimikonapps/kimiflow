"""Text helpers (verbatim ports of Bash slugify / sql_quote / word_count_file)."""
import re

# Bash: tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '-' | sed strip/collapse | cut -c1-40.
# In the C-ish locale [:alnum:] is ASCII, so match with [^a-z0-9] after str.lower().
_NONALNUM = re.compile(r"[^a-z0-9]+")


def slugify(s):
    s = _NONALNUM.sub("-", s.lower())   # runs of non-alnum -> single '-'
    s = s.strip("-")                    # sed 's/^-//; s/-$//'
    return s[:40]                       # cut -c1-40


def sql_quote(s):
    return s.replace("'", "''")


def word_count_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return len(handle.read().split())
    except OSError:
        return 0
