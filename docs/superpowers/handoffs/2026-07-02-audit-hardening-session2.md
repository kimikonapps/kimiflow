# Handoff — Audit-Hardening: B1–B4 KOMPLETT, B5 (Re-Audit + Restbefunde) offen, Floor-Entscheidung beim User

**Date:** 2026-07-02 (Session 2) · **Repo:** kimiflow · **Branch:** `main` · **HEAD:** `53cc587` (working tree clean, **11 Commits NICHT gepusht** — `2b5c096`…`53cc587`)

Vorgänger-Handoff: `2026-07-02-audit-hardening-session.md` (Programm-Definition, Baseline-Audit, B1/B2-Details).

---

## Auftrag (unverändert)

Repo so härten, dass es in unabhängigen adversarialen Multi-Lens-Audits **null offene BLOCKER/HIGH** hat + strukturelle Schwächen (v. a. Token-Ökonomie) beseitigen. Programm: Baseline-Audit → B1 Prosa → B2 Hooks → B3 Python → B4 Token-Restrukturierung → B5 Re-Audit. **B1–B4 sind fertig.**

**Prozessregeln des Users (bindend):** Plan-Audit durch externe Auditor-Agents vor nicht-trivialer Implementierung (binär, BLOCKER/HIGH fixen, Cap ~3); TDD (failing test first) für jeden Fix; keine AI-Attribution in Commits; nur benannte Pfade stagen; Anti-Halluzination (falsches Finding schlimmer als fehlendes); Deutsch, TLDR zuerst.

---

## Was in dieser Session passiert ist (chronologisch, alle Commits einzeln grün)

### `18b910e` — B3: memory_router-Python-Fixes (alle test-first, Repro zuerst ausgeführt)
- **P1 `rows.py` Path-Traversal:** `../../etc/hosts` galt als in-repo, `/etc/hosts` wurde real gehasht (Repro bestätigt). Fix: lexikalisches `os.path.normpath` auf Root+Pfad vor dem Prefix-Check — bewusst KEIN `realpath` (symlinked Roots wie `/tmp` auf macOS würden sonst nie matchen). Escaping-Refs → `OUTSIDE_REPO`.
- **P2 `writes.py`/`store.py` Rewrite-Datenverlust:** Full-Rewrite (status=current) verwarf Nicht-JSON-Zeilen, Append erhielt sie. Neu: `store.read_jsonl_with_lines` → `[(raw, row|None)]`; Rewrite erhält unparsebare Zeilen verbatim in Position; Nicht-Dict-JSON (z. B. `5`) zählt als unparsed (schützt Dedup/Supersession vor `row.get`-Crash). Der alte Test `test_current_rewrite_drops_malformed_lines` wurde bewusst invertiert.
- **P3 Security-Gate-Scope:** Gate scannt jetzt summary+topic+evidence (newline-gejoint — Phrasen-Fenster können Feldgrenzen nicht kreuzen, Guard-Test existiert). Neue `rows.has_secret_value` (AKIA/ASIA, PEM-Header, ghp_/github_pat_, xox*, `key=value`≥16) erzwingt `sensitivity=security` OHNE den Write zu blocken → Row fällt aus VAULT-SYNC-Kandidatur (`provider._sync_base_candidates`).
- Alle 3 als Spec-§12-Rows dokumentiert (`docs/superpowers/specs/2026-06-28-memory-router-python-cli-design.md`). 13 neue Tests; 513/513 Unit, Parity ALL GREEN (Record-Parity-Fälle nutzen saubere Refs → kein Diff).

### `fb13559` — CHANGELOG-Backfill: Unreleased-Block deckt jetzt B1+B2+B3 (+ später B4) ab; `/release`-Voraussetzung erfüllt.

### `68cbf7e` — B4-Plan + Erhaltungsvertrag (User hat Struktur freigegeben, dann externes Plan-Audit)
- `docs/superpowers/plans/2026-07-02-token-restructuring.md` (rev 3) + `…-invariants.md` (rev 3).
- **Audit-Trail:** Runde 1 (2 Auditoren: Regel-Erhaltung, Mechanik/Konsumenten) → 1 BLOCKER (9 weitere `.memory`-Default-Assertions in test-launcher-status.sh) + 4 HIGH (Output-seitiges `del()` statt Assembly-Eingriff; Smoke-Greps auf reference-Alias-Zeilen; Opt-in-Verbot in Frontmatter ungeschützt; Zeile-190-Verbote ungeschützt) → rev 2. Runde 2 (Delta + Kohärenz) → 2 HIGH (WS3-Exception-Widerspruch Row 147; quick-Review-light-Definition ungeschützt) + 5 MEDIUM → rev 3. Runde 3: sauber (nur Label-LOW). **Alle Findings vom Orchestrator selbst am Code verifiziert, bevor sie in den Plan flossen.**
- **Codex-Cross-Family-Seat war nicht verfügbar:** `codex exec` hing 2× mit 0 Byte Output (~0 CPU; vermutlich Auth/Netz) → Same-Family-Fallback, im Plan vermerkt. Nächste Session ggf. `codex` erst mit Mini-Prompt probieren.

