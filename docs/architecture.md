# Architektur

Kimiflow ist eine Prompt-/Shell-Hybrid-Engine fuer explizit gestartete Laeufe und automatisch geroutete,
autorisierte substanzielle Feature-Arbeit. Normale Fixes und kleine risikoarme Arbeiten bleiben direkt. Die
Kernidee: Das Modell fuehrt den Workflow, aber kritische Gates werden durch wiederverwendbare Shell-Skripte
und persistente Artefakte geerdet.

## Schichten

| Schicht | Dateien | Aufgabe |
|---|---|---|
| Canonical Engine | `docs/render/kimiflow/`, `phases/`, `reference.md`, `docs/kimiflow-scaling-knobs.md` | Definiert den duennen Always-loaded Driver, on-demand Phasenregeln, Scope-Regeln, Project Map, Review- und Commit-Kontrakt und rendert die Host-Skills. |
| Host Packaging | `SKILL.md`, `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/` | Macht dieselbe Engine fuer Claude Code und Codex installierbar und sichtbar. |
| Mechanical Layer | `hooks/*.sh`, `hooks.json`, `hooks/hooks.json` | Implementiert Gate-Resolver, Host-Hooks, Installer und strukturelle Checks. |
| Project Intelligence | `.kimiflow/project/`, `hooks/project-map-status.sh`, `hooks/memory-router.sh` | Baut lokale Projektkarten, erkennt Staleness, routet bounded Memory/Recall und trennt lokale Analyse von Repo-Doku. |
| Validation & Docs | `.github/workflows/ci.yml`, `docs/`, `examples/`, `evals/` | Verifiziert Packaging, Hooks und Verhalten; erklaert die Nutzung publish-safe. |

## Kontrollfluss

```text
User request
  -> /kimiflow in Claude Code oder $kimiflow in Codex
  -> canonical workflow aus SKILL.md
  -> Phasendetails aus phases/*.md plus Detailregeln aus reference.md / docs/
  -> mechanische Resolver/Hooks fuer Gates
  -> Artefakte unter .kimiflow/<slug>/ oder .kimiflow/project/
  -> lokaler atomarer Commit der verifizierten, lauf-eigenen Pfade
```

Claude Code nutzt den gerenderten Root-Skill und plugin-bundled Hooks. Codex nutzt einen gerenderten
Adapter-Skill unter `skills/kimiflow/` und stabile Hook-Wrapper, die per `hooks/install-codex-hooks.sh` in
das lokale Codex-Home geschrieben werden. Beide Skill-Dateien bleiben committed, werden aber aus
`docs/render/kimiflow/` materialisiert:

```bash
PYTHONPATH="$PWD/hooks" python3 -m kimiflow_core.render
```

`hooks/release-consistency-check.sh` rendert vor dem Release per `--check` und faellt bei Drift in
`SKILL.md` oder `skills/kimiflow/SKILL.md` fehl, ohne lokale Drift zu ueberschreiben. Derselbe Check haelt
Byte-Budgets fuer die immer geladene Prosa (`SKILL.md` <= 17,000 Bytes, Codex-Skill <= 15,000 Bytes), fuer Phase-Dateien
(`phases/*.md` jeweils <= 20,000 Bytes) und fuer die Launcher-Default-Ausgabe (JSON <= 8,000 Bytes,
Pretty <= 12,000 Bytes auf einem sauberen Fixture-Repo).

## Wichtige Invarianten

- Explizites Kimiflow startet den Flow, explizites direkt/direkt umgeht ihn; autorisierte substanzielle
  Feature-Arbeit mit materiellem Integrations-, Datenfluss-, Security-, API-, Architektur- oder Discovery-Risiko
  wird automatisch geroutet. Normale Fixes, Reviews, Doku/Config und kleine risikoarme Features bleiben direkt.
- Gate-Entscheidungen duerfen nicht nur behauptet werden; Resolver-Skripte liefern die mechanische Wahrheit,
  wo das moeglich ist.
- Normale Laeufe persistieren State unter `.kimiflow/<slug>/`.
- Project Intelligence persistiert lokale Projektkarten und bounded Memory unter `.kimiflow/project/`.
- Repo-Doku ist ein kuratierter Publishing-Layer. Lokale Findings und sensible Arbeitsnotizen bleiben in
  `.kimiflow/project/` und werden nicht automatisch committed.

## Aenderungsachsen

- Always-loaded Workflow-Aenderungen beginnen in `docs/render/kimiflow/canonical/SKILL.md`; danach wird
  `SKILL.md` gerendert. Phasendetails gehoeren in `phases/*.md`, Skalierungsdetails in
  `docs/kimiflow-scaling-knobs.md`, und breite Referenz-/Maintainerregeln in `reference.md` oder `docs/`.
- Claude-spezifisches Packaging liegt in `.claude-plugin/` und `hooks/hooks.json`.
- Codex-spezifisches Packaging liegt in `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/`,
  `docs/render/kimiflow/overlays/codex.md` und `hooks/install-codex-hooks.sh`.
- Hook-Verhalten braucht in der Regel ein passendes `hooks/test-*.sh` und Smoke-Coverage.
- Project-Map-Verhalten braucht Updates in `reference.md`, `hooks/project-map-status.sh` und
  `hooks/test-project-map-status.sh`.
- Memory-/Learning-Verhalten braucht Updates in `reference.md`, `hooks/memory-router.sh`,
  `hooks/test-memory-router.sh` und den Launcher-Smoke-Checks.
