# List — <project>

The one list of work. Search it by ID or status instead of reading it whole. Done items move to
LIST-done.md with date and commit.

Line format: ``- `ID` [STATUS] Title — details``

Statuses: NEXT · OPEN · IN PROGRESS · DONE-PENDING (built, not on main) · LATER (deferred, with
trigger) · DONE · IDEA · INFO

Owner items check (before every test build, main merge and handoff):
`grep -nE '\[(NEXT|OPEN|IN PROGRESS)\].*[Oo]wner' LIST.md`

## 0 State

- `S1` [INFO] <one line on where the project stands>

## 1 Next

- `<ID>` [NEXT] <title> — <details>

## 2 Open

- `<ID>` [OPEN] <title> — <details; "owner" when the owner must decide or act>

## 3 Deferred

- `<ID>` [LATER] <title> — <why deferred> — trigger: <when to pick it up>

## 4 Backlog

- `<ID>` [IDEA] <title> — <details>
