# Handoff: Outcome-Evidence vor Simplification und Calibration

## Abschlussstatus am 09.08.2026

Dieses Handoff ist umgesetzt und bleibt als historische Entscheidungsgrundlage erhalten. Die nachfolgenden
Abschnitte dokumentieren den damaligen Ausgangsstand und sind keine offenen Arbeitsanweisungen mehr.

- `0.4.0` ist das erste öffentliche und einzige GitHub-Release.
- Das model-freie Outcome-Evidence-MVP besteht aus dem geschlossenen v1-Schema,
  `evals/outcome-comparisons.jsonl`, dem Validator/Summarizer `hooks/outcome-comparisons.sh` und der
  menschenlesbaren Auswertung in `evals/outcomes.md`.
- Der Evaluator startet weder Plain- noch Kimiflow-Sessions und ist ausschließlich für aufgezeichnete Läufe
  in Wegwerf-Benchmarkprojekten vorgesehen.
- Zwei Pilotvergleiche samt fingerprint-gebundener Evidence sind erfasst. Beide sind wegen dokumentierter
  Confounds korrekt als `field_note` ausgeschlossen; es gibt weiterhin null primär gültige Paare und keinen
  Qualitätsclaim (`claim_status=insufficient_evidence`).
- Der nächste Evidenzschritt ist die getrennt autorisierte Erhebung sauberer, vergleichbarer Paare. Private
  oder produktive User-Projekte bleiben als Testumgebung ausgeschlossen.

---

**Zweck:** Die naechste Session soll Kimiflows wichtigste offene Produktfrage messbar beantworten:
Produziert Kimiflow bei vergleichbarem Zeit-/Tokenaufwand bessere Software-Outcomes als eine gute Plain-Session?
Bis diese Evidenz existiert, gilt ein Feature-Freeze fuer neue grosse Kimiflow-Funktionen.

**Arbeitsreihenfolge (entschieden):**

1. Outcome-Evidence
2. Simplification anhand der gemessenen Kosten und Wirkung
3. Calibration der bestehenden deterministischen Heuristiken

Keine neue Integrationsschicht, kein GraphRAG, kein zusaetzlicher Planner/Reviewer/Agent und kein weiterer
Runtime-Controller vor belastbaren Vergleichsdaten.

---

## 1. Aktueller Repository- und Release-Stand

- Repository: `kimikonapps/kimiflow`
- Branch: `main`
- HEAD bei Erstellung: `5363edfc515446483bd3cbbe92fc51b232b53d30`
- Release: `kimiflow--v0.3.0`
- 0.3.0 ist das einzige GitHub-Release und der einzige verbleibende `kimiflow--v*`-Tag.
- 47 aeltere Release-Objekte und 99 aeltere lokale/remote Versionstags wurden entfernt.
- Die Git-Historie wurde nicht umgeschrieben; alte Source-Commits bleiben erreichbar.
- 0.3.0 wurde vor der Veroeffentlichung mit 53 automatisch entdeckten Testskripten und beiden
  Installations-Smokes verifiziert.
- Der Arbeitsbaum war vor Anlage dieses Handoffs sauber und synchron mit `origin/main`.

Wichtig: Dieses Handoff ist die einzige neue Datei der aktuellen Session. Es wurden bei der Analyse keine
Produktionsdateien veraendert.

---

## 2. Verifizierte Ausgangslage

### 2.1 Kimiflow beweist noch keine besseren Outcomes

`evals/outcomes.md` enthaelt weiterhin:

> Comparisons recorded: 0.

Die vorhandenen Evaluationspfade pruefen andere, weiterhin wertvolle Dinge:

- `evals/README.md`: mechanische Gate-Integritaet und Behavioral Release Calibration.
- `hooks/memory_router/outcomes.py`: klassifiziert einen Kimiflow-Lauf anhand eigener Run-Evidence als
  `verified_success`, `verified_failure` oder `inconclusive`.
