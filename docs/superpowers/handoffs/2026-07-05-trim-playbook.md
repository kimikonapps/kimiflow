# Trimm-Playbook — datenbasierte Schnitt-Entscheidungen nach dem Feature-Freeze

**Zweck:** Dieses Dokument macht das Architektur-Urteil für die nächste Trimm-Runde ausführbar,
ohne dass es neu getroffen werden muss. Jede verbleibende Subsystem-Frage bekommt ein messbares
Kriterium + die konkrete Schnitt-Prozedur. Ausführbar von jedem Modell (Opus reicht); die
Urteile sind hier festgeschrieben.

**Kontext:** Feature-Freeze seit 05.07.2026 (Handoff `2026-07-05-entschlackung-followups.md`, Punkt F).
Datenstand bei Erstellung: 40 Global-Ledger-Rows (7 Projekte, 26.06.–04.07.), Ergebnis
8× saving / 8× waste / 24× unknown, 251 Recall-Hits davon 29 genutzt (11,5 %), Ø 538 Token
netto/Run. Seit `23df613` trägt jede neue Row ein `scope`-Feld — die Kriterien unten setzen
darauf auf. Alte Rows (ohne `scope`) für Scope-Fragen ignorieren.

**Meta-Regeln (gelten für jeden Schnitt):**
1. **Demotion vor Deletion.** Erst auf advisory/opt-in stellen (Spec-Edit), erst nach einer
   Opt-in-Periode mit Null-Nutzung löschen — das war das Muster der Entschlackung (Zero-Usage-
   Evidenz → Removal) und es hat sich bewährt.
2. **Entscheidungszeitpunkt:** frühestens nach **≥15 neuen Rows** im Global-Ledger ODER ~2 Wochen
   echter Nutzung, je nachdem was zuerst eintritt. Bei dünner Datenlage: nicht schneiden, warten.
3. Jeder Schnitt läuft als normaler kimiflow-Run (audit/feature) mit den üblichen Gates;
   CHANGELOG-Eintrag; Tests/Smokes/release-consistency vor Commit.
4. Messbasis: `~/.kimiflow/metrics/token-economics.jsonl`. Auswertung z. B.:
   `jq -s '[.[] | select(.scope=="large")] | {n: length, used: (map(.used_hit_count)|add), hits: (map(.recall_hit_count)|add), net: (map(.net_estimated_tokens_saved)|add)}' ~/.kimiflow/metrics/token-economics.jsonl`

---

## 1. Memory-Recall bei `scope=large` — die offene Kernfrage

- **Kosten:** ~400–450 recall_tokens/Run + Orchestrator-Aufwand (MR status + recall + Einarbeitung).
- **Evidenz heute:** 11,5 % Hit-Nutzung über alles; small/quick wurde deshalb schon abgeschaltet
  (`b0d80e0`). Für `large` isoliert: **keine Daten** (Rows waren scope-blind).
- **Kriterium (SCHNITT):** Nach ≥8 `large`-Rows mit `scope`-Feld: Median `used_hit_count` = 0
  ODER `net_estimated_tokens_saved` ≤ 0 in >50 % der large-Rows → Recall auch bei `large` streichen,
  d. h. Recall wird komplett entfernt (kein Opt-in-Zwischenschritt nötig — die Opt-in-Stufe war
  faktisch schon der small/quick-Skip).
- **Kriterium (BEHALTEN):** Median `used_hit_count` ≥ 2 bei large → behalten, nichts tun.
- **Prozedur:** Gleiches Muster wie `b0d80e0`: `phases/phase-2-understand.md` (Recall-Schritt),
  `reference.md` „Memory recall", Render-Quellen, Smoke-Needles. Learning-**Writes** (Loop, Phase 7)
  davon getrennt behandeln → Punkt 2.

## 2. Learning Loop (review-run, Usefulness-Lifecycle, Curation, Proposals)

- **Kosten:** Phase-7-only (bounded); Spec-Fläche in reference.md; MR-Subkommandos.
- **Evidenz heute:** Quality-Gate blockt die meisten Writes (gewollt); wenige durable Learnings
  je Run (typisch 0–3); Nutzung der Learnings ist Teil der Recall-Frage (Punkt 1).
