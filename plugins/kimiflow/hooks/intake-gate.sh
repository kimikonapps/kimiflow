#!/usr/bin/env bash
# kimiflow — Contract-3 Product Intake PreToolUse guard.
# Supported local host tools are guarded mechanically; hosts/tools that do not
# emit these hook events remain outside this enforcement boundary.
set -u

command -v python3 >/dev/null 2>&1 || exit 0
KIMIFLOW_INTAKE_HOOKS_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 0
export KIMIFLOW_INTAKE_HOOKS_DIR
exec python3 -c '
import hashlib, json, os, re, shlex, subprocess, sys

def deny(reason):
    print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"kimiflow intake-gate: " + reason}}, separators=(",",":")))
    raise SystemExit(0)

def git_root(cwd):
    try:
        p=subprocess.run(["git","-C",cwd or os.getcwd(),"rev-parse","--show-toplevel"],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    except OSError:
        return ""

try:
    data=json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
if not isinstance(data,dict): raise SystemExit(0)
ti=data.get("tool_input") if isinstance(data.get("tool_input"),dict) else {}
root=git_root(data.get("cwd") or ti.get("cwd") or data.get("working_directory") or os.getcwd())
if not root: raise SystemExit(0)
active_path=os.path.join(root,".kimiflow/session/ACTIVE_RUN.json")
try:
    if os.path.islink(active_path): deny("active-run authority is unsafe")
    with open(active_path,encoding="utf-8") as f: active=json.load(f)
except (OSError,ValueError):
    raise SystemExit(0)
if not isinstance(active,dict) or active.get("status") != "active" or active.get("intent_contract") != "3" or active.get("mode") != "feature" or active.get("scope") == "trivial":
    raise SystemExit(0)
owner=active.get("owner") if isinstance(active.get("owner"),dict) else None
session=data.get("session_id")
host=os.environ.get("KIMIFLOW_HOST","") or ("codex" if os.environ.get("CODEX_THREAD_ID") or os.environ.get("PLUGIN_ROOT") else "claude")
if not owner or not session or owner.get("host") != host or owner.get("session_id") != str(session):
    raise SystemExit(0)
run_rel=active.get("run","")
if not isinstance(run_rel,str) or not run_rel.startswith(".kimiflow/"): deny("pinned run path is invalid")
run_dir=os.path.normpath(os.path.join(root,run_rel))
if not run_dir.startswith(os.path.join(root,".kimiflow")+os.sep): deny("pinned run path escapes .kimiflow")

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(65536),b""): h.update(chunk)
    return "sha256:"+h.hexdigest()

def valid_receipt(round_no):
    request_name="INTAKE.md" if round_no == 1 else "INTAKE-2.md"
    rp=os.path.join(run_dir,"INTAKE-RECEIPT-%d.json"%round_no)
    qp=os.path.join(run_dir,request_name)
    try:
        if os.path.islink(rp) or os.path.islink(qp): return False
        with open(rp,encoding="utf-8") as f: value=json.load(f)
        expected={"schema_version","contract","round","request","request_digest","channel","responded_at"}
        return isinstance(value,dict) and set(value)==expected and value.get("schema_version")==1 and value.get("contract")==3 and value.get("round")==round_no and value.get("request")==request_name and value.get("request_digest")==digest(qp) and value.get("channel") in ("chat","native_tool")
    except (OSError,ValueError): return False

pending_round=active.get("intake_round") if active.get("awaiting_user") is True and active.get("awaiting_kind") == "intake" else None
round_one_ok=valid_receipt(1)
required_ok=valid_receipt(pending_round) if pending_round in (1,2) else round_one_ok
expected_request="INTAKE-2.md" if pending_round == 2 else "INTAKE.md"
tool=data.get("tool_name") or data.get("name") or ""
if not tool and isinstance(data.get("tool"),dict): tool=data["tool"].get("name","")
command=ti.get("command") or (ti.get("args") or {}).get("command") if isinstance(ti.get("args"),dict) else ti.get("command")
command=command or data.get("command") or data.get("shell_command") or ""
protected=(".kimiflow/session/ACTIVE_RUN.json","INTAKE-RECEIPT-1.json","INTAKE-RECEIPT-2.json","INTENT-LOCK.json")

def mutation_mentions_protected(text):
    if not isinstance(text,str): return False
    return any(name in text for name in protected)

