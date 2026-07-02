"""Python port of hooks/project-map-status.sh."""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from .atomic import atomic_write


USAGE = """#!/usr/bin/env bash
# kimiflow — project-map staleness resolver. Orchestrator-invoked, not a hook.
#
# Usage:
#   project-map-status.sh [status] [--index <path>] [--affected <path>]...
#   project-map-status.sh coverage [--index <path>] [--affected <path>]...
#   project-map-status.sh refresh [--index <path>] --section <name>...
#   project-map-status.sh refresh [--index <path>] --changed
#   project-map-status.sh index-symbols [--index <path>] [--section <name>...]   (default: all sections)
#
# Output is TSV-ish and stable:
#   PROJECT_MAP <status> stale=<n> potentially_stale=<n> unknown=<n> affected_stale=<n> index=<path>
#   PROJECT_MAP_COVERAGE <status> affected=<n> mapped=<n> unmapped=<n> affected_stale=<n> affected_unknown=<n> phase2_depth=<compressed|targeted|full> reason=<reason> index=<path>
#   SECTION     <name>   <status> affected=<yes|no|all> reason=<reason> paths=<csv|->
#   REFRESHED   <name>   files=<n> commit=<sha|NOT VERIFIED>
#   NEW-FILE    <section> <path>
#   SYMBOLS     <section>
#
# R2 invariant examples:
#   project-map-status.sh
#   PMS coverage --affected
#   PMS refresh --changed
set -u
"""

FUNCTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)")


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    sys.stderr.write("project-map-status: %s\n" % message)
    return code


def need_jq():
    if not shutil.which("jq"):
        return die("jq is required", 2)
    return 0


def run_git(root, args):
    try:
        proc = subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc


def repo_root():
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return os.getcwd()
    root = proc.stdout.strip()
    if proc.returncode == 0 and root:
        return root
    return os.getcwd()


def git_commit_ok(root, commit):
    if not commit or commit == "NOT VERIFIED":
        return False
    proc = run_git(root, ["cat-file", "-e", "%s^{commit}" % commit])
    return bool(proc and proc.returncode == 0)


def git_short_head(root):
    proc = run_git(root, ["rev-parse", "--short", "HEAD"])
    if proc and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "NOT VERIFIED"


