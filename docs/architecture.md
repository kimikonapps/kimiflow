# Architektur

Kimiflow ist eine Prompt-/Shell-Hybrid-Engine fuer explizit gestartete Feature- und Bugfix-Laeufe. Die
Kernidee: Das Modell fuehrt den Workflow, aber kritische Gates werden durch wiederverwendbare Shell-Skripte
und persistente Artefakte geerdet.

## Schichten

| Schicht | Dateien | Aufgabe |
|---|---|---|
| Canonical Engine | `SKILL.md`, `reference.md` | Definiert Modi, Phasen, Scope-Regeln, Project Map, Review- und Commit-Kontrakt. |
| Host Packaging | `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/` | Macht dieselbe Engine fuer Claude Code und Codex installierbar und sichtbar. |
| Mechanical Layer | `hooks/*.sh`, `hooks.json`, `hooks/hooks.json` | Implementiert Gate-Resolver, Host-Hooks, Installer und strukturelle Checks. |
| Project Intelligence | `.kimiflow/project/`, `hooks/project-map-status.sh`, `hooks/memory-router.sh` | Baut lokale Projektkarten, erkennt Staleness, routet bounded Memory/Recall und trennt lokale Analyse von Repo-Doku. |
| Validation & Docs | `.github/workflows/ci.yml`, `docs/`, `examples/`, `evals/` | Verifiziert Packaging, Hooks und Verhalten; erklaert die Nutzung publish-safe. |

## Kontrollfluss

```text
User request
  -> /kimiflow in Claude Code oder $kimiflow in Codex
  -> canonical workflow aus SKILL.md
  -> Detailregeln aus reference.md
  -> mechanische Resolver/Hooks fuer Gates
  -> Artefakte unter .kimiflow/<slug>/ oder .kimiflow/project/
  -> Commit-Gate stoppt fuer explizites OK
```

Claude Code nutzt den Root-Skill und plugin-bundled Hooks. Codex nutzt einen Adapter-Skill unter
`skills/kimiflow/` und stabile Hook-Wrapper, die per `hooks/install-codex-hooks.sh` in das lokale Codex-Home
geschrieben werden.

## Wichtige Invarianten

- Kimiflow ist opt-in: Es startet nur, wenn der User Kimiflow explizit anfordert.
- Gate-Entscheidungen duerfen nicht nur behauptet werden; Resolver-Skripte liefern die mechanische Wahrheit,
  wo das moeglich ist.
- Normale Laeufe persistieren State unter `.kimiflow/<slug>/`.
- Project Intelligence persistiert lokale Projektkarten und bounded Memory unter `.kimiflow/project/`.
- Repo-Doku ist ein kuratierter Publishing-Layer. Lokale Findings und sensible Arbeitsnotizen bleiben in
  `.kimiflow/project/` und werden nicht automatisch committed.

## Aenderungsachsen

- Workflow-Aenderungen beginnen in `SKILL.md`; Detailregeln gehoeren in `reference.md`.
- Claude-spezifisches Packaging liegt in `.claude-plugin/` und `hooks/hooks.json`.
- Codex-spezifisches Packaging liegt in `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/` und
  `hooks/install-codex-hooks.sh`.
- Hook-Verhalten braucht in der Regel ein passendes `hooks/test-*.sh` und Smoke-Coverage.
- Project-Map-Verhalten braucht Updates in `reference.md`, `hooks/project-map-status.sh` und
  `hooks/test-project-map-status.sh`.
- Memory-/Learning-Verhalten braucht Updates in `reference.md`, `hooks/memory-router.sh`,
  `hooks/test-memory-router.sh` und den Launcher-Smoke-Checks.
