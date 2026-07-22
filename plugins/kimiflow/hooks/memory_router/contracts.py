"""jq-identical JSON serialization. All stdout JSON in the CLI goes through dumps()."""
import json
import sys


def dumps(obj, pretty=False):
    if pretty:
        # jq default pretty: 2-space indent, ", "/": " separators, UTF-8 literals.
        return json.dumps(obj, indent=2, ensure_ascii=False)
    # jq -c compact: no spaces.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def json_print(obj, pretty=False, stream=None):
    # Bash json_print: `jq .` (pretty) or `jq -c .` (compact), each via `printf '%s\n'`
    # so the output carries a trailing newline.
    if stream is None:
        stream = sys.stdout
    stream.write(dumps(obj, pretty) + "\n")
