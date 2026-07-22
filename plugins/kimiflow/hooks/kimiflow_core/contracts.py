"""JSON output helpers matching the jq shapes used by the Bash helpers."""

import json
import sys


def dumps(obj, pretty=False):
    if pretty:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def json_print(obj, pretty=False, stream=None):
    if stream is None:
        stream = sys.stdout
    stream.write(dumps(obj, pretty) + "\n")
