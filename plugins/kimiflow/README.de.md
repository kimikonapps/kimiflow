# Kimiflow

Ein Betriebskit für planergeführte Arbeit mit Coding-Agenten, dazu eine kurze Arbeitsvereinbarung
für alles andere: Ziel klären, native Werkzeuge nutzen, Ergebnis prüfen.

[English](README.md) · [Workflow](SKILL.md) · [Release 0.6.2](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.6.2)

## Was Kimiflow bietet

- **Betriebskit.** `/kimiflow init` legt Projektdokumente in `.kimiflow/ops/` an: verbindliche Regeln,
  ständige Worker-Regeln, ein Handoff, eine Liste und Plan-Vorlagen. Ab der nächsten Sitzung liest die
  Planer-Sitzung sie und folgt dem [Betriebsmodell](references/operating-model.md). Das Kit ist
  projektneutral; die eigenen Regeln des Projekts stehen in dessen OPERATIONS.md.
- **Ablauf nach Risiko.** Datenschreibzugriffe, Migrationen, Sicherheit, öffentliche API und Löschungen
  bekommen den vollen Ablauf (Feature-Blatt → Plan → Audit → Worker → Review); UI-Arbeit einen kurzen
  Plan und ein Review; ein Bugfix beginnt mit einem fehlschlagenden Test. Gates gibt es nur dort, wo das
  Betriebsmodell sie nennt: das OK des Owners zum Feature-Blatt, ein Plan-Audit, wo das Risiko es
  verlangt, und der Owner-Punkte-Check vor einem Merge nach main.
- **Lieferhilfe außerhalb.** Ohne initialisiertes Projekt gilt die kurze Arbeitsvereinbarung: Ziel,
  Abnahme, Grenzen, relevante Projektkonventionen, projekteigene Checks.
- Kein Controller, kein Laufzustand, kein API-Key, kein neuer Dienst. Kimiflow besteht aus Dokumenten,
  Konventionen, einem Hilfsskript und in Claude Code zwei Hooks.

Die öffentliche Versionsreihe beginnt mit **0.5.0**.

## Schnellstart