- **Kriterium (SCHNITT auf Kern):** Wenn Punkt 1 mit SCHNITT endet UND
  `jq -s '[.[] | select(.usefulness.hot.count + .usefulness.warm.count > 0)] | length'` über
  `memory-router.sh status` je aktivem Projekt zeigt, dass <20 % der Learnings je hot/warm wurden →
  Lifecycle/Curation/Proposals-Maschinerie aus der Spec nehmen (Sections in reference.md
  „Memory Router & Learning Loop" auf: append LEARNINGS.jsonl + `review-run` eindampfen);
  Python-Code bleibt (ungenutzte Subkommandos schaden nicht, kein Code-Risiko).
- **Kriterium (BEHALTEN):** Recall bleibt (Punkt 1 BEHALTEN) → Loop unangetastet lassen,
  er ist der Zulieferer.

## 3. Vault-Pulse / Vault-Sync (Obsidian)

- **Kosten:** nur noch bei `large` (seit 0.1.59); 1 MCP-Query + ggf. Save-Frage.
- **Evidenz heute:** wiederholt 0-Treffer-Queries (z. B. fable-leaf-routing-Run); Sync-Kandidaten
  selten exportiert.
- **Kriterium (SCHNITT):** 10 aufeinanderfolgende large-Runs mit 0 genutzten Vault-Treffern und
  0 angenommenen vault-saves → Vault-Pulse auf explizites Opt-in (`--vault`) stellen; Sync-Pfad
  (VAULT-SYNC.md, provider sync) unangetastet lassen (separater, billiger Pfad).
- **Prozedur:** `phases/phase-2-understand.md` Vault-Pulse-Schritt + reference.md „Vault Pulse" —
  Bedingung von „scope=large" auf „Flag gesetzt" ändern. Kleiner Spec-Edit, Muster `b0d80e0`.

## 4. Cross-Family-Kette (Codex → Gemini → same-family)

- **Kosten:** pro Review-/Verify-Seat ein CLI-Call mit Timeout (~5 min Seats, ~30 min Implementer).
- **Evidenz heute:** hat in den Entschlackungs-Wellen reale Findings geliefert; Transport-Stalls
  vom 05.07. betrafen das Companion-Plugin, NICHT kimiflows `codex exec`-Transport. **Behalten.**
- **Kriterium (nur Transport-Fix, kein Schnitt):** Wenn in STATE.md-Zeilen (`cross_family:`) über
  2 Wochen >50 % der Seat-Calls in timeout/fallback enden → Kette auf EIN Cross-Family-Glied
  kürzen (Codex ODER Gemini per Order-Token), nicht abschaffen.

## 5. Phase-Reads-Enforcement

- **Evidenz:** verhindert eine reale Deadlock-Klasse (0.1.56-Fix); Kosten minimal (Hash-Records).
- **Urteil: behalten.** Nur handeln, wenn das Gate nachweislich falsch blockt
  (>1 False-Block/Woche, in STATE dokumentiert) → dann Gate-Bedingung prüfen, nicht entfernen.

## 6. Project Map (quick + refresh --changed)

- **Evidenz:** 2 Realtests am 05.07. erfolgreich; Pflegepfad funktioniert. **Behalten.**
- **Bekannte Klein-Fixes (jederzeit, jedes Modell, kein Fable-Urteil nötig):**
  a) überlappende nackte `hooks/`-Prefixes → False-Positive-Kaskaden (längster-Prefix-Adoption);
  b) `refresh --changed` adoptiert untracked/dirty Dateien → committed-only-Modus;
  c) `project-map-status.sh refresh` ignoriert unbekannte Flags stillschweigend (`--dry-run`
     führte real aus) → unknown-flag = Fehler.
  Auslöser: sobald einer davon im Dogfooding zum zweiten Mal stört.

## 7. Simplicity-Lens / Prosecutor, Eval-Suite, Active-Session-Contract

- **Urteil: behalten, kein Kriterium nötig.** Simplicity ist advisory und bei `small` gefaltet
  (kein Spawn); Evals sind out-of-CI und kosten nur bei Pflege; der Active-Session-Contract
  trägt die Gates. Kein Trimm-Kandidat in Sicht.

## Offene Klein-Aufträge (unabhängig von Kriterien, jedes Modell)

- G-2: Eval-Szenario Leaf-Routing (nur wenn Evals ohnehin angefasst werden).
- Optionales volles `--project-map quick`-Re-Bootstrap dieses Repos (lokal, Map-Qualität).
- Parity-Strip-Fragilität (LOW, Opus-Review 05.07.): der `"scope":"…",`-Strip in
  test_economics/test_review setzt voraus, dass `scope` nicht letztes Feld der Row wird —
  fail-closed, nur beachten falls die Row je umsortiert wird.
