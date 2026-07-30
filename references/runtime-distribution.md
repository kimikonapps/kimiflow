# Kimiflow runtime distribution contract v1

Kimiflow has one canonical source and one runtime release. Embedded Codex/Claude users and external app hosts
consume the same candidate; KimiTalk or another host does not fork workflow, gates, memory, or versioning.

## Release assets

Every official release carries:

- `kimiflow-runtime-<version>.zip`: deterministic, stored ZIP rooted at `kimiflow/`;
- `kimiflow-update-v1.json`: version, pinned source commit, artifact digest, protocol range, host profiles, and
  the fixed official GitHub release origin.

The ZIP is derived only from `plugins/kimiflow`, whose allowlisted inventory is already bound by
`RUNTIME-FINGERPRINT.json`. ZIP directory/executable modes are canonical (`0755`), all other files are `0644`,
timestamps are fixed, and entries are sorted. Checkout umask therefore cannot alter release bytes.

## Trust and compatibility

`build-runtime-release.sh verify` is deliberately offline. It can prove `artifact_verified`, or
`draft_verified` when given explicit draft release metadata. Caller-supplied JSON can never produce an official
or compatible verdict.

`verify-published` alone can produce `published_verified` or a host compatibility verdict. It fetches the exact
official GitHub API release and tag-commit endpoints over TLS, rejects changed origins/paths, requires an
immutable non-draft release, and binds both assets to the pinned source commit.

Host profiles are named policy:

- `contracts.features` advertises the adapter feature names understood by this runtime;
- `embedded`: no optional adapter feature is required;
- `app_host`: adapter protocol v1 plus `workflow_context`, `model_roles`, `structured_events`, and
  `root_confinement`.
- `pi`: adapter protocol v1 plus `workflow_context` and `structured_events`. Pi 0.82.1 and 0.83.0 do not provide a
  host-enforced project sandbox, so this profile deliberately does not advertise `root_confinement`; callers
  must treat Pi's file and shell permissions as host policy.

Hosts install only after `compatible`; missing protocol/features return the explicit non-error-domain verdict
`incompatible` (exit 3). Invalid origin, metadata, archive, digest, tag, or source identity fails verification.

For the optional Pi host, extract the verified runtime ZIP and install its rooted package directory with
`pi install /absolute/path/to/kimiflow` (or `pi install -l ...` for project-local scope). The same command may
target a trusted source checkout. Pi then loads the extensions and skill declared by that package's
`package.json`; Kimiflow does not install or start Pi itself.

## Maintainer flow

The tracked release skill regenerates and stages the candidate before release gates. The release commit is
tagged and pushed first. `publish-runtime-release.sh` then exports the pinned commit with replacement refs
disabled, rebuilds in isolation, creates/uploads a draft, verifies it, publishes it, and performs fixed-origin
verification. Repository immutable releases must be enabled. A tag/publish race cannot be made atomic through
GitHub's API; any final mismatch is terminal and no compliant host may activate that release.

This distribution layer is additive. Normal marketplace installation remains supported and does not require an
app host, provider account, daemon, duplicated memory store, or second Kimiflow codebase.
