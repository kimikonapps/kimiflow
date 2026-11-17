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

# No usable Python. Speak only in a project with a kit (also from a subfolder or a worktree under it).
dir=$PWD
while [ ! -d "$dir/.kimiflow/ops" ]; do
    [ -z "$dir" ] || [ "$dir" = / ] && exit 0
    dir=${dir%/*}
done

problem='Kimiflow found no Python 3.9+ (checked PATH, /opt/homebrew/bin, /usr/local/bin, /usr/bin)'
case $1 in
    session-start)
        printf '{"systemMessage": "%s: the session context and the git guard are off. Install Python 3 (for example the Xcode Command Line Tools or Homebrew) and start a new session.", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s, so the Kimiflow session context and git guard are not running. Read .kimiflow/ops/OPERATIONS.md and HANDOFF.md yourself and tell the owner that Python 3 is missing."}}\n' \
            "$problem" "$problem"
        ;;
    git-guard)
        input=
        while IFS= read -r line || [ -n "$line" ]; do input="$input$line"; done
        case $input in
            *'git add'* | *'git commit'* | *'git -'*)
                printf '%s, so the git guard cannot check this command. Ask the owner to install Python 3 or to run it themselves.\n' \
                    "$problem" >&2
                exit 2
                ;;
        esac
        ;;
esac
exit 0
