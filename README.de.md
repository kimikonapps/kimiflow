# kimiflow

**Ein tokeneffizienter Feature- und Bugfix-Flow mit mechanischen Gates für Claude Code und Codex.**

[English](README.md) | [Workflow-Referenz](reference.md) | [Beispiele](examples/README.md) | [Kompatibilität](COMPATIBILITY.md)

Kimiflow ist ein bewusst aufgerufener Skill beziehungsweise Plugin-Flow mit acht Phasen: klären,
verstehen oder diagnostizieren, planen, reviewen, umsetzen, verifizieren, Code prüfen und committen.
Einfache Arbeit bleibt klein; wichtige Grenzen werden durch getestete Skripte und Hooks abgesichert,
statt vom Modell nur behauptet zu werden.

Kimiflow kann substanzielle Feature-Arbeit automatisch routen; Fixes und kleine risikoarme Aenderungen
bleiben direkt, sofern du nicht `/kimiflow` in Claude Code oder `$kimiflow` in Codex aufrufst.
Explizites `direkt` umgeht Kimiflow immer. Es antwortet in der Sprache, in der du schreibst.

## Warum Kimiflow

Claude Code und Codex können bereits planen, delegieren und reviewen. Kimiflow legt darum einen
dauerhaften, wiederaufnehmbaren Qualitätsvertrag:

- State und Evidence liegen unter `.kimiflow/<slug>/`;
- Plan- und Code-Review-Gates lösen BLOCKER/HIGH mechanisch auf;
- Bugfixes brauchen Reproduktion, belegte Ursache und Red/Green-Evidence;
- riskante Entscheidungen und Commits warten auf menschliche Freigabe;
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
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

Danach Codex neu starten und eine neue Task öffnen. Update:

