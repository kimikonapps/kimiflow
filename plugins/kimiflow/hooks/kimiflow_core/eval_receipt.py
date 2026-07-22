"""Validate local behavioral-eval receipts without invoking a model or network."""

import json
import os
import re
import stat
import subprocess
import sys


MAX_RECEIPT_BYTES = 256 * 1024
TOP_LEVEL_KEYS = {
    "schema_version",
    "scenario",
    "mode",
    "source_commit",
    "attribution_clean",
    "sample_count",
    "pass_count",
    "failure_count",
    "verdict",
    "runs",
}
RUN_KEYS = {"id", "passed", "rule_refs"}
SCENARIO_RE = re.compile(r"^[0-9]{2}-[a-z0-9][a-z0-9-]{1,79}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RULE_REF_RE = re.compile(
    r"^(?P<file>SKILL\.md|reference\.md)(?::(?P<line>[1-9][0-9]*)| §(?P<section>[^\r\n]{1,120}))$"
)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
OPEN_ENDED_SCENARIOS = frozenset(
    {"01-commit-gate", "03-plan-gate-cap", "08-advisory-triage-failclosed", "09-headless-build-gate"}
)


class ReceiptError(Exception):
    pass


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _git_environment():
    environment = os.environ.copy()
    environment["GIT_ALLOW_PROTOCOL"] = "file"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def _git_blob_lines(commit, relative_path):
    try:
        tree = subprocess.run(
            ["git", "ls-tree", "-z", commit, "--", relative_path],
            cwd=_repo_root(),
            check=True,
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
        entries = [entry for entry in tree.split(b"\0") if entry]
        if len(entries) != 1:
            return None
        metadata, separator, path = entries[0].partition(b"\t")
        fields = metadata.split()
        if separator != b"\t" or path != os.fsencode(relative_path) or len(fields) != 3:
            return None
        mode, kind, object_id = fields
        if mode not in (b"100644", b"100755") or kind != b"blob":
            return None
        payload = subprocess.run(
            ["git", "cat-file", "blob", os.fsdecode(object_id)],
            cwd=_repo_root(),
            check=True,
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
        return payload.decode("utf-8").splitlines()
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None


def _commit_exists(commit):
    try:
        subprocess.run(
            ["git", "cat-file", "-e", commit + "^{commit}"],
            cwd=_repo_root(),
            check=True,
            env=_git_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _substantive_rule_line(lines, index):
    if index < 0 or index >= len(lines):
        return False
    frontmatter_end = -1
    if lines and lines[0].strip() == "---":
        frontmatter_end = len(lines) - 1
        for candidate in range(1, len(lines)):
            if lines[candidate].strip() == "---":
                frontmatter_end = candidate
                break
    if index <= frontmatter_end:
        return False
    fence = None
    for position, line in enumerate(lines[: index + 1]):
        match = FENCE_RE.fullmatch(line)
        if fence is None:
            if match:
                marker = match.group("marker")
                if position == index:
                    return False
                fence = (marker[0], len(marker))
        else:
            closes = (
                match is not None
                and match.group("marker")[0] == fence[0]
                and len(match.group("marker")) >= fence[1]
                and not match.group("rest").strip()
            )
            if position == index:
                return False
            if closes:
                fence = None
    value = lines[index].strip()
    if not value or value == "---" or value.startswith(("```", "~~~", "<!--")):
        return False
    return HEADING_RE.fullmatch(value) is not None or any(character.isalpha() for character in value)


def _rule_ref_exists(ref, commit, blob_cache):
    match = RULE_REF_RE.fullmatch(ref)
    if match is None:
        return False
    source = match.group("file")
    if source not in blob_cache:
        blob_cache[source] = _git_blob_lines(commit, source)
    lines = blob_cache[source]
    if lines is None:
        return False
    if match.group("line"):
        return _substantive_rule_line(lines, int(match.group("line")) - 1)
    section = match.group("section").strip().casefold()
    headings = {
        heading.group(1).strip().casefold()
        for line in lines
        for heading in [HEADING_RE.fullmatch(line.rstrip("\r\n"))]
        if heading
    }
    return bool(section) and section in headings


def _integer(value, field, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReceiptError("%s must be an integer >= %s" % (field, minimum))
    return value


def validate(value):
    if not isinstance(value, dict) or set(value) != TOP_LEVEL_KEYS:
        raise ReceiptError("receipt fields do not match schema 1")
    if value["schema_version"] != 1:
        raise ReceiptError("unsupported schema_version")
    if not isinstance(value["scenario"], str) or SCENARIO_RE.fullmatch(value["scenario"]) is None:
        raise ReceiptError("scenario is invalid")
    if value["mode"] not in ("multiple_choice", "open_ended"):
        raise ReceiptError("mode is invalid")
    if not isinstance(value["source_commit"], str) or COMMIT_RE.fullmatch(value["source_commit"]) is None:
        raise ReceiptError("source_commit must be a full lowercase Git SHA")
    if not _commit_exists(value["source_commit"]):
        raise ReceiptError("source_commit does not exist")
    scenario_path = "evals/scenarios/" + value["scenario"] + ".md"
    if _git_blob_lines(value["source_commit"], scenario_path) is None:
        raise ReceiptError("scenario does not exist at source_commit")
    if value["mode"] == "open_ended" and value["scenario"] not in OPEN_ENDED_SCENARIOS:
        raise ReceiptError("scenario is not eligible for open_ended mode")
    if value["attribution_clean"] is not True:
        raise ReceiptError("attribution_clean must be true")

    sample_count = _integer(value["sample_count"], "sample_count", 3)
    pass_count = _integer(value["pass_count"], "pass_count")
    failure_count = _integer(value["failure_count"], "failure_count")
    if not isinstance(value["runs"], list) or len(value["runs"]) != sample_count:
        raise ReceiptError("runs length must equal sample_count")
    if pass_count + failure_count != sample_count:
        raise ReceiptError("pass_count + failure_count must equal sample_count")

    seen = set()
    derived_passes = 0
    rule_blobs = {}
    rule_validity = {}
    for index, run in enumerate(value["runs"]):
        if not isinstance(run, dict) or set(run) != RUN_KEYS:
            raise ReceiptError("run %s fields do not match schema 1" % index)
        run_id = run["id"]
        if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None or run_id in seen:
            raise ReceiptError("run id is invalid or duplicated")
        seen.add(run_id)
        if not isinstance(run["passed"], bool):
            raise ReceiptError("run passed must be boolean")
        refs = run["rule_refs"]
        if not isinstance(refs, list):
            raise ReceiptError("run rule_refs are invalid")
        for ref in refs:
            if not isinstance(ref, str):
                raise ReceiptError("run rule_refs are invalid")
            if ref not in rule_validity:
                rule_validity[ref] = _rule_ref_exists(ref, value["source_commit"], rule_blobs)
            if not rule_validity[ref]:
                raise ReceiptError("run rule_refs are invalid")
        if run["passed"] and not refs:
            raise ReceiptError("passing run needs a Kimiflow rule reference")
        derived_passes += int(run["passed"])

    if derived_passes != pass_count:
        raise ReceiptError("pass_count does not match runs")
    expected_verdict = "PASS" if pass_count >= (sample_count // 2 + 1) else "CRACK"
    if value["verdict"] != expected_verdict:
        raise ReceiptError("verdict does not match strict majority")
    return {
        "schema_version": 1,
        "scenario": value["scenario"],
        "mode": value["mode"],
        "sample_count": sample_count,
        "pass_count": pass_count,
        "failure_count": failure_count,
        "verdict": expected_verdict,
        "attribution_clean": True,
    }


def load(path):
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ReceiptError("cannot stat receipt: %s" % exc) from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ReceiptError("receipt must be a regular non-symlink file")
    if before.st_size > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt exceeds size limit")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ReceiptError("receipt changed during validation")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            payload = handle.read(MAX_RECEIPT_BYTES + 1)
        if len(payload) > MAX_RECEIPT_BYTES:
            raise ReceiptError("receipt exceeds size limit")
        text = payload.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicates)
    except ReceiptError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReceiptError("receipt JSON is invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "validate":
        sys.stderr.write("usage: behavior-eval-receipt.sh validate <receipt.json>\n")
        return 2
    try:
        result = validate(load(argv[1]))
    except ReceiptError as exc:
        sys.stderr.write("behavior-eval-receipt: %s\n" % exc)
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
