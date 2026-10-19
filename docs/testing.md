# Testing

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_plugin.py --check
git diff --check
```

CI runs the same suite on Linux/Python 3.9 and macOS/Python 3.14. Hooks and process-group cleanup
are tested on these POSIX systems only.

- `tests/test_ops.py`: main-checkout resolution (subfolder, worktree; silent for non-Git, submodule
  and bare), `init` (second run changes nothing, owner edits kept, exclude effective in a worktree,
  refusal of tracked kit files, symlinks leading outside and a legacy `ops` task, legacy runs only
  reported), `update` (report without writing, `--apply-safe`, `--resolved`, symlinks), the
  session-start hook (absolute paths from a worktree, trust gate, version notice, commits since
  HANDOFF, size notices for OPERATIONS.md > 12 KB and HANDOFF.md > 6 KB) and the git guard (blocks only in an initialized project, allows named paths and plain
  commits). The built helper runs from the package without an exec bit.
- `tests/test_check_change.py`: the check helper, including failing commands, source/index drift,
  ignored output, timeouts and surviving children.
- `tests/test_package.py`: exact package contents, source/render parity, version consistency, unsafe
  output/source paths, hooks and agents only in the Claude manifest, agents without a model, no
  model-excluding wording and no project-specific terms in shipped texts.

A passing test suite says nothing about whether the kit or the skill improves model outcomes; use the
separate paired-task protocol in `evals/README.md` for that question. Historical coverage of deleted
engines belongs to the matching development commit (see MIGRATION.md).
