# Design: kimiflow — Audit/Cleanup-Modus (ponytail lens)

- **Datum:** 2026-06-22
- **Status:** Design freigegeben (brainstorming) → bereit für `writing-plans`
- **Sprache:** Prosa Deutsch (Review-Sprache des Users); Code-Identifier/Pfade/Section-Namen Englisch.
- **Abhängigkeit:** baut auf **A1 (Pre-Build-Summary-Gate)** + **A3 (caller-verified deletion gate)** aus `2026-06-22-kimiflow-prebuild-gate-and-phase-tasklist-design.md` auf. Diese sollten zuerst umgesetzt sein (Audit-Modus nutzt beide wieder).
- **Recherche-Grundlage:** Vault → `Dead-Code-Audit — Scope & Safety für KI-Agenten (ponytail-Lens) 2026-06`.

## Problem / Ziel

kimiflow hat **feature-mode** und **fix-mode**, aber keinen Modus, um **bestehenden over-engineered/toten Code sicher zu entschlacken**. Ziel: ein **dritter Modus** (Audit/Cleanup) nach der „ponytail lens" — **staged**: finden → Report → Freigabe → ausführen — der die kimiflow-Engine (Gates, Subagenten, atomare Commits) wiederverwendet und **die Engine nicht ändert** (reine neue Ausführungs-Linie, keine Änderung an feature/fix oder den Qualitäts-Gates).

## Entscheidungen (im Brainstorming festgelegt)

- **Voller dritter Modus, phasen-gemappt** — Audit verzweigt die Phasen 1–7 (analog wie fix die Phasen 1–2 verzweigt). Maximaler Reuse, on-brand. (Alternativen „leichtes Sub-Flow" / „nur Rezept" verworfen.)
- **Staged** (find → Report → Freigabe → execute) — nutzt A1 + A3 maximal.
- **Trigger:** `/kimiflow --audit <pfad/modul>` (parallel zu `--fix`) + Auto-Detect aus dem Request („aufräumen", „dead code", „entschlacken", „over-engineering"). **Ziel-Pfad/Modul erforderlich.**
- **Scope:** bounded Target für die *Analyse*; **Caller-Greps laufen repo-weit** (Cross-Deps); Whole-Repo = mehrere gezielte Läufe. (Recherche: Analyse-Scope ≠ Ausführungs-Einheit; Agenten sind bei Whole-Repo context-limit-fehleranfällig.)
- **Safety-Verfeinerungen (aus der Recherche, verpflichtend):** caller-grep **repo-weit** · **Git-History-Freshness** als Konfidenz-Filter (zero-caller + kürzlich angefasst = WIP, nicht tot) · caller-grep = dokumentiertes **MINIMUM** (dynamische/reflektive Refs Blind Spot) → Tests-grün + do-NOT-touch + adversariale „dead"-Claim-Verifikation als Backstop · konservativ (bei Zweifel downgrade/skip, nie auf Annahme löschen).
- **Tag-Taxonomie:** `yagni` (spekulative Architektur) · `delete` (tot, zero-caller) · `shrink` (dedupe, Verhalten erhalten) · `stdlib` (handgerollt → Standardbibliothek, edge-cases erhalten).

## Design — Phasen-Mapping

