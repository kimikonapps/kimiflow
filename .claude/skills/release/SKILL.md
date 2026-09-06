---
name: release
description: Publish an explicitly requested release of the minimal Kimiflow plugin using normal Git and GitHub tools.
---

# Kimiflow release

Publish only when the user requests it. Preserve unrelated changes; use a clean checkout of the
reviewed commit when the working tree contains other work.

1. Choose the requested version, or the next appropriate version for the change. Keep the Claude,
   Codex and Pi manifests and the existing repository marketplace version consistent. Promote the
   changelog entry and document any migration boundary.
2. Run `python3 scripts/build_plugin.py`, `python3 -m unittest discover -s tests -v`,
   `python3 scripts/build_plugin.py --check` and `git diff --check`. Inspect the named release diff.
3. Commit only the reviewed release inputs and generated package. Push the tested commit to main and
   require green GitHub CI before tagging/publishing. Never rewrite a published tag or release.
4. Use the available GitHub CLI to publish the annotated version tag with changelog notes. If a
   downloadable plugin ZIP is needed, export only `plugins/kimiflow/` from that exact commit and
   provide its SHA-256 digest. Verify the published tag and any attached bytes against the local export.

The old managed `kimiflow-update-v1`/adapter contracts do not apply to 0.5. Do not advertise their
compatibility or add a project-release engine to publish this small plugin.
