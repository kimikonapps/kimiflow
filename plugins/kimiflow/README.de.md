# Kimiflow

**Ein kleiner Arbeitsablauf für leistungsfähige Coding-Agenten und lokale Modelle.**

[English](README.md) · [Workflow](SKILL.md) · [Optionale Werkzeuge](references/optional-tools.md)

## Release 0.4.3

[Release und verifizierter Runtime-Download](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.4.3)

Der schlanke Ablauf ist jetzt Standard. Sechs doppelte CI-Aufrufe entfallen bei gleicher Abdeckung.
Der Code-Review hat zwei Fehler beseitigt: Weiterlaufende Kindprozesse verhindern nun ein grünes
Prüfergebnis; Headless-Wiederanläufe bleiben beim benötigten Legacy-Vertrag. Nach dem Plugin-Update
eine neue Host-Sitzung starten. Ein Git-Pull allein ersetzt keinen installierten Plugin-Cache.

Neue Aufgaben laufen in drei Schritten: **Verstehen → Bauen → Prüfen und liefern**.
Der gewählte Agent nutzt die vorhandenen Host-Werkzeuge. Ein eindeutiger Auftrag braucht keine zweite
Routinebestätigung, acht Phasen, Lesebelege, feste Reviewer-Besetzung oder verpflichtende Memory-Pflege.
Für lange Aufgaben reicht bei Bedarf eine lokale `NOTES.md` mit Ziel, Entscheidungen, offenen Punkten
und Prüfresultaten. Aktueller Code ist wichtiger als die Notiz.

GPT-6 Astra und Fable 5.1 bilden den aktuellen Frontier-Ausgangspunkt. Kimiflow erzwingt diese Anbieter
nicht: Modell und Effort bleiben beim Host bzw. Nutzer. Lokale Modelle erhalten denselben schlanken
Ablauf, bei Bedarf mit kleineren Aufgaben und kompaktem Kontext. Kein automatischer Wechsel zu einem
kostenpflichtigen Dienst und kein zusätzlicher API-Key.

## Installation und Nutzung

Die Befehle für Claude Code und Codex stehen in der [englischen Installationsanleitung](README.md#install).
Nach Plugin-Updates den Host neu starten; der installierte Cache aktualisiert sich nicht durch eine
Änderung am Source-Checkout.

`/kimiflow` bzw. `$kimiflow` ohne Ziel zeigt den Projektstatus. Konkrete Aufträge starten direkt.
`full` und `quick` unterscheiden Umfang und Prüftiefe, nicht die Autorisierung. `grill`, `plan`, `review`
und `audit` bleiben zunächst ohne Produktänderungen. `build` setzt einen vorhandenen Plan um; `fix`
reproduziert zuerst. `release` aktiviert ausdrücklich den optionalen Release-Ablauf.

## Prüfen statt behaupten

`hooks/check-change.sh` führt explizite Projektbefehle als argv-Arrays aus, ohne implizite Shell:

```bash
/absoluter/plugin/pfad/hooks/check-change.sh --root /absolutes/projekt \
  --check '["python3","-m","unittest","discover"]'
```

Ein fehlgeschlagener Check beendet die Prüfung. Unterschiede an HEAD, Index oder Quelldateien zwischen den Snapshots vor und nach einem Check verhindern ein grünes Ergebnis. Ausgabe und Laufzeit sind begrenzt. Der Helfer schreibt
keinen Zustand, startet kein Modell und macht keinen Commit. Auf POSIX werden überlebende Mitglieder
der Check-Prozessgruppe beendet und als unvollständiger Check gemeldet. Checks müssen ihre Arbeit
synchron abschließen; abgetrennte Prozesse außerhalb der Gruppe und zwischen Snapshots zurückgesetzte
Änderungen kann der Helfer nicht beobachten. Build-Ausgaben unter Git-ignore und lokale
ungetrackte `.kimiflow/`-Notizen zählen nicht als Quellcode; getrackte Dateien immer.

Unabhängiger Review richtet sich nach konkretem Risiko. Fremde Änderungen und Staging bleiben geschützt;
Commits betreffen nur benannte eigene Pfade. Veröffentlichung und irreversible Aktionen benötigen ihre
eigene Autorisierung. Modellurteile und tatsächlich ausgeführte Tests sind verschiedene Belege.

## Bestehende Runs und optionale Funktionen

Vorhandene Runs mit `Flow schema` behalten ihre gepinnten Gates. `--resume <slug>` lädt dafür den
[Legacy-Workflow](references/legacy-workflow.md). Headless-Controller und verwaltete FirstMate-Crews
nutzen ihn ebenfalls. Aktive Zustände und gespeicherte Learnings werden nicht umgeschrieben oder gelöscht.

Projektkarten, Memory/Vault, Fleet, Security, Solution Search und Projekt-Releases bleiben
[ausdrücklich wählbare Werkzeuge](references/optional-tools.md), keine Pflicht für neue Aufgaben.
Die alte same-pass Prosa-Anleitung nach [no-ai-slop](https://github.com/petergyang/no-ai-slop) benötigt
keinen weiteren Modellaufruf und ist kein AI-Urheberschaftsdetektor. Neue Aufgaben laden sie nicht.

Die [vorhandenen Vergleiche](evals/outcomes.md) liefern noch keinen belastbaren Qualitätsvorsprung.
Weniger Prompt-Bytes sind messbar; echte Zeit- und Tokenersparnis muss mit fairen Modellläufen geprüft
werden. [Tests](docs/testing.md) · [Architektur](docs/architecture.md) · [MIT](LICENSE)
