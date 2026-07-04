# Handoff: Fable-Token-Sparen — Leaf-Seats auf Opus routen

**Datum:** 2026-07-04 · **Status:** Design beschlossen, EINE Frage offen → nächste Session startet GENAU dort.
**Kontext:** User hat Session-Default auf **Fable 5** gestellt. Ziel: Fable-Tokens maximal sparen — Fable nur für Orchestrierung/Deep Thinking (Stamm), Subagenten (v. a. Audits) auf **Opus 4.8**. Wichtig: **Opus wird in naher Zukunft das Session-Modell** — alle Regeln müssen konditional sein (No-Op wenn Session schon Opus). Niemals automatisch ZU Fable eskalieren.

## ⏸️ OFFENE FRAGE (hier weitermachen)

**Welche Leaf-Seats gehen bei Fable-Session auf Opus?** Audits/Reviews sind gesetzt — offen ist, ob auch die Qualitäts-Seats (Planner, Implementer) auf Opus gehen:

| Option | Inhalt | Trade-off |
|---|---|---|
| **A: Alle Leaves → Opus** (war empfohlen) | Nur Orchestrator bleibt Fable | Maximale Ersparnis; Opus 4.8 für Planner/Implementer stark genug; Cross-Family-Reviews fangen ab |
| B: Qualitäts-Seats bleiben Fable | Planner + Implementer auf Fable, Rest → Opus | Weniger Ersparnis, stärkstes Modell wo Qualität entschieden wird |
| C: Nur Audits/Reviews → Opus | Minimalste Änderung | Geringste Ersparnis |

→ Dem User die Frage erneut stellen (AskUserQuestion), dann umsetzen.

## ✅ BESCHLOSSEN (vom User bestätigt, 2026-07-04)

Drei Schichten umsetzen, Env-Var NICHT:

1. **kimiflow-Routing** — neue konditionale Regel in `reference.md` → Sektion „Model routing (per-role)":
   *Session-Modell = Fable-Familie → betroffene Leaf-Seats spawnen per-Spawn mit `model: opus`; Smallest-Tier-Lenses (feature-check, 🧭 Explore-Direction) bleiben smallest tier; Orchestrator bleibt Session-Modell.* Advisory, nie ein Gate; No-Op bei Opus-Session (Muster wie „failure-security off-Fable", Release 0.1.57). **Als kimiflow-Feature durch den eigenen Loop bauen** (meta, wie model-aware-routing).
2. **Audit-Agenten pinnen** — `model: opus` ins Frontmatter von `code-review-audit` und `senior-reviewer` (User-Agents, vermutlich `~/.claude/agents/` — Pfad beim Umsetzen verifizieren). Deterministisch, 2 kleine Edits.
3. **CLAUDE.md-Zeile** (global, `~/.claude/CLAUDE.md`) — eine Zeile für ad-hoc Spawns außerhalb kimiflow: Session-Modell Fable → Subagenten standardmäßig `model: opus`.

**❌ Abgelehnt:** `CLAUDE_CODE_SUBAGENT_MODEL` env var — überschreibt laut Doku den per-Spawn-Parameter UND Frontmatter → würde kimiflows per-Seat-Routing plätten (keine Haiku-Lenses, keine gezielte Seat-Wahl, global). Bleibt Notbremse falls Prompt-Compliance flattert.

## Verifizierte Fakten (nicht neu recherchieren)

- **Preise:** Fable 5 = $10/$50 pro MTok · Opus 4.8 = $5/$25 → Opus ist der halbe Preis. Leaves fressen die Mehrheit der Run-Tokens.
- **Präzedenz (Claude-Code-Doku, code.claude.com/docs/en/model-config):** `CLAUDE_CODE_SUBAGENT_MODEL` > per-Spawn `model`-Param > Agent-Frontmatter `model` > erben. Doku-Zitat: *„The model to use for all subagents … Overrides the per-invocation `model` parameter and the subagent definition's `model` frontmatter. Set to `inherit` to use normal model resolution instead."*
- **Agent-Tool:** `model`-Param akzeptiert Aliase `sonnet|opus|haiku|fable`; per-Call schlägt Frontmatter.
- **kimiflow-Anker:** `reference.md` „Model routing (per-role)" hat schon Fable-bedingte Regeln (failure-security-Lens off-Fable wegen Fable-Safety-Classifiern) + per-Seat-`effort`. Cross-Family-Seats (Codex/`agy`-Gemini) kosten bereits null Anthropic-Tokens.
- Subagenten erben ohne Routing das Session-Modell → bei Fable-Session brennt heute jeder Leaf Fable.

## Separater geparkter Thread (nicht vergessen, eigene Baustelle)

**Kontextfenster-Reset-Harness** (Brainstorm dieser Session, vor dem Routing-Thema, kein Design-Doc geschrieben):
- User-Wunsch: bei ~400k Tokens automatisch Handoff schreiben + frisches Fenster.
- Erkenntnisse: `/clear` kann nur User oder ein äußerer Terminal-Driver auslösen (Harness-Grenze); Token-Messung ist gratis (Transcript-JSONL `usage`-Felder: cache_read + cache_creation + input); Hermes Agent (Nous Research) löst es mit Kompression@50% + Session-Reset mit „eine Runde zum Sichern" → `MEMORY.md` ≙ kimiflows `STATE.md`.
- Kernbefund: kimiflows Leaves laufen SCHON in eigenen Fenstern (Explore/Planner/Reviewer/Implementer/Verifier sind Subagenten) — das Problem ist der **Orchestrator-Stamm** (wächst durch Clarify-Convo, Subagent-Rückgaben, Artefakt-Re-Reads, Plan-Gate-Runden auf ~47% vor Build). Lösung wäre: Stamm an Phasen-/Iterationsgrenzen resetten + aus Artefakten rehydrieren; dafür müsste STATE.md sub-phasen-genau werden (Schritt N/M, letzter Commit, nächster Schritt) und Plan-Gate-Auflösungs-Entscheidungen je Runde persistiert werden (Runden-Log). Offen: Messung wo die 47% genau sitzen; interaktiv (User drückt /clear) vs. Terminal-Driver.
