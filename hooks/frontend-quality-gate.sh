#!/usr/bin/env bash
# kimiflow — token-efficient frontend routing and evidence gate.
# Orchestrator-invoked; Python 3 is already a Kimiflow runtime prerequisite.
set -u

if ! command -v python3 >/dev/null 2>&1; then
  printf 'FRONTEND_QUALITY_GATE\tCLOSED\tblockers=1\treason=python3_missing\tdetail=python3_missing\n'
  exit 0
fi

exec python3 - "$@" <<'PY'
from __future__ import print_function

import binascii
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import zlib

SELECTORS = (
    "Frontend quality contract",
    "Frontend quality",
    "Frontend quality routing",
    "Frontend quality evidence",
    "Frontend quality basis",
    "Frontend quality start",
    "Frontend quality recovery",
    "Frontend quality recovery owns global",
)
QA_KEYS = (
    "Lane", "Source truth", "Implementation evidence", "Viewport", "State",
    "Strategy", "Deterministic checks", "Comparison history", "Open P0",
    "Open P1", "Open P2", "Open P3", "Final result",
)
RECOVERY_KEYS = (
    "Version", "Status", "Attempt", "Kind", "Lane", "Source truth hash",
    "Viewport", "State hash", "Strategy hash", "Pixel hash",
)
MAX_PNG = 25 * 1024 * 1024
MAX_RAW = 128 * 1024 * 1024
MAX_DIM = 16384
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def emit(status, reason, details):
    clean = []
    for item in details:
        if item and item not in clean:
            clean.append(item)
    blockers = 0 if status == "OPEN" else max(1, len(clean))
    detail = ",".join(clean)
    print("FRONTEND_QUALITY_GATE\t%s\tblockers=%d\treason=%s\tdetail=%s" %
          (status, blockers, reason, detail))
    raise SystemExit(0)


def run(cmd, cwd=None):
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("command_failed:%s:%s" % (cmd[0], err.decode("utf-8", "replace").strip()))
    return out