def sha256_file(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:%s" % hasher.hexdigest()


def sorted_unique(values):
    return sorted({value for value in values if value})


def append_unique(items, value):
    if value and value not in items:
        items.append(value)


def join_csv(items):
    clean = [item for item in items if item]
    return ",".join(clean) if clean else "-"


def path_matches_prefix(path, prefixes):
    return prefix_match_len(path, prefixes) >= 0


def prefix_match_len(path, prefixes):
    best = -1
    for prefix in prefixes:
        if not prefix:
            continue
        matched = path == prefix or path.startswith(prefix + "/")
        if not matched and prefix.endswith("/"):
            matched = path.startswith(prefix)
        if matched and len(prefix) > best:
            best = len(prefix)
    return best


def manifest_path(path):
    patterns = (
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "tsconfig*.json",
        "Cargo.toml",
        "Cargo.lock",
        "pyproject.toml",
        "requirements*.txt",
        "uv.lock",
        "go.mod",
        "go.sum",
        "Gemfile",
        "Gemfile.lock",
        "composer.json",
        "composer.lock",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        "Makefile",
        "Dockerfile",
        "docker-compose*.yml",
        "docker-compose*.yaml",
    )
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def flow_path(path):
    patterns = ("*route*", "*api*", "*schema*", "*migration*", "db/migrate/*")
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def section_is_stackish(section):
    return section in {"tech", "stack", "dependencies", "architecture", "testing", "quality", "conventions"}


def section_is_flowish(section):
    return section in {"flow", "flows", "routing", "api", "schema", "migrations"}


def collect_changed_paths(root, base):
    changed = []
    if git_commit_ok(root, base):
        proc = run_git(root, ["diff", "--name-status", base, "HEAD"])
        if proc and proc.stdout:
            for raw in proc.stdout.splitlines():
                if not raw:
                    continue
                parts = raw.split("\t")
                status = parts[0] if parts else ""
                if status.startswith(("R", "C")):
                    if len(parts) > 1 and parts[1]:
                        changed.append(parts[1])
                    if len(parts) > 2 and parts[2]:
                        changed.append(parts[2])
                elif len(parts) > 1 and parts[1]:
                    changed.append(parts[1])

    for args in (
        ["diff", "--name-only", "--cached"],
        ["diff", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        proc = run_git(root, args)
        if proc and proc.stdout:
            changed.extend(path for path in proc.stdout.splitlines() if path)
    return changed


def load_index(index_path):
    with open(index_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_index(index_path, data):
    # R1 write-safety: install into the INDEX directory, refuse symlink targets,
    # and let callers print success only after this atomic replace returns.
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write(index_path, payload, mode=0o600, refuse_symlink=True)


def sections(data):
    value = data.get("sections")
    if isinstance(value, dict):
        return value
    return {}


def section_value(data, section):
    value = sections(data).get(section)
    if isinstance(value, dict):
        return value
    return {}


def section_names_sorted(data):
    return sorted(sections(data).keys())


def section_names_unsorted(data):
    return list(sections(data).keys())


def section_members(data, section):
    sec = section_value(data, section)
    files = sec.get("files") if isinstance(sec.get("files"), list) else []
    hashes = sec.get("file_hashes") if isinstance(sec.get("file_hashes"), dict) else {}
    return sorted_unique(list(files) + list(hashes.keys()))


def section_hash_paths(data, section):
    hashes = section_value(data, section).get("file_hashes")
    if not isinstance(hashes, dict):
        return []
    return sorted_unique(hashes.keys())


def section_prefixes(data, section):
    prefixes = section_value(data, section).get("prefixes")
    if not isinstance(prefixes, list):
        return []
    return [prefix for prefix in prefixes if prefix]


def section_prefixes_effective(data, section):
    prefixes = section_prefixes(data, section)
    if prefixes:
        return prefixes
    effective = []
    for path in section_members(data, section):
        if "/" in path:
            effective.append(path.rsplit("/", 1)[0] + "/")
        else:
            effective.append(path)
    return effective


def section_file_hash(data, section, path):
    hashes = section_value(data, section).get("file_hashes")
    if isinstance(hashes, dict):
        return hashes.get(path, "")
    return ""


def section_status(root, data, section, affected_paths):
    files = section_members(data, section)
    hash_paths = section_hash_paths(data, section)
    prefixes = section_prefixes_effective(data, section)
    hit_paths = []
    base = section_value(data, section).get("last_scanned_commit") or data.get("baseline_commit") or "NOT VERIFIED"
    changed_paths = collect_changed_paths(root, base)

    status = "current"
    reason = "clean"
    if not files and not prefixes:
        status = "unknown"
        reason = "no-section-files"

    for path in files:
        full_path = os.path.join(root, path)
        if not os.path.exists(full_path):
            status = "stale"
            reason = "deleted-section-file"
            hit_paths.append(path)
            break
        expected = section_file_hash(data, section, path)
        if expected and sha256_file(full_path) != expected:
            status = "stale"
            reason = "hash-mismatch"
            hit_paths.append(path)
            break

    if status == "current":
        file_set = set(files)
        hash_set = set(hash_paths)
        for path in changed_paths:
            if files and path in file_set and (not hash_paths or path not in hash_set):
                if manifest_path(path) and section_is_stackish(section):
                    status = "potentially_stale"
                    reason = "manifest-or-build-config-changed"
                else:
                    status = "stale"
                    reason = "changed-section-file"
                hit_paths.append(path)
                break

    if status == "current":
        file_set = set(files)
        for path in changed_paths:
            if (not files or path not in file_set) and prefixes and path_matches_prefix(path, prefixes):
                status = "potentially_stale"
                reason = "new-or-unmapped-file-under-prefix"
                hit_paths.append(path)
                break

    if status == "current":
        for path in changed_paths:
            if manifest_path(path) and section_is_stackish(section):
                status = "potentially_stale"
                reason = "manifest-or-build-config-changed"
                hit_paths.append(path)
                break
            if flow_path(path) and section_is_flowish(section):
                status = "stale"
                reason = "route-api-schema-or-migration-changed"
                hit_paths.append(path)
                break

    if status == "current" and not git_commit_ok(root, base) and not hash_paths:
        status = "unknown"
        reason = "baseline-commit-not-verifiable"

    affected = "all"
    if affected_paths:
        affected = "no"
        file_set = set(files)
        for path in affected_paths:
            if (files and path in file_set) or (prefixes and path_matches_prefix(path, prefixes)):
                affected = "yes"
                break

    return "SECTION\t%s\t%s\taffected=%s\treason=%s\tpaths=%s" % (
        section,
        status,
        affected,
        reason,
        join_csv(hit_paths),
    )


def build_map_scope(data):
    map_files = []
    map_prefixes = []
    for sec in sections(data).values():
        if not isinstance(sec, dict):
            continue
        files = sec.get("files") if isinstance(sec.get("files"), list) else []
        hashes = sec.get("file_hashes") if isinstance(sec.get("file_hashes"), dict) else {}
        map_files.extend(files)
        map_files.extend(hashes.keys())
        prefixes = sec.get("prefixes") if isinstance(sec.get("prefixes"), list) else []
        map_prefixes.extend(prefixes)
    map_files = sorted_unique(map_files)
    map_prefixes = sorted_unique(map_prefixes)
    for path in map_files:
        if "/" in path:
            map_prefixes.append(path.rsplit("/", 1)[0] + "/")
        else:
            map_prefixes.append(path)
    return map_files, map_prefixes


def path_is_mapped(path, map_files, map_prefixes):
    return path in set(map_files) or path_matches_prefix(path, map_prefixes)


def index_symbols_for_section(root, data, section):
    symbols = {}
    for rel in section_members(data, section):
        if not rel.endswith(".sh"):
            continue
        full_path = os.path.join(root, rel)
        if not os.path.isfile(full_path):
            continue
        with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
            for lineno, line in enumerate(handle, 1):
                match = FUNCTION_RE.match(line.rstrip("\n"))
                if match:
                    symbols[match.group(1)] = "%s:%s" % (rel, lineno)
    section_value(data, section)["symbols"] = symbols


def output_missing(mode, affected_paths, index_path):
    if mode == "coverage":
        sys.stdout.write(
            "PROJECT_MAP_COVERAGE\tmissing\taffected=%s\tmapped=0\tunmapped=%s\taffected_stale=0\taffected_unknown=0\tphase2_depth=full\treason=missing-index\tindex=%s\n"
            % (len(affected_paths), len(affected_paths), index_path)
        )
    else:
        sys.stdout.write(
            "PROJECT_MAP\tmissing\tstale=0\tpotentially_stale=0\tunknown=0\taffected_stale=0\tindex=%s\n"
            % index_path
        )


def output_invalid(mode, affected_paths, index_path):
    if mode == "coverage":
        sys.stdout.write(
            "PROJECT_MAP_COVERAGE\tunknown\taffected=%s\tmapped=0\tunmapped=%s\taffected_stale=0\taffected_unknown=0\tphase2_depth=full\treason=invalid-index\tindex=%s\n"
            % (len(affected_paths), len(affected_paths), index_path)
        )
    else:
        sys.stdout.write(
            "PROJECT_MAP\tunknown\tstale=0\tpotentially_stale=0\tunknown=1\taffected_stale=0\tindex=%s\n"
            % index_path
        )


def cmd_index_symbols(root, index_path, data, refresh_sections):
    names = refresh_sections or section_names_sorted(data)
    for section in names:
        if section not in sections(data):
            return die("unknown section: %s" % section)
        try:
            index_symbols_for_section(root, data, section)
            dump_index(index_path, data)
        except (OSError, ValueError):
            return die("cannot install %s" % index_path)
        sys.stdout.write("SYMBOLS\t%s\n" % section)
    return 0


def cmd_refresh(root, index_path, data, refresh_sections):
    names = refresh_sections or section_names_sorted(data)
    commit = git_short_head(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for section in names:
        if section not in sections(data):
            return die("unknown section: %s" % section)
        hashes = {}
        count = 0
        for rel in section_members(data, section):
            full_path = os.path.join(root, rel)
            if os.path.isfile(full_path):
                hashes[rel] = sha256_file(full_path)
                count += 1
        sec = section_value(data, section)
        sec["file_hashes"] = hashes
        sec["last_scanned_commit"] = commit
        sec["status"] = "current"
        sec["updated_at"] = now
        try:
            dump_index(index_path, data)
        except (OSError, ValueError):
            return die("cannot install %s" % index_path)
        sys.stdout.write("REFRESHED\t%s\tfiles=%s\tcommit=%s\n" % (section, count, commit))
    return 0


def cmd_refresh_changed(root, index_path, data):
    base = data.get("baseline_commit") or "NOT VERIFIED"
    changed_paths = collect_changed_paths(root, base)
    commit = git_short_head(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_sections = section_names_unsorted(data)
    all_members = sorted_unique(
        member for section in all_sections for member in section_members(data, section)
    )
    all_member_set = set(all_members)

    member_pairs = []
    prefix_pairs = []
    for section in all_sections:
        for rel in section_members(data, section):
            member_pairs.append((section, rel))
        for prefix in section_prefixes_effective(data, section):
            prefix_pairs.append((section, prefix))

    unique_changed = []
    for rel in changed_paths:
        append_unique(unique_changed, rel)

    affected_sections = []
    newfile_sections = []
    newfile_paths = []
    for rel in unique_changed:
        if all_members and rel in all_member_set:
            for section, member in member_pairs:
                if member == rel:
                    append_unique(affected_sections, section)
        else:
            if not os.path.exists(os.path.join(root, rel)):
                continue
            best_section = ""
            best_len = -1
            for section, prefix in prefix_pairs:
                match_len = prefix_match_len(rel, [prefix])
                if match_len > best_len:
                    best_len = match_len
                    best_section = section
            if best_section and best_len >= 0:
                newfile_sections.append(best_section)
                newfile_paths.append(rel)
                append_unique(affected_sections, best_section)

    if not affected_sections:
        return 0

    pending_output = []
    for section, rel in zip(newfile_sections, newfile_paths):
        pending_output.append("NEW-FILE\t%s\t%s\n" % (section, rel))

    refresh_counts = []
    for section in affected_sections:
        final_files = []
        for rel in section_members(data, section):
            if os.path.exists(os.path.join(root, rel)):
                append_unique(final_files, rel)
        for new_section, rel in zip(newfile_sections, newfile_paths):
            if new_section == section:
                append_unique(final_files, rel)

        hashes = {}
        count = 0
        for rel in final_files:
            full_path = os.path.join(root, rel)
            if os.path.isfile(full_path):
                hashes[rel] = sha256_file(full_path)
                count += 1

        sec = section_value(data, section)
        sec["files"] = final_files
        sec["file_hashes"] = hashes
        sec["last_scanned_commit"] = commit
        sec["status"] = "current"
        sec["updated_at"] = now
        index_symbols_for_section(root, data, section)
        refresh_counts.append((section, count))

    if commit != "NOT VERIFIED":
        data["baseline_commit"] = commit

    try:
        dump_index(index_path, data)
    except (OSError, ValueError):
        return die("cannot install %s" % index_path)

    for section, count in refresh_counts:
        pending_output.append("REFRESHED\t%s\tfiles=%s\tcommit=%s\n" % (section, count, commit))
    for line in pending_output:
        sys.stdout.write(line)
    return 0


def normalize_affected(root, affected_paths):
    normalized = []
    prefix = root + os.sep
    for path in affected_paths:
        if path.startswith(prefix):
            normalized.append(path[len(prefix) :])
        elif path.startswith("./"):
            normalized.append(path[2:])
        else:
            normalized.append(path)
    return normalized


def parse_args(argv):
    mode = "status"
    index_path = ""
    affected_paths = []
    refresh_sections = []
    refresh_changed = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("status", "coverage", "refresh", "index-symbols"):
            mode = arg
            i += 1
        elif arg == "--index":
            index_path = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif arg == "--affected":
            affected_paths.append(argv[i + 1] if i + 1 < len(argv) else "")
            i += 2
        elif arg == "--section":
            refresh_sections.append(argv[i + 1] if i + 1 < len(argv) else "")
            i += 2
        elif arg == "--changed":
            refresh_changed = True
            i += 1
        elif arg in ("--help", "-h"):
            usage()
            raise SystemExit(0)
        else:
            if mode in ("refresh", "index-symbols"):
                refresh_sections.append(arg)
            else:
                affected_paths.append(arg)
            i += 1
    return mode, index_path, affected_paths, refresh_sections, refresh_changed


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        mode, index_path, affected_paths, refresh_sections, refresh_changed = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    jq_rc = need_jq()
    if jq_rc:
        return jq_rc

    root = repo_root()
    if not index_path:
        index_path = os.path.join(root, ".kimiflow/project/INDEX.json")
    affected_paths = normalize_affected(root, affected_paths)

    if not os.path.isfile(index_path):
        output_missing(mode, affected_paths, index_path)
        return 0

    try:
        data = load_index(index_path)
    except (OSError, json.JSONDecodeError):
        output_invalid(mode, affected_paths, index_path)
        return 0

    if mode == "index-symbols":
        return cmd_index_symbols(root, index_path, data, refresh_sections)

    if mode == "refresh":
        if refresh_changed:
            return cmd_refresh_changed(root, index_path, data)
        return cmd_refresh(root, index_path, data, refresh_sections)

    names = section_names_sorted(data)
    if not names:
        sys.stdout.write(
            "PROJECT_MAP\tunknown\tstale=0\tpotentially_stale=0\tunknown=1\taffected_stale=0\tindex=%s\n"
            % index_path
        )
        return 0

    lines = []
    stale = 0
    potential = 0
    unknown = 0
    affected_stale = 0
    affected_unknown = 0
    for section in names:
        line = section_status(root, data, section, affected_paths)
        lines.append(line)
        if "\tstale\t" in line:
            stale += 1
        elif "\tpotentially_stale\t" in line:
            potential += 1
        elif "\tunknown\t" in line:
            unknown += 1
        if ("\tstale\t" in line or "\tpotentially_stale\t" in line) and "affected=yes" in line:
            affected_stale += 1
        if "\tunknown\t" in line and "affected=yes" in line:
            affected_unknown += 1

    overall = "current"
    if stale > 0:
        overall = "stale" if stale == len(names) else "partially_stale"
    elif potential > 0:
        overall = "partially_stale"
    elif unknown > 0:
        overall = "unknown"

    if mode == "coverage":
        map_files, map_prefixes = build_map_scope(data)
        affected = len(affected_paths)
        mapped = 0
        unmapped = 0
        for path in affected_paths:
            if path_is_mapped(path, map_files, map_prefixes):
                mapped += 1
            else:
                unmapped += 1
        coverage_status = "covered"
        phase2_depth = "compressed"
        reason = "affected-paths-covered-current"
        if affected == 0:
            coverage_status = "unscoped"
            phase2_depth = "targeted"
            reason = "no-affected-paths"
        elif unmapped > 0:
            coverage_status = "partial"
            phase2_depth = "full"
            reason = "unmapped-affected-paths"
        elif affected_stale > 0:
            coverage_status = "stale"
            phase2_depth = "targeted"
            reason = "mapped-but-stale"
        elif affected_unknown > 0:
            coverage_status = "unknown"
            phase2_depth = "targeted"
            reason = "mapped-but-unknown"
        elif overall != "current":
            coverage_status = "covered"
            phase2_depth = "compressed"
            reason = "affected-paths-covered-unrelated-map-staleness"
        sys.stdout.write(
            "PROJECT_MAP_COVERAGE\t%s\taffected=%s\tmapped=%s\tunmapped=%s\taffected_stale=%s\taffected_unknown=%s\tphase2_depth=%s\treason=%s\tindex=%s\n"
            % (
                coverage_status,
                affected,
                mapped,
                unmapped,
                affected_stale,
                affected_unknown,
                phase2_depth,
                reason,
                index_path,
            )
        )
        return 0

    sys.stdout.write(
        "PROJECT_MAP\t%s\tstale=%s\tpotentially_stale=%s\tunknown=%s\taffected_stale=%s\tindex=%s\n"
        % (overall, stale, potential, unknown, affected_stale, index_path)
    )
    for line in lines:
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
