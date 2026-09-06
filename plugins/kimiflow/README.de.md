# Kimiflow

Eine kurze Arbeitsvereinbarung für Coding-Agenten: Ziel klären, native Werkzeuge nutzen, Ergebnis prüfen.

[English](README.md) · [Workflow](SKILL.md) · [Release 0.5.0](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.5.0)

## Was Kimiflow bietet

Kimiflow hält Ziel, Erfolgskriterien, Grenzen und Projektkonventionen fest. Planung, Umsetzung,
Werkzeuge und Modellwahl bleiben beim Agenten und Host. Bestehende Tests und CI haben Vorrang.
Für lange Aufgaben gibt es bei Bedarf eine kurze Fortsetzungsnotiz statt einer eigenen Zustandsmaschine.

Kimiflow nutzt deine vorhandenen Werkzeuge. Kein API-Key und kein neuer Dienst sind erforderlich.
Astra, Fable 5.1 und lokale Modelle verwenden dieselbe Vereinbarung; bei begrenztem Kontext helfen
kleinere Aufgaben und präzise Befehle. Die öffentliche Versionsreihe beginnt mit **0.5.0**.

## Nutzung und Installation

`/kimiflow <Auftrag>` in Claude Code oder `$kimiflow <Auftrag>` in Codex. Pi lädt dieselben Grundregeln.
Keine Spezialmodi nötig: Beschreibe das gewünschte Ergebnis. `direkt` umgeht den Skill; reine
Einschätzungen bleiben ohne Umsetzung. Ohne Aufgabe wird nur nach dem Auftrag gefragt.

Installation: [Befehle in der englischen README](README.md#installation).
Nach Installation oder Update eine neue Aufgabe im Host starten, damit der Skill frisch geladen wird.
Für vorhandene Entwicklungsinstallationen siehe [Kompatibilitätshinweise](MIGRATION.md).

## Optionales Prüfwerkzeug

Wenn die vorhandenen Projektwerkzeuge ausreichen, direkt verwenden. Andernfalls kann der kleine Helfer
explizite argv-Checks ausführen und die Quellbasis vor/nach jedem Check vergleichen:

```bash
python3 /absoluter/plugin/pfad/scripts/check_change.py --root /absolutes/projekt \
  --check '["python3","-m","unittest","discover"]'
```

Den Beispielbefehl durch die tatsächliche Projektprüfung ersetzen. Der Helfer benötigt Git und Python
3.9+, schreibt keinen Zustand und verändert weder Staging noch Commits. Fehler, Timeouts oder
Unterschiede zwischen den Snapshots verhindern ein grünes Ergebnis. Auf POSIX werden überlebende
Mitglieder der Check-Prozessgruppe beendet. Das ist keine Sandbox oder kontinuierliche Überwachung;
abgetrennte Prozesse und zwischen Snapshots zurückgesetzte Änderungen kann der Helfer nicht erkennen.

## Nutzen messen

Native Agentenarbeit und diesen Kern mit gleichem Modell, Startzustand, Werkzeugen und Gesamtbudget
vergleichen. Akzeptanz, Restfehler, Unterbrechungen, Zeit und gemessene Nutzung entscheiden.
Die historischen Pilotdaten belegen keinen Vorteil; ein neuer Modellbenchmark wurde noch nicht ausgeführt.
[Evaluationsvorgehen](evals/README.md) · [Tests](docs/testing.md) · [MIT](LICENSE)
