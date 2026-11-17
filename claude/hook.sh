# Starts a Kimiflow hook (session-start or git-guard) with a Python 3.9+ that does not depend on
# the host's PATH: an app started from the Dock, such as Claude Desktop, can hand hooks a shorter
# PATH than a terminal. Without a usable Python, an initialized project hears about it instead of
# silently losing its session context and git guard.
# KIMIFLOW_PYTHON_FALLBACKS replaces the fixed fallback list (tests use it).

usable() {
    [ -n "$1" ] && [ -x "$1" ] || return 1
    if [ "$1" = /usr/bin/python3 ] && [ "$(/usr/bin/uname -s 2>/dev/null)" = Darwin ]; then
        # Without the Command Line Tools this macOS stub opens an install dialog instead of running.
        /usr/bin/xcode-select -p >/dev/null 2>&1 || return 1
    fi
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1 </dev/null
}

for candidate in "$(command -v python3 2>/dev/null)" \
        ${KIMIFLOW_PYTHON_FALLBACKS-/opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3}; do
    if usable "$candidate"; then
        exec "$candidate" "${0%/*}/../scripts/kimiflow_ops.py" "$@"
    fi
done

# No usable Python, so no JSON parser and maybe no git (on macOS without the Command Line Tools
# /usr/bin/git is the same install stub): plain sh only, no external commands.
mode=$1
blanks=$(printf ' \t\nx'); blanks=${blanks%x}  # the default IFS, set explicitly
set -f  # no glob expansion in the word splits below
input=
while IFS= read -r line || [ -n "$line" ]; do input="$input$line
"; done

# Find the string values of "cwd" and "command" in the hook JSON in one linear pass without building
# strings (appending to a growing variable is quadratic in bash): split at every quote; a field that
# ends in an odd number of backslashes stopped at an escaped quote, so its string goes on. A
# one-field string followed by nothing but a colon is a key; the next string is its value. Words in a
# description never count, and a key inside a string value cannot match: its quotes are escaped there.
i=0 instring= single= last= value_of= start= cwd_from= cwd_to= command_from= command_to=
IFS='"'; set -- $input; IFS=$blanks
for field do
    i=$((i + 1))
    if [ -z "$instring" ]; then
        case $field in
            *:*:* | *[!:[:space:]]*) value_of= ;;
            *:*) value_of=$last ;;
            *) value_of= ;;
        esac
        instring=1 single=1 start=$((i + 1))
        continue
    fi
    case $field in
        *\\)
            # Ends in backslashes: an odd run escapes the quote. Counted by splitting at backslashes, in
            # place: shell patterns on a long string (${x%?} in a loop, ${x##*[!\\]}) are quadratic, and a
            # function call copies this loop's thousands of positional parameters in bash.
            run=0 text=
            IFS='\'
            for piece in $field; do if [ -n "$piece" ]; then run=0 text=1; else run=$((run + 1)); fi; done
            IFS=$blanks
            [ -z "$text" ] || run=$((run + 1))
            if [ $((run % 2)) -eq 1 ]; then single=; continue; fi
            ;;
    esac
    case $value_of in
        cwd) [ -n "$cwd_from" ] || cwd_from=$start cwd_to=$i ;;
        command) [ -n "$command_from" ] || command_from=$start command_to=$i ;;
    esac
    if [ -n "$single" ]; then last=$field; else last=; fi
    instring= value_of=
done

# print_value <from> <to>: the raw JSON string made of fields <from>..<to>, unescaped. \t, \n and \r
# become a blank (only Git words matter here); \uXXXX keeps uXXXX. Prints a trailing x, so command
# substitution keeps trailing blanks.
print_value() {
    from=$1 to=$2 i=0
    IFS='"'; set -- $input
    for field do
        i=$((i + 1))
        [ "$i" -lt "$from" ] && continue
        [ "$i" -gt "$to" ] && break
        [ "$i" -gt "$from" ] && printf '"'
        printf '%s' "$field"
    done | {
        IFS= read -r raw
        IFS='\'; set -- $raw
        printf '%s' "${1-}"; [ $# -eq 0 ] || shift
        literal=
        for field do
            if [ -n "$literal" ]; then literal=; printf '%s' "$field"; continue; fi
            case $field in
                '') printf '\\'; literal=1 ;;
                [tnr]*) printf ' %s' "${field#?}" ;;
                *) printf '%s' "$field" ;;
            esac
        done
    }
    printf x
}

# Speak only where the Python gate would: the main checkout of the payload's cwd (also from any
# linked worktree) has .kimiflow/ops/OPERATIONS.md and the local exclude entry init writes. Whether
# .kimiflow is tracked needs git, so that last check is left to the Python gate.
dir=$PWD
if [ -n "$cwd_from" ]; then value=$(print_value "$cwd_from" "$cwd_to"); value=${value%x}; [ -z "$value" ] || dir=$value; fi
case $dir in /*) ;; *) dir=$PWD ;; esac  # Claude Code sends an absolute cwd
cd -P -- "$dir" 2>/dev/null && dir=$(pwd -P)  # resolve symlinks, as the Python gate does
while [ ! -e "$dir/.git" ]; do
    [ -z "$dir" ] || [ "$dir" = / ] && exit 0
    dir=${dir%/*}
done
main=$dir
if [ -f "$dir/.git" ]; then
    IFS= read -r gitdir < "$dir/.git" || [ -n "$gitdir" ] || exit 0
    gitdir=${gitdir#gitdir: }
    case $gitdir in /*) ;; *) gitdir=$dir/$gitdir ;; esac
    case $gitdir in */.git/worktrees/*) main=${gitdir%/.git/worktrees/*} ;; *) exit 0 ;; esac
fi
[ -f "$main/.kimiflow/ops/OPERATIONS.md" ] || exit 0
exclude=$main/.git/info/exclude excluded=
[ -f "$exclude" ] || exit 0
while IFS= read -r line || [ -n "$line" ]; do
    [ "$line" = .kimiflow/ ] && excluded=1
done < "$exclude"
[ -n "$excluded" ] || exit 0

problem='Kimiflow found no Python 3.9+ (checked PATH, /opt/homebrew/bin, /usr/local/bin, /usr/bin)'
case $mode in
    session-start)
        printf '{"systemMessage": "%s: the session context and the git guard are off. Install Python 3 (for example the Xcode Command Line Tools or Homebrew) and start a new session.", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s, so the Kimiflow session context and git guard are not running. Read .kimiflow/ops/OPERATIONS.md and HANDOFF.md yourself and tell the owner that Python 3 is missing."}}\n' \
            "$problem" "$problem"
        ;;
    git-guard)
        # Only the command counts, never its description. Without the key, check everything.
        if [ -n "$command_from" ]; then value=$(print_value "$command_from" "$command_to"); value=${value%x}
        else value=$input; fi
        set -- $value  # tabs, newlines and runs of blanks become one blank
        command=" $*"
        case $command in
            *'git add'* | *'git stage'* | *'git commit'* | *'git -'*)
                printf '%s, so the git guard cannot check this command. Ask the owner to install Python 3 or to run it themselves.\n' \
                    "$problem" >&2
                exit 2
                ;;
        esac
        ;;
esac
exit 0