### `84672dd` — Beifang: 3 stille Smoke-Regressionen aus B1/B2
Der per-Commit-Smoke-Loop (neu in B4) fand: smoke-install.sh erwartete die **alten unquoted** `${KIMIFLOW_PLUGIN_ROOT…}`-Manifest-Formen (B2 hat gequotet) und greppte `Current-State Gate` (B1 hat auf `Current-State Pulse / Gate` umbenannt). Smoke-Erwartungen auf das gehärtete Verhalten aktualisiert (fail-closed). **Lehre: Smokes gehören in jede Batch-Verifikation, nicht nur Suiten+Consistency.**

### `148dee7` — B4 Commit A: Launcher-First-Screen kompakt (TDD)
- Default-Output ohne `runs.items`, `background.items`, volles `memory`; Counts/`memory_summary`/`maintenance`/`.launcher` unverändert. Neues `--full` = heutige Vollform. **Trim NUR bei Serialisierung** (`TRIM='del(…)'` nach dem `.launcher`-Merge, launcher-status.sh Ende) — `.launcher` liest `$snapshot.memory` intern (Zeilen ~694/775f./818–823), bleibt byte-identisch.
- RED: 24 Fails (3 neue Default-Assertions + 15 auf `--full` rewired + 6 Folge) → GREEN komplett. reference.md-Drilldowns (Backlog/Background/Memory + „Mechanical snapshot"-Absatz) zeigen auf `--full`.

### `74cc10a` — B4 Commit B: `docs/commit-secret-gate.md`
Maintainer-Mechanik (Installer, Pattern-Liste, 4 Parsing-Boundaries, `-a`-Coverage) verbatim aus reference.md „Commit hygiene" extrahiert; Sektion behält operative Regeln + Pointer. LSP-Absatz → Pointer auf „Verification" (`KIMIFLOW_LSP_MAX_COMMANDS` dorthin gefaltet). 6 Kommentar-Pointer in commit-secret-gate.sh/test aktualisiert. Verifiziert: kein Hook/Test/Smoke grept die verschobenen Inhalte.

### `1407ca5` + `53cc587` — B4 Commits C+D: SKILL.md-Kompression unter Erhaltungsvertrag
- **Needle-Check:** `docs/superpowers/plans/2026-07-02-invariant-check.sh` — 130+ verbatim Needles (Invarianten + Smoke-Phrasen, SKILL.md UND reference.md), VOR der Kompression gegen den Ist-Zustand validiert („INVARIANTS OK"), gated seitdem jeden Edit. **Bei künftigen SKILL.md/reference.md-Edits mitpflegen/laufen lassen.**
- Pass 1 (C, von Hand): Duplikat-Enumerationen, Alias-Restatement, Project-Map-Prosa, Phase-6-Fastkopie, die 4K-Learning-Loop-Zeile → operative Kerne + Pointer. Reviewer-Spawn-Vertrag: **Reviewer lesen reference.md nicht mehr** — Spawn-Prompt inlined Lens-Definition + FINDING/CANDIDATE-Grammatik + Datei-Form-Constraints (Spalte 0, kein Newline im Reason, nur FINDING-Zeilen oder einzeiliges `NONE` — byte-kompatibel zur Resolver-Regex `resolve-review-gate.sh:57`). Lens A/B kanonisch in der Rubrik (additiv, reference.md ~1435ff.); Phase 7 hat jetzt den Orchestrator-Rubrik-Read. Row 147 (Audit-Lens) + quick-Ensemble (23/181) bleiben VOLL in SKILL.
- Pass 2 (D, delegiert mit Nur-Streichen-Vertrag, Diff von mir reviewt): −690 Bytes, rein deletiv.

---

## OFFEN 1 — Floor-Entscheidung (User!)

SKILL.md: **60.463 → 53.277 Bytes (−12 %)**. Das ≤30K-Ziel ist **ohne Regelverlust nicht erreichbar** — empirisch sind ~85 % des Resttexts geschützte Regeln (Invarianten-Artefakt). Die Est.-saved-Spalte des Baseline-Audits hatte die Regeldichte massiv unterschätzt (Runde-2-Auditor hatte genau das als MEDIUM geflaggt).

Die großen realen Hebel sind gebankt: Launcher-JSON −13K+ chars/Aufruf · Reviewer-Spawns lesen die 11,4K-Rubrik nicht mehr (mehrfach pro Run) · Commit-Gate-Pfad −4,5K · deterministischer Spawn-Vertrag.

**Optionen:** (a) Floor akzeptieren (Empfehlung) · (b) gezielt Regelgruppen auf on-demand verschieben — nur mit expliziter User-Freigabe pro Gruppe (weicht den Erhaltungsvertrag auf). Im CHANGELOG-Unreleased ist der Floor bereits ehrlich dokumentiert.

## OFFEN 2 — B5: Re-Audit + Restbefunde

1. **Re-Audit:** frische, unabhängige Konsistenz- + Token-Auditoren über SKILL.md/reference.md/launcher nach B4 (gleiches adversariales Muster; Anti-Halluzination gilt; Findings selbst verifizieren). Danach volle Suiten + Smokes (Loop: alle `hooks/test-*.sh` außer `test-gate.sh`/`test-weakening-scan.sh`, + `smoke-install.sh` + `smoke-install-codex.sh` + `release-consistency-check.sh` + Needle-Check).
2. **Restbefunde aus dem Baseline-Audit** (unverändert offen, Details im Vorgänger-Handoff):
   - `state-gate.sh:61` Deny-Message behauptet „only trivial runs without STATE" (widerspricht SKILL.md; Wording angleichen).
   - Helper-Drift `resolve_root` (agentic-readiness `pwd -P`+hard-die vs. logical+fallback in 3 Siblings) · `state_value` Case-(In)Sensitivity (clarify-gate vs. active-run/launcher).
   - `project-map-status.sh` bare `mktemp` in $TMPDIR + `mv` (cross-device nicht atomar, 0600-Mode, ENOSPC druckt trotzdem REFRESHED; Zeilen ~314/425/441/537).
   - release-Skill (`.claude/skills/release/SKILL.md`) loopt `hooks/test-*.sh` inkl. der 2 Produktions-Hooks — an den CI-Discovery-Loop angleichen (exkludiert sie).
   - Codex-Port `skills/kimiflow/SKILL.md`: Schreibweise „Current-State Pulse/Gate" prüfen/angleichen.
   - `test-gate.sh` untracked-Marker-Trust-Boundary: dokumentierter Residual — **Entscheidung: belassen.**
3. Danach: CHANGELOG ist release-ready; Push + `/release` nur auf User-Ansage.

## OFFEN 3 — Nicht gepusht

11 Commits lokal auf `main` (`2b5c096` bis `53cc587`). Kein Push ohne User-OK.

---

## Arbeitsweise, die sich bewährt hat (beibehalten + neu)

1. Findings NIE ungeprüft übernehmen — jeder Auditor-/Agent-Befund wird am Code verifiziert (Repro/grep), bevor er in Plan oder Fix fließt. Hat diese Session 2 falsche Prämissen des Handoffs korrigiert (T2-Rubrik-Inlining existierte nicht; Est.-saved zu optimistisch).
2. Hook-/Python-Fixes: failing test first, im Stil der bestehenden Suite; Parity-Auswirkung prüfen; bewusste Divergenzen als Spec-§12-Row + Code-Kommentar.
3. **Pro Commit:** benannte Pfade, volle Suiten + BEIDE Smokes + release-consistency + (bei SKILL/reference-Edits) Needle-Check. Smokes nie auslassen — sie haben B1/B2-Regressionen gefangen.
4. Plan-Audit-Muster: 2+ frische adversariale Auditoren mit disjunkten Lenses, Findings-Format `FINDING <SEV> <ref> :: <reason>`, Cap 3; Codex-Seat optional, bei Hang → Same-Family-Fallback + Vermerk.
5. Delegierte Kompression nur mit hartem Vertrag (nur Streichen, Unantastbar-Liste, Checks selbst laufen lassen) + eigenem Diff-Review.
6. Bash 3.2 (macOS) Target; `${arr[@]+…}`-Idiom; macOS hat kein `timeout` (Bash-Tool-Timeout nutzen).
7. User-Kommunikation: Deutsch, TLDR zuerst; ehrliche Floor-/Kosten-Reports statt schöngerechneter Ziele.
