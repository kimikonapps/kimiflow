# Optional Kimiflow tools

These tools are not prerequisites for ordinary feature/fix delivery. Use only the requested entry, with the installed plugin root. Existing data and pinned legacy runs remain intact.

| Request | Read or invoke |
|---|---|
| Resume a run whose STATE.md contains Flow schema | Read `references/legacy-workflow.md`; on Codex also `references/legacy-codex.md`. Run `hooks/active-run.sh status` once, then follow its pinned phase/ownership contract. |
| Headless `kimiflow run` or managed FirstMate crew | These controllers consume the legacy state machine. Read the same legacy workflow before initializing/resuming their run. The managed Pi crew instructions are in `references/legacy-pi.md`. |
| Project release | Read only `reference.md` → `Project Release Profile` (including v2). Publication still requires the release request. |
| `--project-map <quick|skip>` or project-map refresh | Read only `reference.md` → `Project Map Bootstrap`; use `hooks/project-map-status.sh`. Skip is a no-op. |
| Prior fix or project learning | Query `hooks/memory-router.sh` for the specific subject using `reference.md` → `Memory recall`. Do not run broad recall, Vault Pulse or learning promotion just because a task ended. |
| Vault setup or export | Read only `reference.md` → `Vault conventions`; preserve its privacy/export boundary. |
| Fleet integration or recovery | Read only `reference.md` → `Workspace preflight` and use `hooks/workspace-preflight.sh`. Never replace managed integration with an unverified merge. |
| Local security scan | Read only `reference.md` → `Local actionable security`; the output remains private/advisory. |
| Explicit bounded Solution Search | Read only `reference.md` → `Bounded Solution Search`; off otherwise. |

Legacy runtime contracts remain for compatibility. Their mechanics are not a prerequisite for the lean loop. Model/effort selection always follows the user's host selection; do not infer permission to launch a paid service from an installed CLI.
