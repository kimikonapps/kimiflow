#!/usr/bin/env bash
# kimiflow — Contract-3/4 Product Intake PreToolUse guard.
# Supported local host tools are guarded mechanically; hosts/tools that do not
# emit these hook events remain outside this enforcement boundary.
set -u

command -v python3 >/dev/null 2>&1 || exit 0
KIMIFLOW_INTAKE_HOOKS_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 0
export KIMIFLOW_INTAKE_HOOKS_DIR
exec python3 -c '
import hashlib, json, os, re, shlex, stat, subprocess, sys

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
reported_root=git_root(data.get("cwd") or ti.get("cwd") or data.get("working_directory") or os.getcwd())
try:
    hooks_dir=os.path.realpath(os.environ.get("KIMIFLOW_INTAKE_HOOKS_DIR", ""))
    if hooks_dir not in sys.path: sys.path.insert(0,hooks_dir)
    from kimiflow_core import active_run as active_run_contract
    root=active_run_contract.hook_root(json.dumps(data,separators=(",",":")),data)
except Exception:
    root=reported_root
if not root: raise SystemExit(0)
active_path=os.path.join(root,".kimiflow/session/ACTIVE_RUN.json")
try:
    if os.path.islink(active_path): deny("active-run authority is unsafe")
    with open(active_path,encoding="utf-8") as f: active=json.load(f)
except (OSError,ValueError):
    raise SystemExit(0)
if not isinstance(active,dict) or active.get("status") != "active" or active.get("intent_contract") not in ("3","4") or active.get("mode") != "feature" or active.get("scope") == "trivial":
    raise SystemExit(0)
intent_contract=int(active["intent_contract"])
try: intake_schema=int(active.get("intake_schema") or 1)
except (TypeError,ValueError): intake_schema=1
owner=active.get("owner") if isinstance(active.get("owner"),dict) else None
session=data.get("session_id")
host=os.environ.get("KIMIFLOW_HOST","") or ("codex" if os.environ.get("CODEX_THREAD_ID") or os.environ.get("PLUGIN_ROOT") else "claude")
if not owner or not session or owner.get("host") != host or owner.get("session_id") != str(session):
    raise SystemExit(0)
run_rel=active.get("run","")
if not isinstance(run_rel,str) or not run_rel.startswith(".kimiflow/"): deny("pinned run path is invalid")
run_dir=os.path.normpath(os.path.join(root,run_rel))
if not run_dir.startswith(os.path.join(root,".kimiflow")+os.sep): deny("pinned run path escapes .kimiflow")
expected_real_run=os.path.join(os.path.realpath(root),os.path.relpath(run_dir,root))
if os.path.realpath(run_dir)!=expected_real_run: deny("pinned run path is aliased")

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
        receipt_info=os.lstat(rp); request_info=os.lstat(qp)
        if not stat.S_ISREG(receipt_info.st_mode) or receipt_info.st_nlink!=1: return False
        if not stat.S_ISREG(request_info.st_mode) or request_info.st_nlink!=1: return False
        with open(rp,encoding="utf-8") as f: value=json.load(f)
        if not isinstance(value,dict): return False
        if intake_schema==2:
            expected={"schema_version","contract","round","stage","action","request","request_digest","contract_digest","user_language","channel","responded_at"}
            expected_stage="scope" if round_no==1 else "final"
            expected_action="scope_ready" if round_no==1 else "confirmed"
            return set(value)==expected and value.get("schema_version")==2 and value.get("contract")==4 and value.get("round")==round_no and value.get("stage")==expected_stage and value.get("action")==expected_action and value.get("request")==request_name and value.get("request_digest")==digest(qp) and re.fullmatch(r"sha256:[0-9a-f]{64}",str(value.get("contract_digest") or "")) is not None and value.get("user_language")==active.get("interaction_language") and value.get("channel") in ("chat","native_tool")
        expected={"schema_version","contract","round","request","request_digest","channel","responded_at"}
        return set(value)==expected and value.get("schema_version")==1 and value.get("contract")==intent_contract and value.get("round")==round_no and value.get("request")==request_name and value.get("request_digest")==digest(qp) and value.get("channel") in ("chat","native_tool")
    except (OSError,ValueError): return False

