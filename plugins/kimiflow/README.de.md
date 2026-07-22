# kimiflow

**Ein tokeneffizienter Feature- und Bugfix-Flow mit mechanischen Gates für Claude Code und Codex.**

[English](README.md) | [Workflow-Referenz](reference.md) | [Beispiele](examples/README.md) | [Kompatibilität](COMPATIBILITY.md)

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

Explizite Formen:

```text
/kimiflow <feature-oder-bug>
/kimiflow --fix <bug>
/kimiflow --verify-feature <feature-oder-pfad>
/kimiflow <auftrag> --prepare
/kimiflow --resume <slug>
/kimiflow --project-map quick
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
| Workspace-Preflight | Alle Worktrees und Dirty-Pfade werden klassifiziert; ein eigener Ausnahme-Tree wird vollständig archiviert statt gelöscht. |
| Product-Intake-/Clarify-/Discovery-Gates | Unterstützte Planung und Writes bleiben bis zu einer expliziten Produktantwort gesperrt; gesperrter Intent, null technische Fragen, Machbarkeit und Quellen-/Scope-/Entscheidungs-Evidence müssen vor dem Plan stimmen. |
| Plan-/Review-Gates | AC-Mapping und belegte BLOCKER/HIGHs werden in begrenzten Reparaturrunden gelöst. |
| Implementation-Conformance-Gate | Rechercheentscheidungen, Invarianten, Pfade, Checks und jede gesperrte Produktanforderung konvergieren in Phase 6; beim Abschluss muss zusätzlich der Commit exakt dem geprüften Stand entsprechen. |
| Adaptiver Execution-Controller | Run-weites No-Progress und Budgetdruck wählen eine begrenzte Recovery-Aktion; verpflichtende Qualitäts-Gates bleiben erhalten. |
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

`small` und `quick` überspringen breiten Memory-Recall und den **Vault Pulse** standardmäßig. Ein
ausdrücklicher Hinweis, dass ein ähnlicher Bug oder Fix schon existierte, löst stattdessen bei jedem
Scope genau einen gezielten lokalen Recall mit höchstens fünf Treffern und ohne Provider-Suche aus.
Current-State-Checks und Learning-Review bleiben bei jedem nicht-trivialen Run erhalten.

## Projektwissen und Memory

Kimiflow kann unter `.kimiflow/project/` eine lokale Projektkarte mit Codebase-, Architektur-,
Konventions-, Test- und Flow-Evidence anlegen. Spätere Runs prüfen betroffene Bereiche und erneuern nur
stale Abschnitte. Die Map ist optional, lokal und blockiert normale Arbeit nicht.

Der Memory Router speichert begrenzte Projektfakten, Entscheidungen, Standards, Run-Historie und
evidence-basierte Learnings. Promotion erfolgt erst nach erfolgreicher Verifikation und
Source-Freshness-Prüfung. Geänderte Evidence ersetzt alte Learnings.
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
Utility-Score von 0–5; `lifecycle --write` quarantänisiert nur strikt gelesene stale Zeilen, die nachweislich nie
verwendet wurden und eine eindeutige ID haben. Der atomare Pfad-Exchange prüft Identität/Mode der verdrängten Quelle
und den installierten Candidate; begrenzte Re-Exchanges befördern spätere Writer, ohne den kanonischen Pfad zu
entfernen. Ein ungelöster Race behält eine lokale Recovery-Kopie. Nicht verfügbare native Exchange-Unterstützung
sperrt den Write vor der Mutation. `lifecycle --restore <id> --write` stellt genau eine Zeile nur
bei weiterhin exakter Evidence wieder her.
Für optionale projektübergreifende Übergaben erzeugt `capsule --write` eine lokale Mode-0600-Privacy-Capsule
mit höchstens 20 frischen, erlaubten Sechs-Feld-Projektionen. Vault-Sync nutzt dieselbe Projektion und
exportiert weder Source-IDs, Pfade, Evidence-Referenzen, Credential-/JWT-Formen, E-Mails, private/security Zeilen noch
unsichere Inhalte.

Ein Obsidian Vault ist optional. Ohne ihn funktionieren lokales Memory und alle Gates weiter. Mit
authentifizierten Vault-MCP-Tools kann Kimiflow kuratierte, nicht-private projektübergreifende
Learnings abrufen oder exportieren. API-Keys landen nie in `.kimiflow/`.

Details: [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) und
[`reference.md`](reference.md#vault-conventions-phase-2).

## Workspace-Sicherheit und Resume

Ein aktiver Run speichert seine Codex- oder Claude-Owner-Session. Andere Sessions dürfen lesen,
diskutieren und planen. Vor Writes prüfen sie Pfadkonflikte. Die Umsetzung bleibt standardmäßig
sequenziell im aktuellen Worktree. Ein einzelner Ausnahme-Worktree braucht explizite Freigabe und
trusted Registrierung; ist er owned, terminal (`done`, `failed` oder `aborted`), clean und unlocked,
werden Checkout und passende Git-Metadaten ohne destruktives Git-Remove archiviert. `parked` bleibt
fortsetzbar; Codex-Worktrees bleiben app-owned.

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
- [`evals/`](evals/README.md) - verhaltensbasierte Release-Kalibrierung.
- [`CHANGELOG.md`](CHANGELOG.md) - Release-Historie.

## Lizenz

[MIT](LICENSE)