- `STRATEGY-OUTCOMES.jsonl`: speichert Kimiflow-interne Strategie-Outcomes fuer spaeteren Recall.

Keiner dieser Pfade vergleicht denselben Task gegen eine Plain-Session. Die aktuelle Outcome-Auswertung
beweist einen intern konsistent abgeschlossenen Kimiflow-Lauf, nicht eine externe Qualitaetsueberlegenheit.

Das bestehende `evals/outcomes.md` ist ehrlich, aber fuer belastbare Evidenz noch zu schwach:

- Ein geschaetzter Plain-Arm ist erlaubt; Schaetzungen duerfen nicht in die primaere Auswertung.
- Zeit, User-Interaktionen, Acceptance-Pass, Restdefekte und Diff-LOC fehlen.
- Es gibt kein maschinenvalidiertes Pairing beider Arme.
- Es gibt keinen verblindeten gemeinsamen Abschlussreview.

### 2.2 Interne Komplexitaet ist ein reales Risiko

Gemessener Stand am 08.08.2026:

- `hooks/kimiflow_core/active_run.py`: 194.559 Bytes, 4.702 Zeilen.
- `hooks/kimiflow_core/adaptive_control.py`: 96.007 Bytes, 2.376 Zeilen.
- Python-Produktionscode unter `hooks/kimiflow_core`: ca. 1,57 MB.
- Python-Tests unter `hooks/kimiflow_core/tests`: ca. 1,00 MB.

Wachstum von `active_run.py`:

- 15.07.2026: 48.588 Bytes
- 20.07.2026: 110.805 Bytes
- 25.07.2026: 161.604 Bytes
- 08.08.2026: 194.559 Bytes

Der Code ist defensiv und gut getestet. Das Problem ist Verantwortungsakkumulation, nicht sichtbares Chaos.

`active_run.py` enthaelt weiterhin mehrere stabile Verantwortungsgruppen:

- Session/Owner/Status und Hook-Fassade
- Intake/Intent/Receipts
- Items und idempotente Item-Mutationen
- Staleness/Baseline/Conflict Check
- Finish/Terminal/Learning/Outcome/Worktree Retirement

`adaptive_control.py` enthaelt nicht nur den Classifier:

- Scope-/Risiko-Klassifikation
- Context Rollover
- Model Routing
- Retrieval Routing
- Review Routing und Review Cascades
- Execution Variants
- mehrere lokale Outcome-Ledger

### 2.3 Adaptive Control ist bewusst heuristisch

`adaptive_control.classify()` verwendet deterministische Signale wie Schema, Migration, Database, Security,
Public API, Irreversibilitaet und Zahl der Top-Level-Subsysteme.

Bestehende Sicherheitsmerkmale:

- explizite Non-Goals werden vor der Klassifikation entfernt;
- der deklarierte Scope kann nur beibehalten oder hochgestuft werden;
- es gibt keine stille Herabstufung;
- der Classifier trifft keine Produktentscheidung;
- kein Modell- oder Netzwerkaufruf.

Verbleibende Grenze: Ein komplexes Feature ohne passende Keywords und mit wenigen Pfadpraefixen kann bei einer
bereits falschen initialen Scope-Einschaetzung klein bleiben. Daher nicht ersetzen, sondern spaeter gegen reale
Outcome-Daten kalibrieren.

### 2.4 Mechanische Gates sind keine Semantik-Orakel

Der aktuelle Clarify-Vertrag ist staerker als ein einzelner Marker: strukturierte Intake-Felder, Provenance,
Digests, Intent Lock und aktuelle Receipts werden erneut durch nachgelagerte Gates geprueft. Trotzdem kann ein
formal korrektes Artefakt eine fachlich falsche Interpretation enthalten.

Nicht mit weiteren Markern beantworten. Die richtige Gegenprobe ist externe Outcome-Evidence:

- versteckte Acceptance-/Regressionstests;
- verblindeter gemeinsamer Abschlussreview;
- bei fachlich mehrdeutigen Faellen ein menschliches Intent-Urteil.

