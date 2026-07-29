# kimiflow

**Ein tokeneffizienter Feature- und Bugfix-Flow mit mechanischen Gates für Claude Code und Codex.**

[English](README.md) | [Workflow-Referenz](reference.md) | [Beispiele](examples/README.md) | [Kompatibilität](COMPATIBILITY.md)

[![Aktuelles Release](https://img.shields.io/github/v/release/kimikonapps/kimiflow?display_name=tag&sort=semver)](https://github.com/kimikonapps/kimiflow/releases/latest)

Kimiflow ist ein bewusst aufgerufener Skill beziehungsweise Plugin-Flow mit acht Phasen: klären,
verstehen oder diagnostizieren, planen, reviewen, umsetzen, verifizieren, Code prüfen und committen.
Einfache Arbeit bleibt klein; wichtige Grenzen werden durch getestete Skripte und Hooks abgesichert,
statt vom Modell nur behauptet zu werden.

Kimiflow kann konkrete Umsetzungsauftraege fuer substanzielle Feature-Arbeit automatisch routen.
Diskussionen, Ideen, Empfehlungen, Erklaerungen, Statusfragen und Wunschformulierungen bleiben direkt
und read-only. Fixes und kleine risikoarme Aenderungen bleiben ebenfalls direkt, sofern du nicht
`/kimiflow` in Claude Code oder `$kimiflow` in Codex aufrufst. Explizites `direct` oder `direkt`
umgeht Kimiflow immer.

## Warum Kimiflow

Claude Code und Codex können bereits planen, delegieren und reviewen. Kimiflow legt darum einen
dauerhaften, wiederaufnehmbaren Qualitätsvertrag:

- State und Evidence liegen unter `.kimiflow/<slug>/`;
- Plan- und Code-Review-Gates lösen BLOCKER/HIGH mechanisch auf;
- wiederholte Arbeit ohne neue dauerhafte Evidence wechselt automatisch die Strategie, statt nach einem weiteren Run zu fragen;
- Bugfixes brauchen Reproduktion, belegte Ursache und Red/Green-Evidence;
- wesentliche Produkt-/Berechtigungsentscheidungen warten auf menschliche Freigabe; verifizierte lokale Commits laufen automatisch, Push und Release bleiben explizit;
- nur erfolgreich verifizierte Learnings werden kuratiert;
- das stärkste gewählte Modell orchestriert, kleinere Worker übernehmen begrenzte Aufgaben.

Der Default ist nicht der größte, sondern der kleinste Flow, der die konkrete Arbeit sicher trägt.

## Installation

Voraussetzungen: `jq`, Git und `python3 >= 3.9` im `PATH`.

### Claude Code

In Claude Code:

```text
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```

Oder im Terminal:

```bash
claude plugin marketplace add kimikonapps/kimiflow
claude plugin install kimiflow@kimiflow
```

Danach Claude Code neu starten oder eine neue Session öffnen. Update:

```bash
claude plugin update kimiflow
```

### Codex

```bash
codex plugin marketplace add kimikonapps/kimiflow
codex plugin add kimiflow@kimiflow
```

Danach Codex neu starten, unter `/hooks` die gebündelten Kimiflow-Hooks einmal prüfen und freigeben und eine neue
Task öffnen. Codex verlangt diese Sicherheitsfreigabe absichtlich erneut, wenn ein Plugin-Update eine Hook-Definition
ändert. Update:

```bash
codex plugin marketplace upgrade kimiflow
```

Codex lädt den gebündelten Hook-Vertrag über den im Plugin-Manifest deklarierten `hooks`-Pfad. Es sind keine
Wrapper im User-Verzeichnis nötig. Der Marketplace veröffentlicht nur den sauberen Runtime-Kandidaten;
Maintainer-State, Eval-Eingaben und private Workflow-Artefakte bleiben draußen. Ein reproduzierbarer
Inhalts-Fingerprint bindet die ausgelieferten Dateien.

Für lokale Entwicklung kann der deklarierte Vertrag geprüft werden:

```bash
codex plugin marketplace add .
bash hooks/install-codex-hooks.sh --check
```

### Optionaler provider-neutraler Terminal-Runner

Das eingebettete Plugin bleibt der Standard. Für lange Kimiflow-Aufgaben aus dem Terminal gibt es zusätzlich
einen dünnen Controller, der nicht nach jedem Turn eine Bestätigung braucht:

```bash
bash hooks/install-kimiflow-cli.sh
kimiflow run "setze das gewünschte Feature um"
kimiflow status --pretty
```

Codex ist der eingebaute Adapter. Ein vorhandenes lokales oder entferntes Coding-Agent-Harness kann denselben
Lebenszyklus über den versionierten JSON-stdio-Vertrag ausführen:

```bash
kimiflow run --adapter command --adapter-command mein-agent-harness --model qwen-local \
  "setze das gewünschte Feature um"
```

Das Harness muss Datei-, Shell-, Test-, Resume- und Gate-Fähigkeiten ausweisen. Kimiflow hält Workflow,
mechanische Gates, Active-Run-Ownership, das begrenzte Turn-Limit und Usage-Receipts provider-neutral; der Adapter
besitzt nur Modelltransport und Tool-Ausführung. Es entstehen kein Daemon, zweiter Memory-Store oder Worktree.
Ein persistiertes Turn-Limit plus genau ein abschließender Recovery-Turn verhindert Endlosschleifen; ein
ausgeschöpfter Run bleibt ausdrücklich fortsetzbar, statt Erfolg zu behaupten.

Nur ein materieller Kimiflow-Wait oder Park endet mit Status 3. Die Antwort erfolgt mit
`kimiflow resume --message "<entscheidung>"`; unterbrochene oder transportbedingt gestoppte Runs können ohne
Message fortgesetzt werden, solange ihr Active Run offen ist. Der lokale Receipt enthält nur Transportdaten,
niemals Auftrag oder Transkript. `bash hooks/install-kimiflow-cli.sh --check` prüft den verwalteten Wrapper; ein
fremdes `kimiflow`-Executable wird nicht überschrieben.

### Einheitliche lokale Run-Steuerung

Rich Clients und Modell-Adapter können `hooks/run-bridge.sh` als JSON-stdio-Grenze für jeweils einen Aufruf
verwenden. Sie liefert eine deterministische Readiness-Sicht, akzeptiert nur owner-gebundene replay-sichere
Item-Mutationen und stellt inhaltsfreie Phase-Context-Metadaten sowie eine mehrdimensionale terminale Scorecard
bereit. Active Run, Graph, Phasen-, Review- und Finish-Gates bleiben maßgeblich; es entsteht weder Daemon noch
Netzwerkdienst oder Provider. Der Phase Context bleibt Shadow-Evidenz und ersetzt nie die jeweilige Phase-Datei
plus deren exakt zugewiesene Referenzabschnitte; `reference.md` wird nicht vollständig vorgeladen.
Terminale Scorecards bleiben nach dem Ende des Active Run über einen expliziten sicheren Run-Pfad lesbar.

### Optionale Kontinuität für Architekturänderungen und Multi-Run-Programme

Der normale einzelne Kimiflow-Run bleibt unverändert. Für größere Vorhaben gibt es drei explizite lokale
Kommandos:

- `hooks/build-replan.sh` springt aus Phase 5 nur mit aktueller Evidence für eine widerlegte PLAN-Annahme zurück; normale Testfehler bleiben im Build.
- `hooks/project-delta.sh` speichert nach einem erfolgreichen committeten Run eine verifizierte Architekturänderung und lädt sie später nur bei passenden betroffenen Pfaden.
- `hooks/program-engine.sh` validiert einen DAG unter `.kimiflow/programs/<name>/PROGRAM.json` und wählt genau einen deterministischen nächsten Run.

Die Program Engine bleibt absichtlich seriell und mechanisch. Sie journalisiert und bestätigt Aktivierungen
dauerhaft, bindet einen Run exklusiv sowie terminale Evidence und finale Checks, startet aber nie Agent, Run,
Branch oder Worktree. Ohne
Program beziehungsweise Project Delta entsteht kein zusätzlicher Modell-Kontext. Details:
[`references/program-v1.schema.json`](references/program-v1.schema.json) und
[`reference.md`](reference.md#optional-project-continuity-and-program-scheduling).

### Optionale projektspezifische Release-Profile

Ein ausdrückliches `kimiflow release` oder „Release Flow“ kann ein lokales, provider-neutrales Profil unter
`.kimiflow/release/` verwenden. Kimiflow erkennt getrackte Release-Controls, bindet ein Modell-Audit an deren
exakte Digests und verwendet es weiter, bis sich ein Control ändert oder ein echter Release-Fehler auftritt.
Ein Auftrag autorisiert genau einen seriellen Release-Lauf; mutierende Schritte brauchen mechanische Vor- und
Nachbedingungen, unklare Effekte werden nie blind wiederholt und projektspezifische Final-Checks müssen
bestehen. Nach echten Fehlern muss das neue Audit an den exakten Fehlerbeleg gebunden sein.
Audit-Verbesserungen bleiben evidenzgebundene Hinweise und verändern keine laufende Veröffentlichung.
Ohne Release-Auftrag wird kein Profil-Kontext geladen.

Schema-v2-Profile ergänzen typisierte öffentliche Laufzeit-Inputs, projekt- und zielgebundenes privates
Release-Memory, kurzlebige Provider-Identität, granulare Retry-Klassen und die exakte Wiederverwendung von
Phase-6-Evidence. Nicht-GitHub-Releases verwenden die generische `environment`-Identität mit ausschließlich
projektseitig deklarierten kurzlebigen Credentials und müssen mindestens ein öffentliches Publikationsziel
deklarieren, damit ein Wechsel zwischen Registry, App Store oder internem Ziel keine fremde Memory übernimmt.
Der Release-Effekt muss dieses Ziel explizit verwenden. GitHub ist ein optionaler Adapter und bevorzugt ein natives
Token. Der lokale GitHub-Fallback verwendet den durch einen
erfolgreichen Release bestätigten Account weiter (beim ersten Lauf alternativ den Autor des letzten Releases),
validiert die Schreibberechtigung für das Repository erneut und schaltet den globalen `gh`-Account nicht um.
Credentials, rohe Inputs, Kommandoausgaben und absolute Pfade
werden nie gespeichert. Credential-haltige temporäre Homes liegen zwingend außerhalb des Projekts, werden
durch einen unabhängigen Controller-Death-Wächter bereinigt und nach einem Host-Neustart über lokale Leases
wieder eingesammelt.
Provider-Output wird schon während der Ausführung begrenzt und `env`-Wrapper dürfen die versiegelte
HOME-/XDG-/Provider-Konfiguration nicht ersetzen. Die interne Repository-Erkennung verwendet ein festes
System-Git statt des umgebenden `PATH`. Mit `relative_path`, statischen lokalen Effect-Argumenten oder
Affected-Paths benannte Laufzeit-Artefakte durchlaufen vor einem Effekt einen begrenzten eingebauten
Credential-Scan. Verzeichnisinhalt und Bytes sind snapshot-gebunden; ZIP-/tar-Inhalte werden geprüft, während
unsichere, verschlüsselte, verschachtelte oder nicht unterstützte Container fail-closed stoppen. Verdächtige
Unterpfade, bekannte Token-Formen und das aktuelle kurzlebige Credential stoppen, ohne Inhalte zu speichern.
Nicht verwendete Release-Inputs
wie ein neuer Tag invalidieren
einen davon unabhängigen Check nicht; jedes vom Kommando tatsächlich verwendete Relative-Path-Input, seine
betroffenen Bytes, Umgebung und adoptierten externen Tool-Fingerprints müssen weiterhin exakt übereinstimmen.
Unveränderte Releases überspringen damit wiederholte Discovery, Audits, Modellarbeit
und bereits aktuelle Checks. Innerhalb einer unterbrochenen Generation werden abgeschlossene
nicht-authentisierte Checks nur bei weiterhin identischem Pfad-, Umgebungs-, PATH- und Tool-Kontext
wiederverwendet; authentisierte Checks laufen stets erneut. Das Release-Memory wird datei- und
verzeichnis-fsynced idempotent vor dem Completed-Marker geschrieben und bei einem alten abgeschlossenen Lauf
mit fehlendem oder malformed Memory repariert, ohne Projektarbeit zu wiederholen. Echte Projektprüfungen,
Builds und Provider-Schritte laufen weiterhin, sobald
Evidence fehlt oder veraltet ist. Provider-authentisierte Checks werden nie wiederverwendet; sie laufen immer
mit der aktuellen kurzlebigen Release-Identität. Lokale inhaltsfreie Metriken trennen Control-, Check-, Build-
und Provider-Arbeit—ein fixes Release-Zeitbudget gibt es bewusst nicht. Siehe
[`references/release-profile-v2.schema.json`](references/release-profile-v2.schema.json).
`kimiflow release` meldet ein bestehendes bereites v1-Profil einmalig als `upgrade_required` und leitet den
tatsächlichen Provider aus getrackten Controls ab; ein aktiver v1-Lauf wird zuerst sicher beendet. Direkte
v1-Ausführung bleibt kompatibel.

## Demo

![Kimiflow-Launcher und gegateter Feature-/Fix-Flow](docs/demo/kimiflow.gif)

> Geskriptete Illustration des aktuellen Launchers und Kern-Flows. Quelle und Anleitung für einen
> echten Mitschnitt liegen unter [`docs/demo/`](docs/demo/).

## Modi

Die Modi funktionieren mit `/kimiflow` in Claude Code und `$kimiflow` in Codex gleich.

| Modus | Zweck |
|---|---|
| `kimiflow full` | Strenger Large-Flow; pausiert nur für eine wesentliche Entscheidung. |
| `kimiflow quick` | Schlanker Weg für kleine, risikoarme Änderungen. |
| `kimiflow fix` | Erst diagnostizieren, dann begrenzt fixen und Red/Green verifizieren. |
| `kimiflow grill` | Nur den Auftrag klären; kein Plan und kein Code. |
| `kimiflow plan` | Intent, Recherche, Plan und Akzeptanzkriterien vorbereiten; kein Code. |
| `kimiflow build` | Einen freigegebenen vorbereiteten Plan umsetzen. |
| `kimiflow review` | Bestehendes Feature oder aktuellen Diff read-only prüfen. |
| `kimiflow audit` | Cleanup-/Refactoring-Audit vor Auswahl eines Slices. |
| `kimiflow release` | Bei Bedarf importieren/neu auditieren und genau einen Projekt-Release ausführen. |

Explizite Formen:

```text
/kimiflow <feature-oder-bug>
/kimiflow --fix <bug>
/kimiflow --verify-feature <feature-oder-pfad>
/kimiflow <auftrag> --prepare
/kimiflow --resume <slug>
/kimiflow --project-map quick
/kimiflow release
```

Kimiflow belegt zuerst, woher Produktziel, Nutzer, sichtbares Verhalten, Grenzen und Erfolgskriterien
kommen. Jedes neue nicht-triviale Feature erhält vor Planung und Projekt-Writes einen kompakten Product
Intake; bei einem bereits vollständigen Auftrag wird der kurze Produktvertrag bestätigt statt mit
Füllfragen verlängert. Der User entscheidet WHAT/WHY, der Agent Architektur, Libraries, Datenmodell,
Tests und anderes technisches HOW. Danach wird der Vertrag gesperrt und der Flow läuft autonom weiter.
Ein zweiter Fragenblock ist nur erlaubt, wenn die erste Antwort selbst einen neuen materiellen
Produktkonflikt erzeugt. Fixes und exakt triviale Arbeit behalten ihre direkten Routen.

## Acht Phasen

| Phase | Ablauf |
|---|---|
| 0 Setup | Alle Worktrees inventarisieren, dauerhaften Run-State anlegen, sichere Aufräumentscheidung einmal bündeln. |
| 1 Klären | Für nicht-triviale Features den verpflichtenden Product Intake durchführen, HOW-Fragen verbieten, den bestätigten Vertrag sperren und weiterlaufen. |
| 2 Verstehen | Projektwissen und Code prüfen; Discovery `none`, `pulse` oder `focused` wählen und die Architektur-Machbarkeit vor dem Plan belegen. Fixes reproduzieren und belegen die Ursache. |
| 3 Planen | Flachen minimum-complete Plan, testbare Akzeptanzkriterien und höchstens fünf belegte Umsetzungsentscheidungen schreiben. |
| 4 Review | Plan-Blocker lösen; nur bei Autorität, materiellem Scope/Risiko, Privacy/Kosten oder Irreversibilität pausieren. |
| 5 Umsetzen | Kleinste akzeptierte Änderung bauen; Fixes sichern Red-Evidence vor Production-Code. |
| 6 Verifizieren | Akzeptanz, Regression und die Übereinstimmung des realen Diffs mit Strategie und Invarianten prüfen. |
| 7 Review und Commit | Conformance erneut prüfen, Findings verifizieren, den Named-Path-Commit erstellen und danach Commit/Index/Worktree-Gleichheit belegen; Push/Release bleiben explizit. |

## Mechanische Gates

„Mechanisch“ bedeutet: Ein getestetes Skript oder ein Hook entscheidet, nicht ein Selbstbericht.

| Gate | Gesicherte Grenze |
|---|---|
| Workspace-Preflight | Alle Worktrees und Dirty-Pfade werden klassifiziert; bis zu drei eigene Fleet-Trees erhalten exklusive Leases, Revalidierung, serialisierte Candidate-first-Integration und Ancestry-gesichertes Archivieren. |
| Product-Intake-/Clarify-/Discovery-Gates | Unterstützte Planung und Writes bleiben bis zu einer expliziten Produktantwort gesperrt; gesperrter Intent, null technische Fragen, Machbarkeit und Quellen-/Scope-/Entscheidungs-Evidence müssen vor dem Plan stimmen. |
| Plan-/Review-Gates | AC-Mapping und belegte BLOCKER/HIGHs werden in begrenzten Reparaturrunden gelöst. |
| Implementation-Conformance-Gate | Rechercheentscheidungen, Invarianten, Pfade, Checks und jede gesperrte Produktanforderung konvergieren in Phase 6; beim Abschluss muss zusätzlich der Commit exakt dem geprüften Stand entsprechen. |
| Adaptiver Execution-Controller | Run-weites No-Progress und Budgetdruck wählen eine begrenzte Recovery-Aktion; verpflichtende Qualitäts-Gates bleiben erhalten. |
| Evidence-Evaluation | Vier kritische Flow-Verhalten laufen in CI genau einmal gegen eine versiegelte Baseline des vorherigen Releases; Artefakte enthalten nur begrenzte Metadaten und Digests, nie Prompts, Output, Code, Secrets oder absolute Pfade. |
| Lokale Run-Steuerung | Hosts erhalten einen Readiness-/Cursor-Vertrag; gemeinsames Locking, Owner-Nachweis und Action-Receipts machen unterstützte Item-Mutationen fail-closed und replay-sicher. |
| Materielle-Entscheidungs-Gate | Reversible Technik läuft weiter; nur Autorität, Risiko, Zugriff, Privacy/Kosten oder Irreversibilität pausieren. |
| Red/Green-Gate | Fixes brauchen aufgezeichnete failing/passing Evidence und Regression. |
| Atomic-Commit-Gate | Schema-4-Runs stagen Named Run-Paths und committen lokal unter der ursprünglichen Bau-Freigabe. |
| Secret-/State-Hooks | Verdächtige Pfade, Bulk-Staging und Resolver ohne dauerhaften State werden blockiert. |
| Test-Gate | Large-Runs können Abschluss blockieren, solange der konfigurierte Test rot ist. |

Scope, Root-Cause-Qualität und Vollständigkeit der Reviewer bleiben Modellurteile. Kimiflow
mechanisiert die Evidence-Grenzen, ohne Allwissenheit vorzutäuschen.

## Tokeneffiziente Skalierung

- `trivial`: exakte risikoarme Arbeit; kurz umsetzen, verifizieren und lokal committen.
- `small`: Default; kompakte Klärung, adaptive Discovery, ein Planner, begrenztes Review.
- `large`: nur für breite Änderungen, neue Dependencies, Migrationen, Security/Privacy/Money,
  subtile Bugs oder explizites `full`.
- Discovery startet für `none|pulse` keinen Worker, für `focused` normalerweise einen begrenzten
  Evidence-Worker und höchstens zwei unabhängige Lanes.
- Recherche darf die Umsetzung korrigieren; nur `required` Constraints dürfen Scope hinzufügen.
- Conformance speichert höchstens fünf materielle Entscheidungen; `small` braucht keinen zusätzlichen Modell-Call, `large` nutzt den bestehenden unabhängigen Verifier mit.
- Execution nutzt drei feste Qualitätsprofile mit expliziter Auswahlbegründung und einen kompakten lokalen Trace; bei hartem Druck fällt optionale Breite weg, nicht Verifikationsqualität.
- Ein zweiter Planner erscheint nur bei echter Architektur- oder irreversibler Contract-Gabel.
- Das Top-Modell behält Orchestrierung, Synthese, Planung, Review-Verdicts und riskante Diagnose.
- Ein deterministischer Classifier erhöht den Scope anhand von Subsystem-, Daten-, Security-, Integrations- und
  Irreversibilitäts-Evidence, beantwortet aber niemals eine offene Produktentscheidung.
- Große, materiell veränderte Kontexte rollen nur bei gemessenem Druck um; kleine Runs bleiben unverändert.
- Günstigere Modellrouten werden erst nach fünf vergleichbaren sauberen Outcomes freigeschaltet, bei Regression
  entzogen und nie für kritische Arbeit verwendet.
- Die deterministische Behavior-Evaluation braucht keinen Modell-Call. Modellbewertete Kalibrierung wird nur
  als nicht-ausführender Release-Plan abgebildet und bleibt außerhalb normaler Runs und CI.

`small` und `quick` überspringen breiten Memory-Recall und den **Vault Pulse** standardmäßig. Ein
ausdrücklicher Hinweis, dass ein ähnlicher Bug oder Fix schon existierte, löst stattdessen bei jedem
Scope genau einen gezielten lokalen Recall mit höchstens fünf Treffern und ohne Provider-Suche aus.
Current-State-Checks und Learning-Review bleiben bei jedem nicht-trivialen Run erhalten.

Domänenkomplexität und Betriebswirkung sind getrennte konditionale Verträge. Wenn aktiv, braucht jeder eine
typisierte Research-Zeile, einen AC-verknüpften Plan-Check und passende Verification-Evidence. Wenn inaktiv
entsteht kein zusätzlicher Prompt-Overhead.

## Projektwissen und Memory

Kimiflow kann unter `.kimiflow/project/` eine lokale Projektkarte mit Codebase-, Architektur-,
Konventions-, Test- und Flow-Evidence anlegen. Spätere Runs prüfen betroffene Bereiche und erneuern nur
stale Abschnitte. Die Map ist optional, lokal und blockiert normale Arbeit nicht.

Der Memory Router speichert begrenzte Projektfakten, Entscheidungen, Standards, Run-Historie und
evidence-basierte Learnings. Neue projektlokale Learnings beginnen als `probationary`: Sie bleiben
gezielt abrufbar, gelangen aber noch nicht in Always-on-Memory, Vorschläge, Provider-Sync oder portable
Capsules. Erst zwei verifiziert hilfreiche Anwendungen bei weiterhin exakter Source-Evidence machen sie
`durable`; diese Anwendungen sind zusätzlich an einen Fingerabdruck desselben Learning-Inhalts gebunden, sodass
umgeschriebener Inhalt keinen alten Erfolg erbt. Widerspruch, Inhalts- oder Evidence-Drift stuft Vertrauen
reversibel zurück. Bestehende Zeilen ohne
Maturity-Feld behalten ihr bisheriges dauerhaftes Verhalten.
Der Fingerabdruck umfasst jedes Recall-sichtbare Feld außer expliziten Lifecycle-Metadaten, sodass auch ein
künftiges Feld keine frühere Verifikation stillschweigend erben kann.
Abgeschlossene Runs erhalten außerdem eine automatische lokale Outcome-Evaluation. Künftige passende
Runs sehen höchstens eine verifizierte Erfolgsstrategie und eine belegte Fehlstrategie; beide werden
gegen den aktuellen Code erneut geprüft.
Recall packt Memory, Fakten, Learnings, Strategien und Historie nun in ein einziges globales
Context-Budget und ein globales Trefferlimit und entfernt quellenübergreifende Duplikate. Recall bleibt
immer ein Hinweis: aktueller Code, Tests, Specs und Primär-Evidence gewinnen. Der optionale SQLite-Index
wird nur mit aktuellem Source-Fingerprint verwendet; stale Indizes werden ignoriert und bei einem
persistierten Recall atomar neu gebaut.
In großen Monorepos leitet Run-Artefakt-Recall aus den betroffenen Dateien höchstens acht verschachtelte
Package-Einheiten ab und reiht deren Evidence zuerst. Root-Regeln und Evidence ohne nachweisbare Package-Grenze
bleiben global; ungültige, gemischte, zu große oder während des Recalls veränderte Grenzen führen sicher zu
projektweitem Recall. Dafür gibt es nur begrenzte Ancestor-Checks—keinen Repo-Scan, Dependency-Graph,
Worktree-Write, Netzwerkzugriff oder User-Gate.
Finale Treffer erhalten außerdem stabile lokale IDs. Kimiflow zählt eine ID nur dann als verwendet, wenn
sie tatsächlich eine Plan-Entscheidung prägt, verbindet sie mit der Verifikation und bewertet sie im
bestehenden Outcome-Artefakt als `helpful`, `neutral` oder `contradicted`. Dafür entstehen weder externe
Telemetrie noch kopierte Recall-Texte oder eine neue User-Bestätigung.

Memory-Pflege ist Preview-first und reversibel. `memory-router.sh lifecycle` erklärt einen begrenzten
Utility-Score von 0–5, Promotionen, Rückstufungen und Quarantäne-Kandidaten. Erst nachdem der
Terminal-Status atomar geschrieben und die Outcome-Auswertung erfolgreich persistiert wurde, führt
Kimiflow diese Kuratierung automatisch und modellfrei aus und schreibt nur einen kompakten
`memory_curation`-Beleg. Eine kooperative 20-Sekunden-Deadline rollt die auf 8 MiB und 4096 Quellzeilen
begrenzte Learning-/Text-Derivate-Transaktion vor dem 30-Sekunden-Host-Timeout zurück und wird beim erfolgreichen Derivate-Commit deaktiviert; Timeout oder andere
Curation-Fehler bleiben sichtbar, halten den abgeschlossenen
Run aber nicht an. Writes ohne Änderung bleiben byte-identisch.
`lifecycle --write` wertet dafür das bestehende verifizierte Outcome-Ledger strikt und begrenzt aus und
quarantänisiert stale Zeilen nur, wenn sie nachweislich nie verwendet wurden und eine eindeutige ID haben.
Fehlende versiegelte Recall-Evidence sperrt die Kuratierung; eine fehlgeschlagene Outcome-Persistenz lässt
den Run für einen autonomen Abschlussversuch wiederaufnehmbar. Producer und Lifecycle teilen sich einen lokalen
Ledger-Lock, lehnen Duplicate-Keys ab, zählen jeden Run nur einmal und verwenden die serialisierte Ledger-Reihenfolge
statt veränderbarer Zeitstempel für die Vertrauenskausalität. Persistierter Recall und Lifecycle teilen zusätzlich
den Usage-Ledger-Lock mit derselben physischen Identität auch über Root-Aliase, sodass ein gerade verwendetes
Learning nicht gleichzeitig quarantänisiert werden kann.
Durable Candidates gewinnen bereits vor dem begrenzten Candidate-Fenster gegen bloße Lokalität.
Der atomare Pfad-Exchange prüft Identität/Mode der verdrängten Quelle
und den installierten Candidate; begrenzte Re-Exchanges befördern spätere Writer, ohne den kanonischen Pfad zu
entfernen. Ein ungelöster Race behält eine lokale Recovery-Kopie. Nicht verfügbare native Exchange-Unterstützung
sperrt den Write vor der Mutation. `lifecycle --restore <id> --write` stellt genau eine Zeile nur
bei weiterhin exakter Evidence wieder her.
Für optionale projektübergreifende Übergaben erzeugt `capsule --write` eine lokale Mode-0600-Privacy-Capsule
mit höchstens 20 frischen, erlaubten Sechs-Feld-Projektionen. Vault-Sync nutzt dieselbe Projektion und
exportiert weder Source-IDs, Pfade, Evidence-Referenzen, Credential-/JWT-Formen, E-Mails, private/security Zeilen noch
unsichere Inhalte.
Der Outcome-Writer hält die neuesten vollständigen Zeilen unterhalb des strengeren Lifecycle-Limits, damit lange
laufende Projekte keine manuelle Ledger-Bereinigung benötigen.

Ein Obsidian Vault ist optional. Ohne ihn funktionieren lokales Memory und alle Gates weiter. Mit
authentifizierten Vault-MCP-Tools kann Kimiflow kuratierte, nicht-private projektübergreifende
Learnings abrufen oder exportieren. API-Keys landen nie in `.kimiflow/`.

Vault-Reads sind an den Projekt-Namespace gebunden: Fremde Projektpfade und unsichere Felder werden vor der
Run-Injektion verworfen, danach lokal dedupliziert und begrenzt. Alte terminale Run-Artefakte können nach einer
sicheren Frist einzeln archiviert werden; aktive Runs und Learnings sind nie Retention-Kandidaten.

Details: [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) und
[`reference.md`](reference.md#vault-conventions-phase-2).

## Workspace-Sicherheit und Resume

Ein aktiver Run speichert seine Codex- oder Claude-Owner-Session. Andere Sessions dürfen lesen,
diskutieren und planen. Vor Writes inventarisiert Kimiflow alle Checkouts. Ein sauberes/freies Primary
bleibt ohne Fleet-State direkt; bei dirty oder busy Primary entstehen automatisch bis zu drei gesperrte,
eigene `codex/<slug>*`-Worktrees. Weitere Runs warten FIFO ohne Bestätigungsfrage. Phase 3 bindet Pfade
und Contracts an die exakten PLAN-Bytes und vergibt exklusive Primary-/Fleet-Leases; `blocked_by`
nennt den Gewinner. Fremde ignorierte Dateien machen einen ansonsten sicheren
Checkout nicht busy; Kimiflow-eigene ignorierte Run-Artefakte bleiben beim Retirement erhalten, ohne
Ancestry-, Ownership- oder Integritätsprüfungen abzuschwächen. Phase 5 prüft den Write-Gate; nach jedem
Primary-Fortschritt ist eine Revalidierung nötig. Integration nutzt Merge-Tree-Preflight,
argv-basierte Projektchecks auf dem kombinierten Kandidaten vollständig vor der Mutation, bei Bedarf einen
Merge-Commit nur im eigenen Branch und Fast-forward-only für Primary; danach folgen nur mechanische
Git-Integritätsbelege. Erst terminale, grüne und per Ancestry belegte Trees werden crash-sicher
vollständig archiviert. Konflikte bleiben als `needs-reconcile` wiederaufnehmbar. Manuelle und
Codex-Worktrees bleiben unangetastet.

Vorbereitete und geparkte Runs lassen sich aus `.kimiflow/<slug>/` fortsetzen. Bei geänderten Dateien
oder unbekannter Plan-Basis wird vor der Umsetzung revalidiert.

## Sicherheitsgrenzen

- Kimiflow routet nur konkrete Umsetzungsauftraege fuer substanzielle Feature-Arbeit mit materiellem
  Cross-Surface-, Integrations-, Daten-, Security-, Public-API-, Architektur- oder Discovery-Bedarf
  automatisch. Diskussionen, Ideen, Empfehlungen, Erklaerungen, Statusfragen und Wunschformulierungen
  sind keine Bau-Freigabe. Fixes, Reviews, Refactors, Cleanup, Doku/Config und kleine risikoarme
  Features bleiben ohne expliziten Aufruf direkt.
- Explizites `direct` oder `direkt` umgeht Kimiflow immer; ein expliziter Kimiflow-Aufruf startet es immer.
- `.kimiflow/` ist lokaler Run-State und wird standardmäßig nicht committed.
- Der Secret-Hook prüft verdächtige Pfade, nicht Inhalte; für Content-Secrets dient der Advisory Scan
  oder ein Tool wie gitleaks.
- Projektkarten und Repo-Doku veröffentlichen keine rohen Schwachstellen, Secrets, privaten Pfade oder
  Vault-Referenzen ohne explizit sanitisierte Notiz.
- Kimiflow ist pre-1.0; nach Host-Upgrades sollten die Compatibility-Checks erneut laufen.

## Dokumentation

- [`reference.md`](reference.md) - vollständiger Workflow- und Gate-Vertrag.
- [`COMPATIBILITY.md`](COMPATIBILITY.md) - Host-Primitives und Upgrade-Checks.
- [`docs/architecture.md`](docs/architecture.md) - Engine, Adapter, Hooks und Datenfluss.
- [`docs/codebase.md`](docs/codebase.md) - Repository-Map und Zuständigkeiten.
- [`docs/testing.md`](docs/testing.md) - lokale Checks, Smokes und CI.
- [`examples/`](examples/README.md) - Small Fix, riskanter Fix und Feature-Walkthrough.
- [`evals/`](evals/README.md) - deterministische Evidence-Checks und verhaltensbasierte Release-Kalibrierung.
- [`CHANGELOG.md`](CHANGELOG.md) - Release-Historie.

## Lizenz

[MIT](LICENSE)
