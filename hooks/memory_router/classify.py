"""`classify` subcommand: heuristic classification of a learning text. Stateless."""
import re
import sys

from . import contracts

# Lowercased-ASCII pattern set, lifted verbatim from Bash classify_text @ v0.1.50.
# (Output depends only on these ASCII patterns, so str.lower() vs C-locale tr cannot
#  change the result — only ASCII matches drive classification.)
_SECURITY = re.compile(r"(secret|token|credential|password|private key|\.env|vulnerab|exploit|auth bypass|cve-|xss|csrf|sql injection)")
_PRIVATE = re.compile(r"(/users/|/home/|customer|client|kunde|kundendaten|private|vault|obsidian)")
_TRIVIAL = re.compile(r"^(ok|done|fixed|typo|scratch|temporary)$", re.MULTILINE)
_DOC = re.compile(r"(readme|repo doc|documentation|docs/|architecture doc|onboarding|public docs|publish-safe)")
_VAULT = re.compile(r"(cross-project|preference|always|remember|pattern|lesson|decision|learned|wiederkehrend|arbeitsstil|vault)")
_PROJECT = re.compile(r"(test|build|release|convention|standard|decision|architecture|flow|hook|launcher|codex|claude|project map|memory|vault|kimiflow)")


def classify_text(text):
    lower = text.lower()
    words = len(text.split())
    sensitivity = "normal"
    target = "run_only"
    confidence = "medium"
    reasons = []
    vault_allowed = True
    repo_doc_allowed = False
    sanitized_required = False

    if _SECURITY.search(lower):
        sensitivity = "security"
        vault_allowed = False
        repo_doc_allowed = False
        sanitized_required = True
        reasons.append("security_sensitive")
    elif _PRIVATE.search(lower):
        sensitivity = "private"
        vault_allowed = True
        repo_doc_allowed = False
        sanitized_required = True
        reasons.append("private_or_local_detail")

    if words < 4 or _TRIVIAL.search(lower):
        target = "skip"
        confidence = "high"
        reasons.append("too_small_or_trivial")
    elif _DOC.search(lower):
        target = "repo_doc_candidate"
        if sensitivity in ("normal", "public"):
            repo_doc_allowed = True
        reasons.append("documentation_candidate")
    elif _VAULT.search(lower):
        target = "vault"
        reasons.append("long_term_or_cross_project")
    elif _PROJECT.search(lower):
        target = "project_memory"
        reasons.append("project_reusable")

    if sensitivity == "security":
        target = "project_memory"
        confidence = "high"

    return {
        "schema_version": 1,
        "classification": {
            "target": target,
            "sensitivity": sensitivity,
            "confidence": confidence,
            "reasons": reasons,
            "vault_allowed": vault_allowed,
            "repo_doc_allowed": repo_doc_allowed,
            "sanitized_required": sanitized_required,
        },
    }


def _read_input_head(path):
    # Bash: text="$(sed -n '1,160p' "$input")" — first 160 lines, trailing newline stripped
    # by command substitution. splitlines()[:160] joined by "\n" reproduces that.
    with open(path, "r", encoding="utf-8") as handle:
        return "\n".join(handle.read().splitlines()[:160])


def run(argv):
    from .__main__ import die, usage  # lazy import: keeps module load acyclic
    text = ""
    input_path = ""
    pretty = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--input":
            i += 1
            input_path = argv[i] if i < len(argv) else ""
        elif arg == "--text":
            i += 1
            text = argv[i] if i < len(argv) else ""
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("classify: unknown argument: %s" % arg, 2)
        i += 1

    if input_path:
        import os
        if not os.path.isfile(input_path):
            return die("input not found: %s" % input_path, 2)
        text = _read_input_head(input_path)
    if not text:
        return die("classify requires --input or --text", 2)

    sys.stdout.write(contracts.dumps(classify_text(text), pretty) + "\n")
    return 0