1. Plugin installieren (siehe [Installation](#installation)).
2. Im Projekt in Claude Code `/kimiflow init` aufrufen (`/kimiflow:kimiflow init`, falls es einen
   anderen `/kimiflow`-Befehl gibt). Der Agent startet das Init-Skript, analysiert das Projekt und füllt
   die Projektteile von OPERATIONS.md. Änderungen an CLAUDE.md, AGENTS.md oder Einstellungen schlägt er
   vor und setzt sie nur mit deinem OK um.
3. Neue Sitzung starten. Ab dann beginnt jede Sitzung mit dem Kit.

## Betriebskit

`init` ermittelt den Haupt-Checkout (auch aus einem Worktree heraus) und legt an:

```text
.kimiflow/ops/
├── OPERATIONS.md          verbindliche Regeln in Kurzform; Regelverzeichnis; Rollen-/Modelltabelle
├── OPERATIONS-history.md  Gründe, Vorfälle und Flakes hinter den Regeln
├── rules-worker.md        ständige Regeln für Worker, Reviewer und Auditoren
├── HANDOFF.md             nur der aktuelle Stand und die nächsten Schritte
├── LIST.md                die eine Liste offener und zurückgestellter Punkte
├── LIST-done.md           erledigte Punkte
├── plans/                 Vorlagen: Feature-Blatt, Plan, Worker-Brief, Review-Brief
├── archive/               alte Handoffs und Pläne
├── research/              Erkenntnisse, die bleiben sollen
└── .kit.json              Kit-Version und Vorlagen-Hashes für update
```

- `init` überschreibt nie eine vorhandene Datei; ein zweiter Lauf ändert nichts.
- Es trägt `.kimiflow/` in Gits lokale Exclude-Datei ein, damit das Kit lokal bleibt und in allen
  Worktrees gilt. Es bricht ab, wenn das Repository Dateien unter `.kimiflow/` versioniert.
- Es bricht ab, wenn `.kimiflow/ops/` bereits `STATE.md` oder `NOTES.md` einer früheren Aufgabe enthält,
  und meldet Altläufe (`.kimiflow/<task>/STATE.md`), ohne sie anzufassen.

## Rollen und Modelle

| Rolle | Verantwortung |
| --- | --- |
| Planer (die Sitzung) | Features mit dem Owner formen, planen, Briefs schreiben, Ergebnisse abnehmen, committen. |
| Worker | Einen begrenzten Brief umsetzen und die genannten Checks ausführen. |
| Reviewer / Auditor | Plan oder Diff aus frischem Kontext prüfen. |
| Architekt | Berät selten bei schweren, übergreifenden Fragen; ändert nie etwas. |

Die OPERATIONS.md des Projekts enthält die Rollentabelle mit Modell und Effort je Rolle. Ein Worker darf
dasselbe Modell wie der Planer nutzen: Sein leerer Kontext spart bereits Tokens. Ein günstigeres
Worker-Modell ist eine Option, keine Pflicht. Der datierte [Modell-Leitfaden](references/models.md)
beschreibt die aktuellen Claude-Modelle, ihre dokumentierten Stärken und wie man sie briefet; er schließt
kein Modell aus. Aktuelle Verfügbarkeit und Preise entscheiden. Für die Kosten zählen Planung, Review und
Nacharbeit mit; günstigere Worker-Aufrufe allein belegen kein günstigeres Ergebnis. Details:
[Planner-/Worker-Vereinbarung](references/planner-worker.md).

In Claude Code bringt das Plugin die Agents `kimiflow:worker`, `kimiflow:reviewer` und
`kimiflow:architect` (ohne Edit/Write-Werkzeuge; nur lesend per Anweisung, Bash nur zum Lesen) mit. Sie haben kein eigenes Modell; der Planer übergibt beim
Start das Modell aus der Rollentabelle. Ein Subagent ohne Modellangabe erbt das Sitzungsmodell.
Ein gleichnamiger User-Agent hat Vorrang, deshalb das Präfix `kimiflow:` verwenden.

## Kit aktualisieren

`/kimiflow update` vergleicht die Kit-Dateien des Projekts mit den Vorlagen des installierten Plugins.
Standardmäßig schreibt es nichts und meldet jede Datei als new, safe, conflict, unchanged oder
unknown_base; conflict- und unknown_base-Dateien kommen mit Diff.

- `--apply-safe` kopiert neue Dateien und Dateien, die du nie bearbeitet hast; Konflikte nie.
- `--resolved <pfad>` markiert eine von Hand zusammengeführte Datei als abgeglichen.
- Die Kit-Version in `.kit.json` springt erst auf die Plugin-Version, wenn alle Dateien abgeglichen sind.

## Hooks und Git-Guard (Claude Code)

Das Plugin registriert zwei Hooks über `.claude-plugin/plugin.json`. Beide starten `python3` aus dem PATH.

- **Sitzungsstart.** In einem lokal initialisierten Projekt erhält jede Sitzung (Start, Fortsetzen,
  clear, compact; auch im Worktree) die absoluten Pfade der Kit-Dateien und die Lesereihenfolge:
  OPERATIONS.md, dann HANDOFF.md; LIST.md wird durchsucht, nicht ganz gelesen. Dazu kommt ein Hinweis,
  wenn das Kit älter als das Plugin ist und wenn seit der letzten Änderung von HANDOFF.md Commits
  dazukamen. Außerdem warnt er, wenn OPERATIONS.md größer als 12 KB oder HANDOFF.md größer als 6 KB ist.
- **Vertrauensprüfung.** Der Hook spricht nur für ein Kit, das `init` hier angelegt hat: Gits lokale
  Exclude-Datei enthält `.kimiflow/`, und keine Kit-Datei ist versioniert. Ein geklontes Repository
  kann seine eigenen Dateien so nicht zu Betriebsregeln machen.
- **Git-Guard.** Vor jedem Bash-Befehl blockiert er Massen-Staging (`git add -A`, `--all`, `.`, `:/`,
  `*`) und ein `git commit`, dessen Befehlstext eine AI-Attribution enthält, erkannt an Absenderadresse
  oder Produktlink eines AI-Werkzeugs. Eine menschliche Co-Autor-Zeile bleibt erlaubt. Der Guard ist nur
  in einem initialisierten Projekt aktiv.

## Codex und Pi

Codex und Pi erhalten nur die Skill-Texte: keine Hooks, keine Agents. Die Schritte `init` und `update`
des Skills starten dort dasselbe Hilfsskript. Sitzungen bekommen das Kit nicht automatisch; `init`
schlägt eine AGENTS.md-Zeile vor, die Codex auf `.kimiflow/ops/OPERATIONS.md` verweist.

## Nutzung

`/kimiflow <Auftrag>` in Claude Code oder `$kimiflow <Auftrag>` in Codex. Pi lädt dieselbe Vereinbarung.
Ein ausdrückliches `direct` / `direkt` umgeht den Ablauf des Skills; in einem initialisierten Projekt
gelten Session-Start-Kontext, Git-Guard und die Betriebsregeln aus OPERATIONS.md weiter, für jede Aufgabe.
Sonst bleibt die automatische Auswahl auf substanzielle, freigegebene
Features beschränkt; gewöhnliche Fixes, Reviews, Refactors und kleine Änderungen bleiben direkt, außer
sie werden ausdrücklich angefordert. Ohne Aufgabe wird nur nach dem Auftrag gefragt, ohne
Projektinventur.

Reine Leseanfragen bleiben ohne Änderung. Commit- und Veröffentlichungsregeln gehören Host und Projekt.
Der Einstieg mit einer Idee wie „Ich möchte X, damit Y“ steht in der [Feature-Anleitung](references/feature-work.md).

## Installation

Claude Code:

```text
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```

Codex:

```bash
codex plugin marketplace add kimikonapps/kimiflow
codex plugin add kimiflow@kimiflow
```

Nach Installation oder Update eine neue Aufgabe im Host starten, damit der Skill frisch geladen wird.
Für vorhandene Installationen siehe [Kompatibilität](COMPATIBILITY.md) und [Migration](MIGRATION.md).

## Optionales Prüfwerkzeug

Wenn die vorhandenen Projektwerkzeuge ausreichen, direkt verwenden. Andernfalls kann der kleine Helfer
explizite argv-Checks ausführen und die Quellbasis vor/nach jedem Check vergleichen:

```bash
python3 /absoluter/plugin/pfad/scripts/check_change.py --root /absolutes/projekt \
  --check '["python3","-m","unittest","discover"]'
```

Den Beispielbefehl durch die tatsächliche Projektprüfung ersetzen. `--check` lässt sich für mehrere
Checks wiederholen; `--timeout` setzt Sekunden je Check. Der Helfer benötigt Git und Python 3.9+,
schreibt keinen Zustand und verändert weder Staging noch Commits. Fehler, Timeouts oder Unterschiede
zwischen den Snapshots verhindern ein grünes Ergebnis. Ignorierte Build-Ausgaben und unversionierte
`.kimiflow/`-Notizen zählen nicht zur Quellbasis; versionierte Dateien schon. Auf POSIX werden
überlebende Mitglieder der Check-Prozessgruppe beendet, und der Check schlägt fehl.

Das ist keine Sandbox, keine kontinuierliche Überwachung und kein Nachweis der Testqualität.
Abgetrennte Prozesse und zwischen Snapshots zurückgesetzte Änderungen kann der Helfer nicht erkennen.
Checks müssen synchron abschließen. Wenn Host oder CI schon ausreichend prüfen, den Helfer nicht nur
aus Prinzip ergänzen.

## Hilft es?

Das ist eine empirische Frage, und sie ist noch nicht beantwortet. Einen Benchmark des Betriebskits gibt
es nicht. Ein fairer Vergleich führt dieselben Aufgaben mit und ohne Kit aus, mit gleichem Modell,
Startzustand, Werkzeugen, Rechten und Gesamtbudget, und bewertet Abnahme, Restfehler, Unterbrechungen,
Zeit und gemessene Nutzung. Das [Evaluationsvorgehen](evals/README.md) beschreibt den Aufbau. Die
vorhandenen Entwicklungs-Piloten belegen keinen Qualitäts- oder Kostenvorteil.

## Entwicklung

```bash
python3 scripts/render_skills.py
python3 scripts/build_plugin.py
python3 -m unittest discover -s tests -v
python3 scripts/build_plugin.py --check
```

Das Paket hat eine explizite Allowlist; Tests, Entwicklungswerkzeuge, Verläufe und Nutzerdaten werden
nicht ausgeliefert. Siehe [Architektur](docs/architecture.md), [Codebasis](docs/codebase.md),
[Tests](docs/testing.md) und [Changelog](CHANGELOG.md).

[MIT-Lizenz](LICENSE)
