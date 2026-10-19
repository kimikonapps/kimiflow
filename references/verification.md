# Verification

Use existing project tests and CI. Reproduce bugs, verify the changed behavior and relevant regressions,
and inspect the diff against acceptance. Use independent review when a concrete risk warrants it and
the host supports it; there is no fixed reviewer count. After repeated failure, change diagnosis or
approach instead of repeating the same assumption. Report an unresolved blocker honestly.

If existing tools already provide adequate verification, use them directly. Only when useful, the
installed `scripts/check_change.py` can run explicit argv checks and detect source differences between
snapshots before and after each check:

```bash
python3 /absolute/plugin/root/scripts/check_change.py --root /absolute/project --check '["npm","test"]'
```

Repeat `--check` for multiple checks. It writes no state and makes no commit. This is not continuous
monitoring or a security sandbox. Ignored build output and untracked `.kimiflow/` notes are excluded.
On POSIX it rejects and stops surviving members of the check's process group; detached processes
outside that group and changes restored between snapshots are outside its observation.

Deliver the result with actual checks and remaining limitations. Respect the project's commit policy;
when committing, include only intended paths and preserve foreign staging. Publication and irreversible
actions require their own authority. Completion waits only on the gates the project uses: in an
initialized project, those named in [the operating model](operating-model.md).
