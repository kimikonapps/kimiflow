"""`metrics` subcommand: token-economics read (default / --global) + the global-metrics
purge (--global-purge). Behavioral port of the Bash cmd_metrics (2087-2126) at
kimiflow--v0.1.50. Pure composition of already-ported summaries + global_metrics helpers
(it reads and purges; it records nothing, so no salt/hash/record infra is needed here)."""
import os

from . import contracts, global_metrics, summaries
from .cli import die, resolve_root, usage


def run(argv):
    root = ""
    pretty = False
    global_only = False
    global_purge = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--global":
            global_only = True
        elif arg == "--global-purge":
            global_purge = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("metrics: unknown argument: %s" % arg, 2)
        i += 1

    # Bash checks purge FIRST, then global, then default.
    if global_purge:
        base = global_metrics.base_dir()   # None when no HOME/KIMIFLOW_HOME (Bash `|| true` -> empty)
        removed = False
        salt_removed = False
        if base:
            metrics_file = os.path.join(base, "token-economics.jsonl")
            salt_file = os.path.join(base, "salt")
            if os.path.isfile(metrics_file):
                try:
                    os.remove(metrics_file)   # Bash `rm -f "$file" && removed=true`
                    removed = True
                except OSError:
                    pass
            if os.path.isfile(salt_file):
                try:
                    os.remove(salt_file)
                    salt_removed = True
                except OSError:
                    pass
        contracts.json_print({
            "schema_version": 1,
            "status": "purged",
            "path": global_metrics.display_path(),
            "removed": removed,
            "salt_removed": salt_removed,
        }, pretty)
        return 0

    if global_only:
        contracts.json_print(summaries.global_efficiency_summary_json(), pretty)
        return 0

    root = resolve_root(root)
    project = os.path.join(root, ".kimiflow", "project")
    usage_obj = summaries.usage_summary_json(os.path.join(project, "MEMORY-USAGE.json"))
    run_economics = summaries.economics_summary_json(os.path.join(project, "MEMORY-ECONOMICS.jsonl"))
    global_efficiency = summaries.global_efficiency_summary_json()
    # Bash `$usage + {usage:$usage, run_economics:$run_economics, global_efficiency:$global_efficiency}`:
    # spread the usage-summary keys at top level, then append the three nested objects.
    out = dict(usage_obj)
    out["usage"] = usage_obj
    out["run_economics"] = run_economics
    out["global_efficiency"] = global_efficiency
    contracts.json_print(out, pretty)
    return 0