---

## 3. Naechster Arbeitsauftrag: kleinstes Outcome-Evidence-MVP

### 3.1 Ziel

Eine kleine, model-freie Erfassungs- und Auswertungsschicht fuer echte gepaarte Versuche bauen. Sie startet
keine Sessions, ruft kein Modell auf und veraendert den Kimiflow-Runtime-Flow nicht.

Das MVP soll nur:

1. einen echten Kimiflow- und Plain-Arm desselben Tasks sicher paaren;
2. Evidence und Messwerte streng validieren;
3. eine ehrliche gepaarte Zusammenfassung erzeugen;
4. bei zu wenig Daten ausdruecklich keinen Qualitaetsclaim ausgeben.

### 3.2 Minimaler Artefaktvorschlag

- `evals/outcome-comparisons-v1.schema.json`
  - maschinenlesbarer, versionierter Pair-Vertrag.
- `evals/outcome-comparisons.jsonl`
  - eine menschenlesbare JSON-Zeile pro Task-Paar; beide Arme in derselben Row.
- kleiner Validator/Summarizer nach vorhandenem Projektmuster
  - bevorzugt Python-Core plus duenne Shell-Fassade;
  - nur `validate` und `summary`, kein Runner und keine Provider-Integration.
- `evals/outcomes.md`
  - Methodik und menschenlesbare Interpretation;
  - verweist auf JSONL als Messquelle;
  - keine automatisch erfundene Marketingzahl.

Keinen zweiten Speicher, keine Datenbank und keine externe Telemetrie einfuehren.

### 3.3 Pair-Vertrag

Eine Pair-Row muss mindestens binden:

- `schema_version`
- stabile `comparison_id` und `task_id`
- `task_kind`: `bug` oder `feature`
- Repository-/Snapshot-Identitaet und identischer `source_commit`
- Digest des identischen Tasktexts
- vorab eingefrorene Acceptance-/Oracle-Evidence
- zufaellige oder vorab festgelegte Arm-Reihenfolge
- gemeinsame Modell-, Effort-, Tool-, Permission-, Zeit- und Token-Budgets
- genau zwei Arme: `plain` und `kimiflow`
- je Arm:
  - echte Run-/Session-Identitaet, niemals eine Schaetzung fuer Primaerdaten
  - Start-/Endzeit
  - Modell- und Execution-Fingerprint
  - Input-/Output-Token, Model-/Tool-Calls
  - User-Interaktionen
  - Rework-Runden
  - Acceptance-Ergebnis
  - intern vor Abschluss gefundene BLOCKER/HIGH
  - nach Abschluss im gemeinsamen Blind-Review verbleibende BLOCKER/HIGH
  - Git-Diff-LOC
  - Evidence-Referenzen/Fingerprints
  - Confounds und fehlende Werte explizit als `null`, niemals geraten
- optional spaeter aktualisierbare Post-Merge-Defekte mit Beobachtungsfenster

Geschaetzte oder nicht sauber gepaarte Arme duerfen als Field Notes erhalten bleiben, werden aber von der
primaeren Vergleichsauswertung ausgeschlossen.

### 3.4 Wichtige Metriktrennung

Nicht vermischen:

1. **Interne Defekte vor Abschluss:** Zeigt, ob ein Prozess Fehler noch selbst findet.
2. **Restdefekte nach Abschluss:** Faire Outcome-Qualitaet beider Arme unter demselben Blind-Review.
3. **Post-Merge-Defekte:** Spaete reale Qualitaet, separat nach 7-30 Tagen.

Viele interne Findings koennen gute Fehlererkennung oder schlechte Erstimplementierung bedeuten. Erst die
Restdefekte und Post-Merge-Defekte beantworten die eigentliche Produktfrage.

### 3.5 Validator muss fail-closed pruefen