| Phase | feature/fix | **Audit-Modus** |
|---|---|---|
| 0 Setup | Routing | Mode-Detect (`--audit` / Auto-Detect); Ziel-Pfad erfassen; Scope ≥ `small`; STATE notiert `mode: audit` + Target |
| 1 Clarify | Intent/Problem | **Audit-Scope**: welche Pfade, wie aggressiv, Behavior-Preserve-Constraints, do-NOT-touch-Hinweise → `AUDIT-INTENT.md` (plain language, bounded) → **gate** „passt der Audit-Umfang?" |
| 2 Understand | verstehen/diagnose | **„Find the fat"**: Audit-Reader (read-only) über das Target; jeder Fund = Tag + `path:line` + Ersatz + **repo-weiter pre-delete-grep** + Git-History-Freshness → `AUDIT.md`, ranked biggest-cut-first. Memory-first/vault gilt weiter (frühere Audits prüfen) |
| 3 Plan | PLAN.md | **Slices = der Plan** (jede self-contained: Findings + Verify-Gate); **do-NOT-touch-Liste** mit Begründung („earns its place") |
| 4 Plan-Gate | Reviewer (binär) | **Adversariale „dead"-Claim-Verifikation**: Reviewer versuchen jede Löschung zu *widerlegen* (lebenden Caller finden, inkl. dynamic/reflection/string-dispatch); eine Löschung überlebt nur ohne Treffer. Binär, fail-closed, blocker-aware anti-oscillation wie sonst. **+ A1-Summary-Gate** zeigt die Slice-Liste zur User-Freigabe |
| 5 Implement | bauen/fixen | **Slices ausführen** (sequenziell): pro Slice caller-grep==0 (A3) verifizieren → Cut (`delete`/`yagni`) bzw. Refactor (`shrink`/`stdlib`) anwenden |
| 6 Verify | gegen Kriterien | **pro Slice**: grep-sweep clean → typecheck/build → Tests grün (Regression). `shrink`/`stdlib`: Tests grün **vor + nach** = Verhalten erhalten. ggf. cold-boot |
| 7 Review/Commit | Review+Commit | **1 Slice = 1 reviewbarer Diff = 1 Commit**; Companion-Edits (Tests, die gelöschten Code referenzieren) in lockstep; Commit-Gate (A1/Phase-7) stoppt für OK |

## AUDIT.md — Slice-Format

Jede Slice ist unabhängig ausführbar:

```
## Slice <n>: <scope-bezeichnung>  (~−<x> Zeilen)
**Scope:** <pfade>
**ponytail lens (why each exists):** je Item: delete | earns-its-place-simplify
**Findings (ranked, biggest cut first):**
| tag | what to cut | replacement | path:line | repo-wide pre-delete grep (muss 0 / erwartet) | freshness |
|-----|-------------|-------------|-----------|-----------------------------------------------|-----------|
| delete | … | — | file:line | `grep -rn "sym(" src tests` → 0 | letzter Caller-Bezug entfernt vor … |
**do-NOT-touch (earns its place):** <symbol> — <Grund, warum es trotz Grep-Verdacht bleibt>
**Verify-Gate:** grep-sweep → typecheck/build → tests grün
**Companion-Edits:** <Tests, die in lockstep angepasst/gelöscht werden>
```

`AUDIT-INTENT.md`: plain-language — Target, Aggressivität, Behavior-Constraints, do-NOT-touch-Hinweise, „was NICHT angefasst wird".

## Reuse (nichts doppelt bauen)

- **A1 (Pre-Build-Summary-Gate):** zeigt die Slice-Liste am Ende von Phase 4 zur Freigabe — der Audit-„Report" IST der Summary-Gate-Inhalt.
- **A3 (caller-verified deletion gate):** der mechanische Kern jeder `delete`-Slice (grep==0 Pflicht).
- **Adversariale Reviewer + binäres fail-closed-Gate + anti-oscillation:** für die „dead"-Claim-Verifikation in Phase 4.
- **Atomare Commits + Commit-Gate:** 1 Slice = 1 Commit.

## Betroffene Dateien (Orientierung)

- **SKILL.md:** Phase 0 (Mode-Routing um `audit` + `--audit`; `argument-hint`), Audit-Branch in Phase 1+2 (analog zur fix-Verzweigung), Phase-4-Notiz (dead-claim-Verifikation), Phase-7-Notiz (slice-commit), Modes-Section.
- **reference.md:** neuer Abschnitt **„Audit mode (ponytail lens)"** (AUDIT.md/Slice-Format, Tag-Taxonomie, do-NOT-touch, Git-History-Freshness, repo-weiter grep, Verify-Gate); Querverweis auf „Code mandate" (A3) + „Review rubric" (adversarial).
- **CHANGELOG.md** (Version später).

## Tests

- Audit-Modus ist überwiegend **orchestrator-behavioral** (kein Skript erzwingt die Lens) — abgedeckt über UAT im Implementierungsplan; A3 (deletion gate) und A1 (resolver) sind bereits im Vorgänger-Batch unit-getestet.
- Optional mechanisierbar (eigene Plan-Entscheidung, Tendenz später): ein kleiner Git-History-Freshness-Helper (`git log`-basiert) mit Unit-Test. v1: als Instruktion (Orchestrator ruft `git log`).

## Risiken & Gegenmaßnahmen

- **Behavioral, nicht mechanisch:** die Lens hängt am Orchestrator-Befolgen → imperative Formulierung + der *mechanische* A3-Grep als harter Kern jeder Löschung.
- **Falsch-„tot":** repo-weiter grep + Git-History-Freshness + adversariale Verifikation + Tests-grün; dynamische/reflektive Refs explizit als Blind-Spot benannt.
- **Über-aggressiv:** konservativer Default, do-NOT-touch-Liste, downgrade/skip bei Zweifel.
- **Scope-Creep zu Whole-Repo:** bounded Target v1; Whole-Repo = mehrere Läufe.

## Out of Scope (v1, YAGNI)

- Whole-Repo-Sweep ohne Target (→ mehrere gezielte Läufe).
- Automatische Slice-Parallelisierung (erst sequenziell; Worktree-Parallelität später via bestehendem Knob).
- Sprach-spezifische AST-Tools (grep-basiert als dokumentiertes MINIMUM).
- Änderung an feature/fix, Qualitäts-Gates, Thresholds oder Engine-Verhalten.

## Lokalisierung

Audit-Report/Summary in der Sprache des Users; Code-Identifier/Pfade/Tags Englisch.