def atomic_text(path, text):
    parent = os.path.dirname(path)
    if os.path.islink(path):
        raise ValueError("symlink_refused")
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, obj):
    atomic_text(path, json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def normalized_state_line(line):
    line = line.replace("\r", "").strip()
    if line.startswith("- "):
        line = line[2:].lstrip()
    line = line.replace("**", "")
    return line.strip()


def read_lf_lines(path):
    text = open(path, encoding="utf-8").read()
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def affected_block(raw_lines):
    headers = [i for i, line in enumerate(raw_lines)
               if not line.replace("\r", "").lstrip().startswith("- ")
               and normalized_state_line(line) == "Affected files:"]
    affected = []
    rows = set()
    if len(headers) == 1:
        i = headers[0] + 1
        while i < len(raw_lines):
            bullet = raw_lines[i].replace("\r", "").lstrip()
            if not bullet.startswith("- "):
                break
            rows.add(i)
            item = bullet[2:]
            if item:
                affected.append(item)
            i += 1
    return headers, affected, rows


def parse_state(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None, {}, [], ["state_missing"]
    try:
        raw_lines = read_lf_lines(path)
    except (OSError, UnicodeError):
        return None, {}, [], ["state_unreadable"]
    headers, affected, affected_rows = affected_block(raw_lines)
    values = {}
    for i, line in enumerate(raw_lines):
        if i in affected_rows:
            continue
        line = normalized_state_line(line)
        match = re.match(r"^([^:]+):[ \t]*(.*)$", line)
        if match:
            values.setdefault(match.group(1).strip(), []).append(match.group(2).strip())
    errors = []
    if len(headers) != 1:
        errors.append("affected_header_%s" % ("missing" if not headers else "duplicate"))
    if len(affected) != len(set(affected)):
        errors.append("affected_files_duplicate")
    return raw_lines, values, affected, errors


def state_one(values, key, errors, code=None):
    rows = values.get(key, [])
    short = key.lower().replace("frontend quality ", "").replace(" ", "_")
    if not rows:
        errors.append(code or "%s_missing" % short)
        return ""
    if len(rows) != 1:
        errors.append(code or "duplicate_%s" % short)
        return rows[0] if rows else ""
    if not rows[0]:
        errors.append(code or "%s_empty" % short)
    return rows[0]


def replace_state(path, updates):
    lines = read_lf_lines(path)
    _, _, affected_rows = affected_block(lines)
    indices = {}
    for i, line in enumerate(lines):
        if i in affected_rows:
            continue
        normalized = normalized_state_line(line)
        match = re.match(r"^([^:]+):", normalized)
        if match and match.group(1) in updates:
            indices.setdefault(match.group(1), []).append(i)
    for key in updates:
        if len(indices.get(key, [])) != 1:
            raise ValueError("state_update_%s_not_exact" % key)
    for key, value in updates.items():
        lines[indices[key][0]] = "%s: %s" % (key, value)
    atomic_text(path, "\n".join(lines) + "\n")


class DuplicateKey(ValueError):
    pass


def reject_duplicate_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise DuplicateKey(key)
        obj[key] = value
    return obj


def load_active(path):
    if os.path.islink(path):
        return None, "active_run_symlink"
    if not os.path.lexists(path):
        return None, "active_run_missing"
    if not os.path.isfile(path):
        return None, "active_run_type_invalid"
    try:
        with open(path, encoding="utf-8") as handle:
            active = json.load(handle, object_pairs_hook=reject_duplicate_pairs)
        if not isinstance(active, dict):
            return None, "active_run_invalid"
        return active, None
    except DuplicateKey:
        return None, "active_run_duplicate_key"
    except (OSError, UnicodeError, ValueError):
        return None, "active_run_invalid"


def active_matches(active, root, run_dir):
    if not isinstance(active, dict) or active.get("status") != "active":
        return False
    declared = active.get("run")
    if not isinstance(declared, str) or not declared:
        return False
    candidate = declared if os.path.isabs(declared) else os.path.join(root, declared)
    return os.path.realpath(candidate) == os.path.realpath(run_dir)


def decode_path(raw):
    return raw.decode("utf-8", "surrogateescape")


def add_name_status(target, data):
    parts = data.split(b"\0")
    i = 0
    while i < len(parts) and parts[i]:
        status_text = decode_path(parts[i])
        i += 1
        if i >= len(parts) or not parts[i]:
            raise ValueError("git_name_status_malformed")
        target.add(decode_path(parts[i]))
        i += 1
        if status_text[:1] in ("R", "C"):
            if i >= len(parts) or not parts[i]:
                raise ValueError("git_rename_malformed")
            target.add(decode_path(parts[i]))
            i += 1


def excluded_path(path):
    path = path[2:] if path.startswith("./") else path
    return path == ".kimiflow" or path.startswith(".kimiflow/")


def git_delta(root, started_head):
    paths = set()
    commands = (
        ["git", "diff", "--name-status", "-z", "--find-renames", "%s..HEAD" % started_head],
        ["git", "diff", "--cached", "--name-status", "-z", "--find-renames"],
        ["git", "diff", "--name-status", "-z", "--find-renames"],
    )
    for command in commands:
        out = run(command, cwd=root)
        add_name_status(paths, out)
    out = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
    for raw in out.split(b"\0"):
        if raw:
            paths.add(decode_path(raw))
    result = set()
    for path in paths:
        path = path[2:] if path.startswith("./") else path
        if "\r" in path or "\n" in path:
            raise ValueError("git_path_not_representable")
        if not excluded_path(path):
            result.add(path)
    return result


def request_path(run_dir, mode):
    names = {"feature": "INTENT.md", "fix": "PROBLEM.md", "audit": "AUDIT-INTENT.md"}
    name = names.get(mode)
    return (os.path.join(run_dir, name), name) if name else (None, None)


def meaningful(value):
    return bool(value.strip()) and value.strip().lower() not in ("pending", "none", "n/a")


def evidence_value(value, mode, lane, errors):
    match = re.match(r"^ui-surface=(yes|no|excluded-by-mode); ref=(.+)$", value)
    if not match or not meaningful(match.group(2)):
        errors.append("evidence_invalid")
        return None
    ui, ref = match.group(1), match.group(2).strip()
    valid = ((mode == "feature" and lane == "off" and ui == "no") or
             (mode == "feature" and lane in ("standard", "flagship") and ui == "yes") or
             (mode in ("fix", "audit") and lane == "off" and ui == "excluded-by-mode"))
    if not valid:
        errors.append("evidence_mode_mismatch")
    return (ui, ref)


def viewport_dims(value):
    match = re.match(r"^([1-9][0-9]*)x([1-9][0-9]*)$", value or "")
    if not match:
        return None
    limit_digits = len(str(MAX_DIM))
    if len(match.group(1)) > limit_digits or len(match.group(2)) > limit_digits:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width > MAX_DIM or height > MAX_DIM:
        return None
    return width, height


def has_ui_path(paths):
    segments = set("ui frontend component components page pages view views screen screens style styles".split())
    extensions = (".html", ".css", ".scss", ".sass", ".less", ".jsx", ".tsx", ".vue", ".svelte")
    for path in paths:
        lower = path.lower().replace("\\", "/")
        if any(part in segments for part in lower.split("/")) or lower.endswith(extensions):
            return True
    return False


def flagship_intent(text):
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return any(re.search(r"(?:^| )%s(?: |$)" % re.escape(term), normalized)
               for term in ("polish", "redesign", "ui ux", "visual uplift", "visual refresh"))


def basis_hash(marker, start_value, mode, request_name, request_bytes, paths, lane, evidence):
    payload = {
        "contract": marker,
        "start": start_value,
        "mode": mode,
        "request": request_name,
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "paths": sorted(paths),
        "lane": lane,
        "evidence": evidence,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def routing_receipt(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None, "routing_receipt_missing"
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeError):
        return None, "routing_receipt_invalid"
    if len(lines) != 1 or not re.match(r"^Basis: [0-9a-f]{64}$", lines[0]):
        return None, "routing_receipt_invalid"
    return lines[0][7:], None


def exact_fields(path, keys):
    if not os.path.isfile(path) or os.path.islink(path):
        return None, ["artifact_missing"]
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeError):
        return None, ["artifact_unreadable"]
    values = {}
    errors = []
    for line in lines:
        if not line:
            errors.append("artifact_blank_line")
            continue
        match = re.match(r"^([^:]+):[ \t]*(.*)$", line)
        if not match or match.group(1) not in keys:
            errors.append("artifact_field_not_allowed")
            continue
        values.setdefault(match.group(1), []).append(match.group(2).strip())
    result = {}
    for key in keys:
        rows = values.get(key, [])
        if len(rows) != 1:
            errors.append(("duplicate_" if len(rows) > 1 else "missing_") + key.lower().replace(" ", "_"))
        else:
            result[key] = rows[0]
    return result, errors


def recovery_receipt(path):
    if not os.path.lexists(path):
        return None, None
    if os.path.islink(path) or not os.path.isfile(path):
        return None, "recovery_receipt_invalid"
    values, errors = exact_fields(path, RECOVERY_KEYS)
    if errors:
        return None, "recovery_receipt_invalid"
    if values["Version"] != "1" or values["Status"] not in ("closed", "resolved"):
        return None, "recovery_receipt_invalid"
    if not re.match(r"^[1-9][0-9]*$", values["Attempt"]):
        return None, "recovery_receipt_invalid"
    if values["Kind"] not in ("contract", "visual") or values["Lane"] not in ("off", "standard", "flagship"):
        return None, "recovery_receipt_invalid"
    tail = ("Source truth hash", "Viewport", "State hash", "Strategy hash", "Pixel hash")
    if values["Kind"] == "contract":
        if any(values[key] != "n/a" for key in tail):
            return None, "recovery_receipt_invalid"
    else:
        if values["Lane"] not in ("standard", "flagship"):
            return None, "recovery_receipt_invalid"
        if not HEX64.match(values["Source truth hash"]) or not HEX64.match(values["State hash"]):
            return None, "recovery_receipt_invalid"
        if not HEX64.match(values["Strategy hash"]) or not HEX64.match(values["Pixel hash"]):
            return None, "recovery_receipt_invalid"
        if viewport_dims(values["Viewport"]) is None:
            return None, "recovery_receipt_invalid"
    return values, None


def write_recovery(path, values):
    text = "".join("%s: %s\n" % (key, values[key]) for key in RECOVERY_KEYS)
    atomic_text(path, text)


def norm_hash(value):
    normalized = " ".join(value.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def decimal_increment(value):
    digits = list(value)
    index = len(digits) - 1
    while index >= 0 and digits[index] == "9":
        digits[index] = "0"
        index -= 1
    if index < 0:
        digits.insert(0, "1")
    else:
        digits[index] = chr(ord(digits[index]) + 1)
    return "".join(digits)


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def validate_png(path, viewport):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "png_missing"
    if size <= 0 or size > MAX_PNG:
        return None, "png_size_invalid"
    try:
        data = open(path, "rb").read()
    except OSError:
        return None, "png_unreadable"
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None, "png_signature_invalid"
    offset = 8
    seen_ihdr = False
    seen_iend = False
    compressed = bytearray()
    width = height = 0
    chunk_index = 0
    while offset < len(data):
        if offset + 12 > len(data):
            return None, "png_truncated"
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        if length > MAX_PNG or offset + 12 + length > len(data):
            return None, "png_chunk_length_invalid"
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        stored = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        if (binascii.crc32(kind + payload) & 0xffffffff) != stored:
            return None, "png_crc_invalid"
        if kind not in (b"IHDR", b"IDAT", b"IEND"):
            return None, "png_chunk_not_allowed"
        if kind == b"IHDR":
            if chunk_index != 0 or seen_ihdr or length != 13:
                return None, "png_ihdr_invalid"
            seen_ihdr = True
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if not (0 < width <= MAX_DIM and 0 < height <= MAX_DIM):
                return None, "png_dimensions_invalid"
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                return None, "png_profile_invalid"
        elif kind == b"IDAT":
            if not seen_ihdr or seen_iend or length == 0:
                return None, "png_idat_invalid"
            compressed.extend(payload)
            if len(compressed) > MAX_PNG:
                return None, "png_compressed_oversize"
        else:
            if not seen_ihdr or not compressed or seen_iend or length != 0:
                return None, "png_iend_invalid"
            seen_iend = True
            if offset + 12 + length != len(data):
                return None, "png_trailing_data"
        offset += 12 + length
        chunk_index += 1
    if not seen_iend or not compressed:
        return None, "png_incomplete"
    expected = height * (1 + width * 4)
    if expected > MAX_RAW:
        return None, "png_raw_oversize"
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error:
        return None, "png_zlib_invalid"
    if len(raw) != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        return None, "png_scanline_length_invalid"
    wanted = viewport_dims(viewport)
    if wanted is None or (width, height) != wanted:
        return None, "png_viewport_mismatch"
    stride = width * 4
    previous = bytearray(stride)
    digest = hashlib.sha256()
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        if filter_type > 4:
            return None, "png_filter_invalid"
        source = raw[pos:pos + stride]
        pos += stride
        row = bytearray(stride)
        for i, byte in enumerate(source):
            left = row[i - 4] if i >= 4 else 0
            up = previous[i]
            upper_left = previous[i - 4] if i >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            else:
                predictor = paeth(left, up, upper_left)
            row[i] = (byte + predictor) & 0xff
        digest.update(row)
        previous = row
    return {"width": width, "height": height, "pixel_hash": digest.hexdigest()}, None


def qa_contract(run_dir, lane, root, affected, route_receipt_path, recovery, errors):
    path = os.path.join(run_dir, "DESIGN-QA.md")
    values, field_errors = exact_fields(path, QA_KEYS)
    if field_errors:
        errors.extend("qa_" + item for item in field_errors)
        return None
    if values["Lane"] != lane:
        errors.append("qa_lane_mismatch")
    source_match = re.match(r"^(project-system|visual-reference):(.+)$", values["Source truth"])
    if not source_match or not meaningful(source_match.group(2)):
        errors.append("source_truth_invalid")
    image_match = re.match(r"^screenshot:evidence/([A-Za-z0-9][A-Za-z0-9_-]{0,127}\.png)$",
                           values["Implementation evidence"])
    if not image_match:
        errors.append("implementation_evidence_invalid")
        image_path = None
    else:
        evidence_dir = os.path.join(run_dir, "evidence")
        image_path = os.path.join(evidence_dir, image_match.group(1))
        if os.path.islink(evidence_dir) or os.path.islink(image_path):
            errors.append("evidence_symlink")
        elif os.path.realpath(os.path.dirname(image_path)) != os.path.realpath(evidence_dir):
            errors.append("evidence_escape")
        elif not os.path.isfile(image_path):
            errors.append("evidence_missing")
    viewport = values["Viewport"]
    viewport_valid = viewport_dims(viewport) is not None
    if not viewport_valid:
        errors.append("viewport_invalid")
    if not meaningful(values["State"]):
        errors.append("state_invalid")
    if not meaningful(values["Strategy"]):
        errors.append("strategy_invalid")
    if values["Deterministic checks"] != "passed":
        errors.append("deterministic_checks_not_passed")
    if values["Comparison history"] not in ("initial-capture", "fix-capture-compare"):
        errors.append("comparison_history_invalid")
    for severity in ("P0", "P1", "P2", "P3"):
        if not re.match(r"^[0-9]+$", values["Open " + severity]):
            errors.append("open_%s_invalid" % severity.lower())
    if values["Open P0"] != "0": errors.append("open_p0")
    if values["Open P1"] != "0": errors.append("open_p1")
    if values["Open P2"] != "0": errors.append("open_p2")
    if lane == "flagship" and values["Open P3"] != "0": errors.append("open_p3")
    if values["Final result"] != "passed":
        errors.append("final_result_not_passed")
    png = None
    if image_path and os.path.isfile(image_path) and viewport_valid:
        png, png_error = validate_png(image_path, viewport)
        if png_error:
            errors.append(png_error)
        else:
            try:
                newest = os.stat(route_receipt_path).st_mtime_ns
                for rel in affected:
                    candidate = os.path.join(root, rel)
                    if os.path.lexists(candidate):
                        newest = max(newest, os.lstat(candidate).st_mtime_ns)
                if recovery and recovery.get("Status") == "closed":
                    newest = max(newest, os.stat(os.path.join(run_dir, "FRONTEND-QUALITY-RECOVERY")).st_mtime_ns)
                screenshot_mtime = os.stat(image_path).st_mtime_ns
            except OSError:
                errors.append("freshness_input_unavailable")
                return None
            if screenshot_mtime <= newest:
                errors.append("screenshot_stale")
    if not source_match or not png or not meaningful(values["State"]) or not meaningful(values["Strategy"]):
        return None
    return {
        "lane": lane,
        "source_hash": norm_hash(values["Source truth"]),
        "viewport": viewport,
        "state_hash": norm_hash(values["State"]),
        "strategy_hash": norm_hash(values["Strategy"]),
        "pixel_hash": png["pixel_hash"],
        "comparison": values["Comparison history"],
        "image_path": image_path,
    }


def receipt_from_meta(status, attempt, kind, lane, meta=None):
    values = {
        "Version": "1", "Status": status, "Attempt": str(attempt), "Kind": kind,
        "Lane": lane, "Source truth hash": "n/a", "Viewport": "n/a",
        "State hash": "n/a", "Strategy hash": "n/a", "Pixel hash": "n/a",
    }
    if kind == "visual" and meta:
        values.update({
            "Source truth hash": meta["source_hash"], "Viewport": meta["viewport"],
            "State hash": meta["state_hash"], "Strategy hash": meta["strategy_hash"],
            "Pixel hash": meta["pixel_hash"],
        })
    return values


def recovery_transition(state_path, frontend, owns, global_recovery, verdict):
    if frontend not in ("clean", "active") or owns not in ("no", "yes") or global_recovery not in ("clean", "active"):
        return False, "recovery_state_invalid"
    if frontend == "clean" and owns == "yes":
        return False, "recovery_state_invalid"
    if verdict == "CLOSED":
        if frontend == "clean" and owns == "no" and global_recovery == "clean":
            new = ("active", "yes", "active")
        elif frontend == "clean" and owns == "no" and global_recovery == "active":
            new = ("active", "no", "active")
        elif frontend == "active" and owns == "no" and global_recovery == "clean":
            new = ("active", "yes", "active")
        elif frontend == "active" and owns == "no" and global_recovery == "active":
            new = ("active", "no", "active")
        else:
            new = ("active", "yes", "active")
    else:
        if frontend == "clean" and owns == "no":
            new = ("clean", "no", global_recovery)
        elif frontend == "active" and owns == "no":
            new = ("clean", "no", global_recovery)
        else:
            new = ("clean", "no", "clean")
    replace_state(state_path, {
        "Frontend quality recovery": new[0],
        "Frontend quality recovery owns global": new[1],
        "Recovery": new[2],
    })
    return True, None


args = sys.argv[1:]
run_arg = None
record_start_mode = False
record_routing_mode = False
write = False
i = 0
while i < len(args):
    arg = args[i]
    if arg == "--record-start":
        record_start_mode = True
    elif arg == "--record-routing":
        record_routing_mode = True
    elif arg == "--write":
        write = True
    elif arg == "--pretty":
        pass
    elif arg in ("--help", "-h"):
        print("usage: frontend-quality-gate.sh <run-dir> [--record-start|--record-routing] [--write]")
        raise SystemExit(0)
    elif arg.startswith("-") or run_arg is not None:
        emit("CLOSED", "malformed", ["unexpected_argument"])
    else:
        run_arg = arg
    i += 1
if not run_arg or (record_start_mode and record_routing_mode):
    emit("CLOSED", "malformed", ["missing_or_conflicting_mode"])
if (record_start_mode or record_routing_mode) and not write:
    emit("CLOSED", "write_required", ["write_required"])

run_dir = os.path.realpath(run_arg)
if not os.path.isdir(run_dir) or os.path.islink(run_arg):
    emit("CLOSED", "run_dir_invalid", ["run_dir_invalid"])
try:
    root_raw = run(["git", "-C", run_dir, "rev-parse", "--show-toplevel"])
    root = os.path.realpath(root_raw.decode("utf-8", "surrogateescape").strip())
except Exception:
    emit("CLOSED", "git_root_missing", ["git_root_missing"])
state_path = os.path.join(run_dir, "STATE.md")
active_path = os.path.join(root, ".kimiflow", "session", "ACTIVE_RUN.json")
routing_path = os.path.join(run_dir, "FRONTEND-ROUTING-RECEIPT")
recovery_path = os.path.join(run_dir, "FRONTEND-QUALITY-RECOVERY")
qa_path = os.path.join(run_dir, "DESIGN-QA.md")

raw_lines, values, affected, state_errors = parse_state(state_path)
if raw_lines is None:
    emit("CLOSED", state_errors[0], state_errors)
active, active_error = load_active(active_path)
marker_present = isinstance(active, dict) and "frontend_quality_contract" in active
marker = active.get("frontend_quality_contract") if marker_present else None
selector_presence = sum(len(values.get(key, [])) for key in SELECTORS)
sidecar_presence = any(os.path.lexists(path) for path in (routing_path, recovery_path, qa_path))

if selector_presence == 0 and not marker_present and not sidecar_presence:
    if active_error not in (None, "active_run_missing"):
        emit("CLOSED", active_error, [active_error])
    if record_start_mode or record_routing_mode:
        emit("CLOSED", "contract_missing", ["contract_missing"])
    emit("OPEN", "not-required", [])
if selector_presence == 0:
    emit("CLOSED", "contract_missing", ["contract_missing"])

errors = list(state_errors)
contract = state_one(values, "Frontend quality contract", errors, "duplicate_contract" if len(values.get("Frontend quality contract", [])) > 1 else None)
lane = state_one(values, "Frontend quality", errors, "duplicate_lane" if len(values.get("Frontend quality", [])) > 1 else None)
routing = state_one(values, "Frontend quality routing", errors)
evidence = state_one(values, "Frontend quality evidence", errors)
basis = state_one(values, "Frontend quality basis", errors)
start_value = state_one(values, "Frontend quality start", errors)
frontend_recovery = state_one(values, "Frontend quality recovery", errors)
owns_global = state_one(values, "Frontend quality recovery owns global", errors)
mode = state_one(values, "Mode", errors, "duplicate_mode" if len(values.get("Mode", [])) > 1 else None)
global_recovery = state_one(values, "Recovery", errors, "duplicate_global_recovery" if len(values.get("Recovery", [])) > 1 else None)

if contract != "1": errors.append("unsupported_contract")
if lane not in ("off", "standard", "flagship"): errors.append("lane_invalid")
if mode not in ("feature", "fix", "audit"): errors.append("mode_invalid")
if frontend_recovery not in ("clean", "active"): errors.append("frontend_recovery_invalid")
if owns_global not in ("no", "yes"): errors.append("frontend_recovery_owner_invalid")
if global_recovery not in ("clean", "active"): errors.append("global_recovery_invalid")
if frontend_recovery == "clean" and owns_global == "yes": errors.append("recovery_state_invalid")

if active_error:
    errors.append(active_error)
elif not active_matches(active, root, run_dir):
    errors.append("active_run_mismatch")
if not marker_present:
    errors.append("active_contract_marker_missing")
elif marker != 1 or isinstance(marker, bool):
    errors.append("active_contract_marker_invalid")
started_head = active.get("started_head", "") if isinstance(active, dict) else ""
if not re.match(r"^[0-9a-f]{40}$", started_head):
    errors.append("started_head_invalid")

if record_start_mode:
    # The marker is intentionally absent before the first successful start record.
    start_errors = [code for code in errors if code != "active_contract_marker_missing"]
    expected_start = (
        (lane == "off", "start_lane_invalid"),
        (routing == "provisional", "start_routing_invalid"),
        (evidence == "pending", "start_evidence_invalid"),
        (basis == "pending", "start_basis_invalid"),
        (start_value in ("pending", "clean@%s" % started_head), "start_value_invalid"),
        (frontend_recovery == "clean", "start_recovery_invalid"),
        (owns_global == "no", "start_recovery_owner_invalid"),
        (global_recovery == "clean", "start_global_recovery_invalid"),
    )
    start_errors.extend(code for valid, code in expected_start if not valid)
    if start_errors:
        emit("CLOSED", start_errors[0], start_errors)
    try:
        head_raw = run(["git", "rev-parse", "HEAD"], cwd=root)
        current_head = head_raw.decode("ascii").strip()
        delta = git_delta(root, current_head)
    except Exception as exc:
        emit("CLOSED", "git_delta_invalid", [str(exc).split(":")[0]])
    if current_head != started_head:
        emit("CLOSED", "start_head_mismatch", ["start_head_mismatch"])
    if delta:
        emit("CLOSED", "dirty_start", ["dirty_start"])
    active["frontend_quality_contract"] = 1
    try:
        atomic_json(active_path, active)
        replace_state(state_path, {"Frontend quality start": "clean@%s" % started_head})
    except (OSError, ValueError) as exc:
        emit("CLOSED", "start_write_failed", [str(exc)])
    emit("OPEN", "start-recorded", [])

if errors:
    emit("CLOSED", errors[0], errors)
if start_value != "clean@%s" % started_head:
    emit("CLOSED", "start_not_recorded", ["start_not_recorded"])
if not affected:
    emit("CLOSED", "affected_files_empty", ["affected_files_empty"])

try:
    actual_paths = git_delta(root, started_head)
except Exception as exc:
    emit("CLOSED", "git_delta_invalid", [str(exc).split(":")[0]])
if set(affected) != actual_paths:
    emit("CLOSED", "affected_files_mismatch", ["affected_files_mismatch"])
request_file, request_name = request_path(run_dir, mode)
if not request_file or not os.path.isfile(request_file) or os.path.islink(request_file):
    emit("CLOSED", "request_file_missing", ["request_file_missing"])
try:
    request_bytes = open(request_file, "rb").read()
    request_text = request_bytes.decode("utf-8")
except (OSError, UnicodeError):
    emit("CLOSED", "request_file_invalid", ["request_file_invalid"])
route_errors = []
evidence_value(evidence, mode, lane, route_errors)
if mode == "feature" and has_ui_path(actual_paths) and lane == "off":
    route_errors.append("lane_route_mismatch")
if mode == "feature" and flagship_intent(request_text) and lane != "flagship":
    route_errors.append("flagship_route_mismatch")
if mode in ("fix", "audit") and lane != "off":
    route_errors.append("lane_route_mismatch")
if route_errors:
    emit("CLOSED", route_errors[0], route_errors)
expected_basis = basis_hash(marker, start_value, mode, request_name, request_bytes, actual_paths, lane, evidence)

if record_routing_mode:
    try:
        replace_state(state_path, {
            "Frontend quality routing": "final",
            "Frontend quality basis": expected_basis,
        })
        atomic_text(routing_path, "Basis: %s\n" % expected_basis)
    except (OSError, ValueError) as exc:
        emit("CLOSED", "routing_write_failed", [str(exc)])
    emit("OPEN", "routing-recorded", [])

base_errors = []
if routing != "final": base_errors.append("routing_not_final")
if basis != expected_basis: base_errors.append("routing_basis_stale")
receipt_basis, receipt_error = routing_receipt(routing_path)
if receipt_error: base_errors.append(receipt_error)
elif receipt_basis != expected_basis: base_errors.append("routing_receipt_mismatch")
recovery, recovery_error = recovery_receipt(recovery_path)
if recovery_error:
    emit("CLOSED", recovery_error, [recovery_error])

# Fail-closed interrupted CLOSED transition: receipt was durable before STATE.
if recovery and recovery["Status"] == "closed" and frontend_recovery == "clean":
    if write:
        ok, transition_error = recovery_transition(state_path, frontend_recovery, owns_global, global_recovery, "CLOSED")
        if not ok: emit("CLOSED", transition_error, [transition_error])
    emit("CLOSED", "recovery_transition_incomplete", ["recovery_transition_incomplete"])
if frontend_recovery == "active" and not recovery:
    emit("CLOSED", "recovery_receipt_missing", ["recovery_receipt_missing"])

meta = None
if lane == "off":
    if os.path.lexists(qa_path):
        base_errors.append("qa_not_allowed_for_off")
else:
    meta = qa_contract(run_dir, lane, root, affected, routing_path, recovery, base_errors)

if recovery and recovery["Kind"] == "visual":
    identity_errors = []
    if recovery["Lane"] != lane: identity_errors.append("recovery_lane_changed")
    if meta is None:
        identity_errors.append("recovery_visual_evidence_missing")
    else:
        if recovery["Source truth hash"] != meta["source_hash"]: identity_errors.append("source_truth_changed")
        if recovery["Viewport"] != meta["viewport"]: identity_errors.append("viewport_changed")
        if recovery["State hash"] != meta["state_hash"]: identity_errors.append("state_changed")
        if recovery["Status"] == "closed":
            if meta["comparison"] != "fix-capture-compare": identity_errors.append("comparison_required")
            if recovery["Strategy hash"] == meta["strategy_hash"]: identity_errors.append("strategy_not_changed")
            if recovery["Pixel hash"] == meta["pixel_hash"]: identity_errors.append("pixel_not_changed")
        else:
            if recovery["Strategy hash"] != meta["strategy_hash"]: identity_errors.append("resolved_strategy_mismatch")
            if recovery["Pixel hash"] != meta["pixel_hash"]: identity_errors.append("resolved_pixel_mismatch")
    base_errors.extend(identity_errors)

if recovery and recovery["Status"] == "resolved" and frontend_recovery == "active" and not base_errors:
    if not write:
        emit("CLOSED", "recovery_transition_incomplete", ["recovery_transition_incomplete"])
    ok, transition_error = recovery_transition(state_path, frontend_recovery, owns_global, global_recovery, "OPEN")
    if not ok: emit("CLOSED", transition_error, [transition_error])
    emit("OPEN", "clean", [])

if base_errors:
    if write:
        attempt = decimal_increment(recovery["Attempt"]) if recovery else "1"
        if recovery and recovery.get("Status") == "closed" and recovery.get("Kind") == "visual":
            closed = dict(recovery)
            closed["Attempt"] = str(attempt)
        else:
            visual_codes = {"final_result_not_passed", "open_p0", "open_p1", "open_p2", "open_p3",
                            "strategy_not_changed", "pixel_not_changed", "comparison_required"}
            visual = lane in ("standard", "flagship") and meta is not None and any(code in visual_codes for code in base_errors)
            kind = "visual" if visual else "contract"
            closed = receipt_from_meta("closed", attempt, kind, lane, meta if visual else None)
        try:
            write_recovery(recovery_path, closed)
        except (OSError, ValueError) as exc:
            emit("CLOSED", "recovery_write_failed", [str(exc)])
        ok, transition_error = recovery_transition(state_path, frontend_recovery, owns_global, global_recovery, "CLOSED")
        if not ok: emit("CLOSED", transition_error, [transition_error])
    emit("CLOSED", base_errors[0], base_errors)

if write:
    if recovery and recovery["Status"] == "closed":
        resolved = receipt_from_meta("resolved", recovery["Attempt"], recovery["Kind"], lane,
                                     meta if recovery["Kind"] == "visual" else None)
        try:
            write_recovery(recovery_path, resolved)
        except (OSError, ValueError) as exc:
            emit("CLOSED", "recovery_write_failed", [str(exc)])
    ok, transition_error = recovery_transition(state_path, frontend_recovery, owns_global, global_recovery, "OPEN")
    if not ok: emit("CLOSED", transition_error, [transition_error])
emit("OPEN", "clean", [])
PY