- beide Arme haben denselben Source-Commit und Task-Digest;
- Modell-/Effort-/Tool-/Permission-/Budget-Vertrag ist vergleichbar;
- Arm-Namen sind exakt `plain` und `kimiflow` und kommen jeweils einmal vor;
- primaere Daten sind tatsaechliche Messungen, keine Schaetzungen;
- Evidence-Referenzen sind relativ, sicher, vorhanden und fingerprint-gebunden;
- Zaehler sind nichtnegativ und keine Booleans;
- Start/Ende und Beobachtungsfenster sind plausibel;
- Blind-Review und Acceptance-Oracle sind fuer beide Arme identisch;
- fehlende Werte sind `null`, nicht `0` oder eine Schaetzung;
- ungueltige Paare werden gezaehlt und aus dem Primaerergebnis ausgeschlossen.

### 3.6 Summary-Vertrag

Ausgabe mindestens:

- aufgezeichnete, gueltige und ausgeschlossene Paare;
- Bug-/Feature- und Repo-Verteilung;
- Completion und Acceptance je Arm;
- paarweise Differenz der Rest-BLOCKER/HIGH;
- Median der paarweisen Token-, Zeit-, Interaktions-, Rework- und LOC-Differenzen;
- Win/Tie/Loss pro Task nach einer vorab dokumentierten, qualitaets-first Reihenfolge;
- Zahl ausstehender Post-Merge-Fenster;
- `claim_status=insufficient_evidence`, solange weniger als 10 gueltige Paare vorliegen.

Keine zusammengesetzte 0-10-Bewertung und keine Signifikanzbehauptung aus einer kleinen Stichprobe.

---

## 4. Versuchsprotokoll nach dem MVP

Ziel fuer den ersten belastbaren Durchgang: **12 gueltige Paare**.

- 6 Bugs, 6 Features
- mehrere reale Projekte, nicht nur das Kimiflow-Repo
- gleicher Ausgangscommit und identischer Auftrag
- frische isolierte Sessions und Worktrees
- gleiche Modelle, Effort-Stufen, Tools, Rechte und harte Budgets
- keine Uebernahme von Wissen oder Artefakten zwischen den Armen
- Arm-Reihenfolge vorab festlegen/randomisieren
- versteckte Acceptance-/Regressionstests erst nach Abgabe ausfuehren
- derselbe Reviewer prueft beide Ergebnisse verblindet
- Confounds dokumentieren; problematische Paare nicht schoenrechnen

Nach vier instrumentellen Trockenlaeufen nur Messfehler im Harness korrigieren, nicht Kimiflows Verhalten auf
die Ergebnisse hin tunen. Danach 12 echte Paare erheben. Erst nach mindestens 10 gueltigen Paaren einen
vorsichtigen Outcome-Satz formulieren; bei widerspruechlichem Signal bis 20 Paare erweitern.

Primaere Reihenfolge fuer das Urteil:

1. Acceptance/Completion unter dem gemeinsamen Budget
2. verbleibende BLOCKER/HIGH
3. Post-Merge-Defekte
4. bei gleicher Qualitaet: Token, Zeit, Interaktionen, Rework und LOC

Ein akzeptabler maximaler Kostenaufschlag ist eine Produktentscheidung und darf nicht vom Implementer erfunden
werden. Vor dem realen Vergleichslauf entweder vom User festlegen lassen oder nur die Rohdifferenzen berichten.

---

## 5. Simplification erst nach Outcome-Evidence

Fuer jeden im Vergleich aktivierten Kimiflow-Mechanismus soweit beobachtbar zuordnen:

- verursachte Token-/Zeitkosten;
- zusaetzliche Rework-Runden;
- konkret vor Abschluss verhinderten Defekt;
- False-Block oder Arbeit ohne messbaren Outcome-Beitrag.

Danach schneiden oder enger konditionalisieren, nicht vorher. Sicherheitsgrenzen fuer Secret-, Pfad-,
Atomicity-, Conformance- und Release-Integritaet nicht allein wegen fehlender kleiner Stichprobe entfernen.