```bash
codex plugin marketplace upgrade kimiflow
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

Der Hook-Installer schreibt verwaltete Wrapper nach `${CODEX_HOME:-~/.codex}/hooks`. Der stabile
Marketplace-Pfad verhindert, dass ein Update auf einen neuen versionierten Cache alte Wrapper bricht.

## Demo

![Kimiflow-Launcher und gegateter Feature-/Fix-Flow](docs/demo/kimiflow.gif)

> Geskriptete Illustration des aktuellen Launchers und Kern-Flows. Quelle und Anleitung für einen
> echten Mitschnitt liegen unter [`docs/demo/`](docs/demo/).

## Modi

Die Modi funktionieren mit `/kimiflow` in Claude Code und `$kimiflow` in Codex gleich.

| Modus | Zweck |
|---|---|
| `kimiflow full` | Strenger Large-Flow mit einer modusspezifischen Preview-Freigabe. |
| `kimiflow quick` | Schlanker Weg für kleine, risikoarme Änderungen. |
| `kimiflow fix` | Erst diagnostizieren, dann eine Fix Preview und Red/Green-Verifikation. |
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

Für `small` und `quick` gibt es keine Mindestzahl an Fragen. Ein vollständiger Auftrag braucht nur
eine kompakte Bestätigung; technische Lücken gehen in begrenzte Discovery statt in wiederholte
User-Interviews. Exakte triviale Arbeit darf den Loop überspringen.

## Acht Phasen

| Phase | Ablauf |
|---|---|
| 0 Setup | Modus und kleinsten gültigen Scope wählen, Worktree prüfen, State anlegen. |
| 1 Klären | Feature/Audit bestätigt Verhalten, Scope und Ergebnis. Ein klarer Fix schreibt den Problembrief ohne frühen Stopp. |
| 2 Verstehen | Projektwissen und Code prüfen; Discovery `none`, `pulse` oder `focused`. Fixes reproduzieren und belegen die Ursache. |
| 3 Planen | Flachen minimum-complete Plan und testbare Akzeptanzkriterien schreiben. |
| 4 Review | Plan-Blocker lösen. Features nutzen eine risikobasierte Build Preview; Fixes fragen einmal nach der Diagnose mit basisgebundener Fix Preview. |
| 5 Umsetzen | Kleinste akzeptierte Änderung bauen; Fixes sichern Red-Evidence vor Production-Code. |
| 6 Verifizieren | Akzeptanzkriterien, Regression, Red/Green und begrenzte lokale Diagnostics prüfen. |
| 7 Review und Commit | Findings verifizieren, Learnings kuratieren, Diff zeigen und vor dem Commit auf Freigabe warten. |

## Mechanische Gates

„Mechanisch“ bedeutet: Ein getestetes Skript oder ein Hook entscheidet, nicht ein Selbstbericht.

| Gate | Gesicherte Grenze |
|---|---|
| Working-Tree-Gate | Neue Write-Runs starten mit sauberem getracktem Worktree. |
| Clarify-/Discovery-Gates | Nötige Intent-, Quellen-, Scope- und Entscheidungs-Evidence existiert vor dem Plan. |
| Plan-/Review-Gates | AC-Mapping und belegte BLOCKER/HIGHs werden in begrenzten Reparaturrunden gelöst. |
| Fix-Preview-Gate | Freigabe wird an Problem, Diagnose, Plan, Acceptance und relevanten State gefingerprintet; Änderungen machen sie stale. |
| Red/Green-Gate | Fixes brauchen aufgezeichnete failing/passing Evidence und Regression. |
| Commit-Gate | Zeigt den finalen Diff und wartet vor dem Commit auf explizites OK. |
| Secret-/State-Hooks | Verdächtige Pfade, Bulk-Staging und Resolver ohne dauerhaften State werden blockiert. |
| Test-Gate | Large-Runs können Abschluss blockieren, solange der konfigurierte Test rot ist. |

Scope, Root-Cause-Qualität und Vollständigkeit der Reviewer bleiben Modellurteile. Kimiflow
mechanisiert die Evidence-Grenzen, ohne Allwissenheit vorzutäuschen.

## Tokeneffiziente Skalierung

- `trivial`: exakte risikoarme Arbeit; kurz umsetzen und verifizieren, dann Commit-Gate.
- `small`: Default; kompakte Klärung, adaptive Discovery, ein Planner, begrenztes Review.
- `large`: nur für breite Änderungen, neue Dependencies, Migrationen, Security/Privacy/Money,
  subtile Bugs oder explizites `full`.
- Discovery startet für `none|pulse` keinen Worker, für `focused` normalerweise einen begrenzten
  Evidence-Worker und höchstens zwei unabhängige Lanes.
- Recherche darf die Umsetzung korrigieren; nur `required` Constraints dürfen Scope hinzufügen.
- Ein zweiter Planner erscheint nur bei echter Architektur- oder irreversibler Contract-Gabel.
- Das Top-Modell behält Orchestrierung, Synthese, Planung, Review-Verdicts und riskante Diagnose.

`small` und `quick` überspringen breiten Memory-Recall und den **Vault Pulse**; beides läuft nur bei
`scope=large`. Current-State-Checks und Learning-Review bleiben bei jedem nicht-trivialen Run erhalten.

## Projektwissen und Memory

Kimiflow kann unter `.kimiflow/project/` eine lokale Projektkarte mit Codebase-, Architektur-,
Konventions-, Test- und Flow-Evidence anlegen. Spätere Runs prüfen betroffene Bereiche und erneuern nur
stale Abschnitte. Die Map ist optional, lokal und blockiert normale Arbeit nicht.

Der Memory Router speichert begrenzte Projektfakten, Entscheidungen, Standards, Run-Historie und
evidence-basierte Learnings. Promotion erfolgt erst nach erfolgreicher Verifikation und
Source-Freshness-Prüfung. Geänderte Evidence ersetzt alte Learnings.

Ein Obsidian Vault ist optional. Ohne ihn funktionieren lokales Memory und alle Gates weiter. Mit
authentifizierten Vault-MCP-Tools kann Kimiflow kuratierte, nicht-private projektübergreifende
Learnings abrufen oder exportieren. API-Keys landen nie in `.kimiflow/`.

Details: [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) und
[`reference.md`](reference.md#vault-conventions-phase-2).

## Parallele Tasks und Resume

Ein aktiver Run speichert seine Codex- oder Claude-Owner-Session. Andere Sessions dürfen lesen,
diskutieren und planen. Vor Writes prüfen sie Pfadkonflikte: disjunkte Dateien dürfen parallel,
überlappende oder unbekannte Pfade warten, werden enger gescoped oder nutzen einen Worktree.

Vorbereitete und geparkte Runs lassen sich aus `.kimiflow/<slug>/` fortsetzen. Bei geänderten Dateien
oder unbekannter Plan-Basis wird vor der Umsetzung revalidiert.

## Sicherheitsgrenzen

- Kimiflow routet nur substanzielle Feature-Arbeit mit materiellem Cross-Surface-, Integrations-,
  Daten-, Security-, Public-API-, Architektur- oder Discovery-Bedarf automatisch. Fixes, Reviews,
  Refactors, Cleanup, Doku/Config und kleine risikoarme Features bleiben ohne expliziten Aufruf direkt.
- Explizites `direkt` umgeht Kimiflow immer; ein expliziter Kimiflow-Aufruf startet es immer.
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