pending_round=active.get("intake_round") if active.get("awaiting_user") is True and active.get("awaiting_kind") == "intake" else None
round_one_ok=valid_receipt(1)
round_two_ok=valid_receipt(2) if intake_schema==2 else False
intake_conflict=active.get("intake_conflict") is True
required_ok=(round_two_ok if intake_schema==2 else (valid_receipt(pending_round) if pending_round in (1,2) else round_one_ok)) and not intake_conflict
scope_drafting=intake_schema==2 and round_one_ok and not round_two_ok and pending_round is None and not intake_conflict
expected_request="INTAKE-2.md" if pending_round == 2 or pending_round == 1 and intake_conflict else "INTAKE.md"
tool=data.get("tool_name") or data.get("name") or ""
if not tool and isinstance(data.get("tool"),dict): tool=data["tool"].get("name","")
command=ti.get("command") or (ti.get("args") or {}).get("command") if isinstance(ti.get("args"),dict) else ti.get("command")
command=command or data.get("command") or data.get("shell_command") or ""
protected=(".kimiflow/session/ACTIVE_RUN.json","INTAKE-RECEIPT-1.json","INTAKE-RECEIPT-2.json","INTENT-LOCK.json")

def trusted_command(value):
    if not isinstance(value,str): return None
    lines=value.splitlines()
    if len(lines)==1: return value
    if len(lines)!=2: return value
    try: prefix=shlex.split(lines[0],posix=True)
    except ValueError: return None
    if len(prefix)!=2 or prefix[0]!="export" or not prefix[1].startswith("KIMIFLOW_PLUGIN_ROOT="):
        return value
    plugin_root=prefix[1].split("=",1)[1]
    hooks_dir=os.path.realpath(os.environ.get("KIMIFLOW_INTAKE_HOOKS_DIR", ""))
    if not plugin_root or not hooks_dir or os.path.realpath(plugin_root)!=os.path.dirname(hooks_dir):
        return None
    return lines[1].replace("${KIMIFLOW_PLUGIN_ROOT}",plugin_root).replace("$KIMIFLOW_PLUGIN_ROOT",plugin_root)

def shell_segments(text):
    if not isinstance(text,str) or not text.strip() or "\0" in text: return None
    segments=[]; buffer=[]; quote=""; escaped=False; index=0
    while index < len(text):
        char=text[index]
        if escaped:
            buffer.append(char); escaped=False; index+=1; continue
        if char=="\\":
            buffer.append(char); escaped=True; index+=1; continue
        if quote:
            buffer.append(char)
            if char==quote: quote=""
            index+=1; continue
        if char in (chr(39),chr(34)):
            quote=char; buffer.append(char); index+=1; continue
        if char in "\r\n":
            segment="".join(buffer).strip()
            if segment: segments.append(segment)
            buffer=[]; index+=1; continue
        if char in "<>`" or text.startswith("$(",index): return None
        if char in ";|&":
            width=2 if index+1 < len(text) and text[index:index+2] in ("&&","||") else 1
            if char=="&" and width==1: return None
            segment="".join(buffer).strip()
            if not segment: return None
            segments.append(segment); buffer=[]; index+=width; continue
        buffer.append(char); index+=1
    if quote or escaped: return None
    segment="".join(buffer).strip()
    if not segment: return None
    segments.append(segment)
    return segments

def mutation_mentions_protected(text):
    if not isinstance(text,str): return False
    return any(name in text for name in protected)

def read_only_shell(text):
    segments=shell_segments(text)
    if segments is None: return False
    if re.search(r"\b(rm|mv|cp|touch|mkdir|rmdir|chmod|chown|install|tee|truncate|patch|apply_patch)\b|\bsed\s+-[^ ]*i|\bgit\s+(add|commit|push|tag|checkout|switch|merge|rebase|reset|clean)\b|\b(npm|pnpm|yarn|pip|cargo|go)\s+(install|add|build|run|test)|\b(make|cmake|python|python3|node|ruby|perl)\b",text): return False
    return all(re.match(r"^(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^ ]+\s+)*(?:rg|grep|cat|head|tail|less|ls|find|pwd|wc|sort|uniq|cut|awk|printf|echo|sed\s+-n|git\s+(?:status|diff|log|show|rev-parse|ls-files)|shasum|sha256sum|jq|stat|test|\[)\b",seg) for seg in segments)