### Empfohlene modulare Reihenfolge

Die bestehenden CLI-/Import-Fassaden stabil lassen und nur Interna verschieben:

1. `active_run.py`: zuerst den relativ isolierten Items-/Mutation-Bereich extrahieren.
2. Danach Intake/Intent/Receipt-Logik.
3. Finish/Terminal/Retirement zuletzt, wegen Rollback-, Lock- und Atomicity-Risiko.
4. `adaptive_control.py`: Classification, Context Rollover und outcome-basiertes Routing entlang der bereits
   sichtbaren Verantwortungsgrenzen trennen.

Keine 30 Minidateien. Keine Verhaltensaenderung im selben Commit wie eine Extraktion. Bestehende Parity- und
Fail-closed-Tests muessen unveraendert gruene Receipts/Outputs liefern.

Dateigroesse ist nur Warnsignal. Vor jedem Split zusaetzlich Git-Churn, gemeinsame Aenderungen und
Abhaengigkeiten ansehen. Kein harter Release-Gate allein auf Zeilenzahl.

---

## 6. Calibration nach den Vergleichslaeufen

Pro Task zusaetzlich erfassen:

- deklarierter Scope;
- adaptive Scope-Entscheidung und Reason-Codes;
- retrospektive Risikoklasse;
- tatsaechliche Restdefekte und Mehrkosten.

Daraus eine kleine False-positive-/False-negative-Matrix erstellen. Erst danach Keywords oder
Subsystem-Schwellen aendern.

Beibehalten:

- deterministisch und model-frei;
- Scope-Ratchet nur nach oben;
- Non-Goal-Filter;
- keine Produktentscheidung durch den Classifier.

Nicht einfuehren: einen weiteren LLM-Classifier nur zur Scope-Wahl.

---

## 7. Erfolgskriterien fuer die naechste Implementierungs-Session

Das Outcome-Evidence-MVP ist fertig, wenn:

1. Schema, JSONL-Vertrag und Validator ein echtes Pair sicher darstellen.
2. Tests mindestens Mismatch, fehlende/unsichere Evidence, Schaetzungs-Ausschluss, negative Counter,
   Duplicate Arms, `null`-Semantik und weniger-als-10-Claim-Sperre abdecken.
3. `summary` nur aus validierten Rows rechnet und paarweise Kennzahlen ausgibt.
4. Vorhandene Kimiflow-Evidence wie `HOST-USAGE.json`, `EXECUTION-TRACE.json`, Session-Zeitstempel und Git-Diff
   wiederverwendet werden kann, ohne den Runtime-Flow zu erweitern.
5. `evals/outcomes.md` weiterhin ehrlich sagt, dass noch kein besseres Outcome bewiesen ist.
6. Keine neue Modell-, Netzwerk-, MCP-, Memory- oder Agentenschicht entsteht.
7. Relevante Target-Tests, JSON/Shell-Pruefungen und bestehende Eval-Tests gruen sind.

Nicht Teil der ersten Session:

- die 12 realen Vergleichspaare vollstaendig ausfuehren;
- Kimiflow-Gates entfernen;
- `active_run.py` oder `adaptive_control.py` refactoren;
- den Classifier tunen;
- ein neues Release schneiden.

---

## 8. Empfohlener Startprompt fuer die neue Session

> Lies `AGENTS.md` und
> `docs/superpowers/handoffs/2026-08-08-outcome-evidence-simplification.md` vollstaendig. Baue nur das dort
> beschriebene kleinste Outcome-Evidence-MVP: maschinenvalidierte gepaarte Plain-vs-Kimiflow-Evidence plus
> ehrliche Summary, ohne Runner, Modellaufruf oder Runtime-Flow-Aenderung. Beginne mit Repository-Inspektion,
> Annahmen und pruefbaren Erfolgskriterien. Feature-Freeze, Outcome-Evidence vor Simplification/Calibration.
