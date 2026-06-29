"""Global (cross-project, local-anonymous) metrics location + enablement helpers.
Ports of the Bash global_metrics_enabled / global_metrics_base_dir /
global_metrics_display_path @ kimiflow--v0.1.50 (366-385), plus the salt + anonymous
hashing infra (hash_text/ensure_global_metrics_salt/anonymous_hash_id, 387-432) used by
the economics global-row writer."""
import hashlib
import os
import secrets

# Bash `case "$KIMIFLOW_GLOBAL_METRICS" in off|OFF|0|false|FALSE|no|NO) return 1`.
_DISABLED = {"off", "OFF", "0", "false", "FALSE", "no", "NO"}


def enabled():
    # Bash global_metrics_enabled: KIMIFLOW_GLOBAL_METRICS (default "on" when unset OR
    # empty); only these exact off/0/false/no spellings disable it (anything else -> on).
    value = os.environ.get("KIMIFLOW_GLOBAL_METRICS", "on")
    return value not in _DISABLED


def base_dir():
    # Bash global_metrics_base_dir: KIMIFLOW_HOME, else HOME/.kimiflow; None (Bash
    # `return 1`) when neither yields a usable base or the base is empty / "/".
    base = os.environ.get("KIMIFLOW_HOME", "")
    if not base:
        home = os.environ.get("HOME", "")
        if not home:
            return None
        base = home + "/.kimiflow"
    if not base or base == "/":
        return None
    return base + "/metrics"


def display_path():
    # Bash global_metrics_display_path: the fixed user-facing path.
    return "~/.kimiflow/metrics/token-economics.jsonl"


def hash_text(data):
    # Bash hash_text (387-404): shasum -a 256 -> sha256sum -> fail. The stdlib hash is
    # always available (so the port never fails; see spec 12 row 180), and hashes the
    # exact bytes of `data` (no added newline -- callers control the input).
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def ensure_global_metrics_salt(directory):
    # Bash ensure_global_metrics_salt (406-427): create `directory` (0700), generate a
    # 0600 `salt` file once (openssl rand -hex 32; the iso/pid/RANDOM fallback is
    # unreachable when the stdlib is present), and return its FIRST line (sed -n '1p',
    # trailing newline stripped by the `$()` caller). Returns "" on any failure (Bash
    # `return 1`). Callers use `salt:value` as the hash input.
    salt_file = directory + "/salt"
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return ""
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    if not os.path.isfile(salt_file):
        salt = secrets.token_hex(32)
        old_umask = os.umask(0o077)
        try:
            with open(salt_file, "w", encoding="utf-8") as handle:
                handle.write(salt + "\n")
        except OSError:
            return ""
        finally:
            os.umask(old_umask)
        try:
            os.chmod(salt_file, 0o600)
        except OSError:
            pass
    try:
        with open(salt_file, "r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return ""
    return first.rstrip("\n")


def anonymous_hash_id(salt, value):
    # Bash anonymous_hash_id (429-432): `printf '%s:%s' "$salt" "$value" | hash_text`
    # -- exactly the bytes `salt:value` (no trailing newline).
    return hash_text("%s:%s" % (salt, value))