def allowed_setup_command(text,allow_scope_research=False):
    if not isinstance(text,str) or not text.strip(): return False
    if re.search(r"[\r\n;|<>`]|&&|\|\||\$\(",text): return False
    try: tokens=shlex.split(text,posix=True)
    except ValueError: return False
    if tokens and tokens[0]=="env": tokens=tokens[1:]
    hooks_dir=os.path.realpath(os.environ.get("KIMIFLOW_INTAKE_HOOKS_DIR", ""))
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*",tokens[0]):
        name,value=tokens[0].split("=",1)
        if name in ("KIMIFLOW_PLUGIN_ROOT","CLAUDE_PLUGIN_ROOT"):
            if not value or os.path.realpath(value)!=os.path.dirname(hooks_dir): return False
        elif name=="KIMIFLOW_INTAKE_HOOKS_DIR": return False
        tokens=tokens[1:]
    if tokens and tokens[0] in ("bash","sh"): tokens=tokens[1:]
    if not tokens: return False
    script_token=tokens[0]
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
    def bounded_options(values,value_names,flag_names):
        parsed={}; flags=set(); index=0
        while index < len(values):
            key=values[index]
            if key in flag_names and key not in flags:
                flags.add(key); index+=1; continue
            if key in value_names and key not in parsed and index+1 < len(values):
                parsed[key]=values[index+1]; index+=2; continue
            return None,None
        return parsed,flags
    if script=="active-run.sh" and args:
        if args in (["--help"],["-h"]): return True
        if args[0]=="hook-health":
            return args[1:] in ([],["--require"],["--pretty"],["--require","--pretty"],["--pretty","--require"])
        if args[0] in ("status","next-action","phase-read","phase-read-status","phase-read-gate"):
            return True
        if args[0] in ("abort","park","fail"):
            parsed,flags=bounded_options(args[1:],{"--root","--reason"},{"--write","--pretty"})
            if parsed is None or not parsed.get("--reason") or "--write" not in flags: return False
            return "--root" not in parsed or os.path.realpath(parsed["--root"])==os.path.realpath(root)
        if args[0]=="await-user":
            return "--kind" in args and args[args.index("--kind")+1:args.index("--kind")+2]==["intake"] and "--round" in args and args[args.index("--round")+1:args.index("--round")+2] in (["1"],["2"])
    if script=="frontend-quality-gate.sh" and len(args)==3:
        candidate=args[0] if os.path.isabs(args[0]) else os.path.join(root,args[0])
        return os.path.realpath(candidate)==run_dir and set(args[1:])=={"--record-start","--write"}
    if script=="resolve-verbosity.sh" and args in (["get"],["get","--flag","quiet"],["get","--flag","balanced"],["get","--flag","verbose"]):
        return True
    if script=="workspace-preflight.sh":
        if args and args[0] in ("status","route"):
            parsed,flags=bounded_options(args[1:],{"--run","--root"},{"--write","--pretty"})
            if parsed is None: return False
            if "--root" in parsed and os.path.realpath(parsed["--root"])!=os.path.realpath(root): return False
            if args[0]=="status":
                return "--run" not in parsed
            return parsed.get("--run")==run_rel and "--write" in flags
    if script=="adaptive-control.sh":
        if args and args[0]=="classify":
            parsed,flags=bounded_options(args[1:],{"--run","--root"},{"--write","--pretty"})
            if parsed is None: return False
            if "--root" in parsed and os.path.realpath(parsed["--root"])!=os.path.realpath(root): return False
            return parsed.get("--run")==run_rel and "--write" in flags
    if script=="codebase-basis.sh" and allow_scope_research:
        if args in (["--help"],["-h"]): return True
        if args and args[0]=="create":
            seen=set(); index=1
            while index < len(args):
                key=args[index]
                if key in ("--write","--pretty") and key not in seen:
                    seen.add(key); index+=1; continue
                if key in ("--run","--root") and key not in seen and index+1 < len(args):
                    value=args[index+1]
                    if key=="--run" and value!=run_rel: return False
                    if key=="--root" and os.path.realpath(value)!=os.path.realpath(root): return False
                    seen.add(key); index+=2; continue
                return False
            return "--run" in seen and "--write" in seen
    if script=="working-tree-gate.sh":
        index=0
        while index < len(args):
            if args[index] in ("--help","-h","--pretty"):
                index+=1; continue
            if args[index]=="--root" and index+1 < len(args):
                candidate=args[index+1] if os.path.isabs(args[index+1]) else os.path.join(root,args[index+1])
                if os.path.realpath(candidate)!=os.path.realpath(root): return False
                index+=2; continue
            if args[index]=="--max-paths" and index+1 < len(args) and re.fullmatch(r"[1-9][0-9]?",args[index+1]):
                index+=2; continue
            return False
        return True
    return False