def read_only_shell(text):
    if not isinstance(text,str) or not text.strip(): return False
    if re.search(r"[\r\n;|&<>`]|\$\(",text): return False
    if re.search(r"(^|[^<])>|>>|\b(rm|mv|cp|touch|mkdir|rmdir|chmod|chown|install|tee|truncate|patch|apply_patch)\b|\bsed\s+-[^ ]*i|\bgit\s+(add|commit|push|tag|checkout|switch|merge|rebase|reset|clean)\b|\b(npm|pnpm|yarn|pip|cargo|go)\s+(install|add|build|run|test)|\b(make|cmake|python|python3|node|ruby|perl)\b",text): return False
    segments=re.split(r"\s*(?:&&|\|\||;)\s*",text.strip())
    return all(re.match(r"^(?:env\s+[A-Za-z_][A-Za-z0-9_]*=[^ ]+\s+)*(?:rg|grep|cat|head|tail|less|ls|find|pwd|wc|sort|uniq|cut|awk|sed\s+-n|git\s+(?:status|diff|log|show|rev-parse|ls-files)|shasum|sha256sum|jq|stat|test|\[)\b",seg) for seg in segments if seg)

def allowed_setup_command(text):
    if not isinstance(text,str) or not text.strip(): return False
    if re.search(r"[\r\n;|<>`]|&&|\|\||\$\(",text): return False
    try: tokens=shlex.split(text,posix=True)
    except ValueError: return False
    if tokens and tokens[0]=="env": tokens=tokens[1:]
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*",tokens[0]):
        if tokens[0].split("=",1)[0] in ("KIMIFLOW_PLUGIN_ROOT","CLAUDE_PLUGIN_ROOT","KIMIFLOW_INTAKE_HOOKS_DIR"): return False
        tokens=tokens[1:]
    if tokens and tokens[0] in ("bash","sh"): tokens=tokens[1:]
    if not tokens: return False
    script_token=tokens[0]
    hooks_dir=os.path.realpath(os.environ.get("KIMIFLOW_INTAKE_HOOKS_DIR", ""))
    def trusted_script(name):
        candidate=script_token
        for var in ("KIMIFLOW_PLUGIN_ROOT","CLAUDE_PLUGIN_ROOT"):
            value=os.environ.get(var,"")
            for marker in ("$"+var,"${"+var+"}"):
                if candidate==marker+"/hooks/"+name:
                    if not value: return False
                    candidate=os.path.join(value,"hooks",name)
        if not os.path.isabs(candidate): candidate=os.path.join(root,candidate)
        return bool(hooks_dir) and os.path.realpath(candidate)==os.path.join(hooks_dir,name)
    script=os.path.basename(script_token); args=tokens[1:]
    if not trusted_script(script): return False
    if script=="active-run.sh" and args:
        if args[0] in ("status","phase-read","phase-read-status","phase-read-gate"):
            return True
        if args[0]=="await-user":
            return "--kind" in args and args[args.index("--kind")+1:args.index("--kind")+2]==["intake"] and "--round" in args and args[args.index("--round")+1:args.index("--round")+2] in (["1"],["2"])
    if script=="frontend-quality-gate.sh" and len(args)==3:
        candidate=args[0] if os.path.isabs(args[0]) else os.path.join(root,args[0])
        return os.path.realpath(candidate)==run_dir and set(args[1:])=={"--record-start","--write"}
    return False

def patch_paths(value):
    if not isinstance(value,str): return []
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$",value,flags=re.M)

if tool == "Bash" or command:
    if mutation_mentions_protected(command) and not read_only_shell(command): deny("authority files may be changed only by Kimiflow gate commands")
    if not required_ok:
        if not read_only_shell(command) and not allowed_setup_command(command):
            deny("complete the bounded Product Intake before planning or project mutation")
    raise SystemExit(0)

if tool in ("AskUserQuestion","request_user_input"):
    if not required_ok and pending_round not in (1,2): deny("register the intake request with active-run await-user first")
    raise SystemExit(0)

if tool in ("apply_patch","Edit","Write"):
    payload=ti.get("patch") or ti.get("input") or ti.get("content") or ""
    path=ti.get("file_path") or ti.get("path") or ""
    combined="%s\n%s"%(path,payload)
    if mutation_mentions_protected(combined): deny("authority files may be changed only by Kimiflow gate commands")
    if not required_ok:
        paths=patch_paths(payload) if tool == "apply_patch" else ([path] if path else [])
        normalized=[os.path.basename(p.strip()) for p in paths]
        if len(normalized)!=1 or normalized[0] != expected_request:
            deny("only the exact pending intake request artifact may be written before the response")
    raise SystemExit(0)

if not required_ok and tool in ("update_plan","TaskCreate","TaskUpdate","EnterPlanMode","ExitPlanMode"):
    deny("implementation planning starts only after the Product Intake response")
' "$@"
