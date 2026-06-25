# Codebase

Diese Datei ist eine publish-safe Orientierung fuer die Kimiflow-Codebase. Konkrete Analyse-Findings und
Verbesserungs-Backlogs liegen lokal unter `.kimiflow/project/` und werden nicht als Repo-Doku publiziert.

## Top-Level-Struktur

| Pfad | Rolle |
|---|---|
| `SKILL.md` | Canonical Orchestrator fuer Claude Code: Modi, Scope-Gate, Project Map und Phasen 0-7. |
| `reference.md` | Ausfuehrliche Regeln fuer Interviews, Recherche, Review, Verification, Commit-Hygiene und Repo-Doku. |
| `.claude-plugin/` | Claude-Code-Manifest und Marketplace-Metadaten. |
| `.codex-plugin/` | Codex-Plugin-Manifest mit Interface-Texten und Skill-Verweis. |
| `.agents/plugins/` | Repo-lokaler Codex-Marketplace-Eintrag. |
| `skills/kimiflow/` | Codex-Adapter-Skill und Agent-Metadaten. |
| `hooks/` | Gemeinsame Shell-Skripte fuer Gates, Installer, Scanner und Tests. |
| `docs/` | Publish-safe Repo-Doku, Demo-Material und Design-Kontext. |
| `examples/` | Beispielhafte Kimiflow-Laeufe. |
| `evals/` | Behavioural Evals fuer Release-Kalibrierung ausserhalb der CI. |

## Einstiegspunkte

- Claude Code: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `SKILL.md`,
  `hooks/hooks.json`.
- Codex: `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`, `skills/kimiflow/SKILL.md`,
  `skills/kimiflow/agents/openai.yaml`, `hooks/install-codex-hooks.sh`.
- Shared runtime: `hooks/commit-secret-gate.sh`, `hooks/state-gate.sh`, `hooks/test-gate.sh`,
  `hooks/resolve-review-gate.sh`, `hooks/project-map-status.sh`, `hooks/memory-router.sh`.

## Wo Aenderungen typischerweise landen

| Vorhaben | Primaere Dateien |
|---|---|
| Workflow oder Phasenlogik aendern | `SKILL.md`, `reference.md` |
| Project Intelligence erweitern | `reference.md`, `hooks/project-map-status.sh`, `hooks/test-project-map-status.sh` |
| Memory/Recall/Learning Loop erweitern | `reference.md`, `hooks/memory-router.sh`, `hooks/test-memory-router.sh`, `hooks/launcher-status.sh` |
| Codex-Plugin-Darstellung verbessern | `.codex-plugin/plugin.json`, `skills/kimiflow/agents/openai.yaml`, README-Codex-Abschnitt |
| Claude-Plugin-Darstellung verbessern | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, README-Claude-Abschnitt |
| Hook-Verhalten aendern | Passendes `hooks/*.sh`, passendes `hooks/test-*.sh`, Smoke-Tests |
| Release-Doku aktualisieren | `CHANGELOG.md`, `COMPATIBILITY.md`, `README.md` |

## Lokale Artefakte

Kimiflow legt Arbeitszustand und Projektkarten unter `.kimiflow/` ab. Dieses Verzeichnis ist absichtlich in
`.gitignore`, weil es lokale Analyse, Findings, Workqueues und projektspezifische Memory enthaelt. Wenn
Informationen ins Repo gehoeren, werden sie kuratiert in `docs/` oder README/CHANGELOG/COMPATIBILITY
uebernommen.

## Repo-Doku-Regel

Oeffentliche Repo-Doku darf Architektur, Bedienung, Teststrategie und stabile Designentscheidungen erklaeren.
Sie soll keine ungepruefte Fehlerliste, internen Analyse-Backlog oder sensible Schwachstellenbeschreibung
enthalten. Solche Punkte bleiben lokal und werden ueber separate Fix-Laeufe abgearbeitet.