def allowed_pre_intake_shell(text):
    segments=shell_segments(text)
    return segments is not None and all(read_only_shell(segment) or allowed_setup_command(segment) for segment in segments)

def allowed_scope_test(text):
    try: tokens=shlex.split(text,posix=True)
    except ValueError: return False
    return tokens==["python3","-m","unittest","discover","-s","tests","-v"]

def allowed_scope_research_shell(text):
    segments=shell_segments(text)
    return segments is not None and all(read_only_shell(segment) or allowed_scope_test(segment) or allowed_setup_command(segment,allow_scope_research=True) for segment in segments)

def safe_run_artifact_path(value):
    if not isinstance(value,str) or not value.strip() or "\0" in value: return False
    candidate=value.strip()
    if not os.path.isabs(candidate): candidate=os.path.join(root,candidate)
    candidate=os.path.normpath(candidate)
    try:
        if os.path.commonpath((candidate,run_dir))!=run_dir or candidate==run_dir: return False
    except ValueError: return False
    if any(name in os.path.basename(candidate) for name in protected): return False
    parent=os.path.dirname(candidate)
    if os.path.realpath(parent)!=os.path.realpath(os.path.join(expected_real_run,os.path.relpath(parent,run_dir))): return False
    if os.path.lexists(candidate):
        info=os.lstat(candidate)
        return stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not stat.S_ISLNK(info.st_mode)
    return True

def patch_paths(value):
    if not isinstance(value,str): return []
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$",value,flags=re.M)

def exact_request_path(value):
    if not isinstance(value,str) or not value.strip() or "\0" in value: return False
    candidate=value.strip()
    if not os.path.isabs(candidate): candidate=os.path.join(root,candidate)
    candidate=os.path.normpath(candidate)
    expected=os.path.join(run_dir,expected_request)
    expected_real_candidate=os.path.join(os.path.realpath(root),os.path.relpath(candidate,root))
    if candidate!=expected or os.path.realpath(os.path.dirname(candidate))!=expected_real_run:
        return False
    if os.path.lexists(candidate):
        info=os.lstat(candidate)
        return stat.S_ISREG(info.st_mode) and info.st_nlink==1 and os.path.realpath(candidate)==expected_real_candidate
    return True

if tool == "Bash" or not tool and command:
    if required_ok:
        if mutation_mentions_protected(command) and not read_only_shell(command): deny("authority files may be changed only by Kimiflow gate commands")
        raise SystemExit(0)
    checked_command=trusted_command(command)
    if checked_command is None: deny("complete the bounded Product Intake before planning or project mutation")
    if mutation_mentions_protected(checked_command) and not read_only_shell(checked_command): deny("authority files may be changed only by Kimiflow gate commands")
    if not required_ok:
        allowed_shell=allowed_scope_research_shell(checked_command) if scope_drafting else allowed_pre_intake_shell(checked_command)
        if not allowed_shell:
            deny("complete the bounded Product Intake before planning or project mutation")
    raise SystemExit(0)

if tool in ("AskUserQuestion","request_user_input"):
    if not required_ok and pending_round not in (1,2): deny("register the intake request with active-run await-user first")
    raise SystemExit(0)

if tool in ("apply_patch","Edit","Write"):
    payload=ti.get("patch") or ti.get("input") or ti.get("content") or (ti.get("command") if tool == "apply_patch" else "") or (data.get("command") if tool == "apply_patch" else "") or ""
    path=ti.get("file_path") or ti.get("path") or ""
    combined="%s\n%s"%(path,payload)
    if mutation_mentions_protected(combined): deny("authority files may be changed only by Kimiflow gate commands")
    if not required_ok:
        paths=patch_paths(payload) if tool == "apply_patch" else ([path] if path else [])
        scope_artifacts=scope_drafting and bool(paths) and all(safe_run_artifact_path(item) for item in paths)
        if not scope_artifacts and (len(paths)!=1 or not exact_request_path(paths[0])):
            deny("only the exact pending intake request artifact may be written before the response")
    raise SystemExit(0)

if not required_ok and tool in ("update_plan","TaskCreate","TaskUpdate","EnterPlanMode","ExitPlanMode"):
    deny("implementation planning starts only after the Product Intake response")
' "$@"
