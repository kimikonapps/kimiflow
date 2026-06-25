# Kimiflow Project Intelligence Design

Date: 2026-06-25
Language: de
Status: draft for review

## Ziel

Kimiflow bekommt eine mehrstufige Projektintelligenz-Schicht. Sie analysiert eine Codebase, versteht ihre Architektur, kann daraus optionale Verbesserungen ableiten und schreibt wiederverwendbare Dokumentation. Der erste Nutzen ist Coding-Vorbereitung: Bugfixes und Features starten mit einer belegten Projektkarte statt mit wiederholter Repository-Erkundung.

Die Funktion wird in drei Slices gebaut:

1. Project Map Bootstrap
2. Staleness + Delta Refresh
3. Vault, Repo-Doku und Improve-Linsen

Die Projektkarte ist empfohlen, aber keine Voraussetzung. User koennen sie bei jedem kimiflow-Lauf anlegen oder aktualisieren lassen, muessen es aber nicht.

## Leitprinzipien

- Erst analysieren, dann verstehen, dann optional verbessern, dann dokumentieren.
- `.kimiflow/project/` ist immer der lokale Source of Truth.
- Obsidian Vault und Repo-Doku sind zusaetzliche Speicherebenen, nie Voraussetzung.
- Alle menschlich lesbaren Artefakte folgen der User-Sprache.
- Maschinenlesbare Artefakte behalten stabile englische Schema-Keys.
- Jede belastbare Architekturbehauptung braucht Evidence: `file:line`, Commit-SHA, Hash oder `NOT VERIFIED`.
- Token-Effizienz ist ein Produktziel: breite flache Analyse zuerst, gezielte Tiefenlekture nur dort, wo sie spaeteren Nutzen hat.

## Slice 1: Project Map Bootstrap

Beim Start eines nicht-trivialen kimiflow-Laufs prueft kimiflow, ob `.kimiflow/project/INDEX.json` existiert. Falls nicht, wird einmalig pro Lauf ein empfohlener, aber ueberspringbarer Bootstrap angeboten.

Interaktive Auswahl:

```text
Keine kimiflow-Projektkarte gefunden.

Projektkarte anlegen? Empfohlen, aber ueberspringbar.

1. Ueberspringen
2. Schnell
   Stack, Ordnerstruktur, Entry Points, Tests, wichtige Dependencies.
3. Standard (empfohlen)
   Schnell + Architekturmodell, zentrale Module, Datenfluesse,
   Konventionen, Teststrategie, offene Fragen.
4. Tief
   Standard + detaillierte Modulsteckbriefe, kritische Flows,
   Skalierbarkeit/Wartbarkeit/Sicherheitsrisiken.
```

Headless-Flags:

```text
$kimiflow --project-map quick
$kimiflow --project-map standard
$kimiflow --project-map deep
$kimiflow --project-map skip
```

V1-Default ist `standard`, wenn der User interaktiv zustimmt.

### Slice-1-Artefakte

```text
.kimiflow/project/
  INDEX.json
  FACTS.jsonl
  CODEBASE.md
  ARCHITECTURE.md
  CONVENTIONS.md
  TESTING.md
  FLOWS.md
  OPEN-QUESTIONS.md
```

`INDEX.json` speichert Repo-Metadaten, Sprache, Tiefe, Commit-SHA, generierte Bereiche und Staleness-Grunddaten.

`FACTS.jsonl` ist die kompakte Evidence-Schicht. Jede Zeile ist ein Fakt mit stabilen Keys:

```json
{"kind":"entrypoint","area":"cli","path":"SKILL.md","line":1,"summary":"Kimiflow canonical workflow entrypoint","confidence":"high","commit":"cba4942"}
```

Markdown-Dateien sind fuer Menschen und Agenten lesbar, aber nicht die einzige Wahrheit. Spaetere Laeufe sollen zuerst `INDEX.json` und relevante `FACTS.jsonl`-Ausschnitte nutzen und nur bei Bedarf Markdown lesen.

### Mapper-Fokus

Slice 1 uebernimmt die gute GSD-Idee: Fokuslaeufe schreiben direkt Dateien, damit der Orchestrator nicht alles in den Kontext laden muss.

- Tech Mapper: Stack, Dependencies, Integrationen.
- Structure Mapper: Ordnerstruktur, Entry Points, wichtige Orte.
- Architecture Mapper: Komponenten, Verantwortlichkeiten, Datenfluesse, Invarianten.
- Quality Mapper: Konventionen, Tests, CI/Verifikation.
- Synthesis: schreibt `INDEX.json`, verdichtet `FACTS.jsonl`, aktualisiert `OPEN-QUESTIONS.md`.

Wenn Subagents nicht verfuegbar sind, laeuft dieselbe Analyse sequenziell mit Shell/Git/rg.

## Slice 2: Staleness + Delta Refresh

Kimiflow erkennt nicht nur "Map stale", sondern Staleness pro Bereich. Dafuer speichert `INDEX.json` Sections mit Dateien, Hashes, Commit-SHA und Abhaengigkeiten.

Beispiel:

```json
{
  "schema_version": 1,
  "baseline_commit": "cba4942",
  "language": "de",
  "sections": {
    "hooks": {
      "files": ["hooks/commit-secret-gate.sh", "hooks/state-gate.sh"],
      "file_hashes": {
        "hooks/commit-secret-gate.sh": "sha256:<content-hash>"
      },
      "last_scanned_commit": "cba4942",
      "depends_on": ["git", "jq"],
      "status": "current"
    }
  }
}
```

Bei jedem relevanten kimiflow-Lauf:

1. `INDEX.json` lesen.
2. `git diff --name-status <section.last_scanned_commit> HEAD` auswerten.
3. Datei-Hashes fuer betroffene Pfade vergleichen.
4. Geaenderte Dateien auf Sections mappen.
5. Nur stale oder potentially-stale Sections zum Delta-Refresh anbieten.

Impact-Regeln:

- Geaenderte Section-Datei: Section stale.
- Neue Datei unter bekanntem Prefix: Section potentially stale.
- Geloeschte Section-Datei: Section stale hard.
- Manifest/Dependency/Build-Config geaendert: Stack/Testing/Architecture potentially stale.
- Route/API/Schema/Migration geaendert: betroffene Flows stale.
- Shared Utility geaendert: direkte Dependents potentially stale.

Delta-Refresh ist empfohlen, aber nicht blockierend. Wenn ein Feature/Fix direkt in einem stale Bereich arbeitet, fragt kimiflow:

```text
Die Projektkarte kennt diesen Bereich, aber `hooks` ist seit dem letzten Scan stale.
Delta-Refresh durchfuehren? Empfohlen.
```

## Slice 3: Vault, Repo-Doku und Improve-Linsen

Slice 3 baut auf den lokalen Artefakten auf.

Speicherauswahl:

```text
Wo soll das Ergebnis gespeichert werden?

1. Nur in kimiflow
2. kimiflow + Obsidian Vault
3. kimiflow + Obsidian Vault + Repo-Doku
```

Regeln:

- `.kimiflow/project/` wird immer geschrieben.
- Vault schreibt kuratierte, wiederfindbare Notizen mit Index/MOC-Update.
- Repo-Doku wird nur auf explizite Wahl geschrieben.
- Improve ist opt-in und nutzt Analyse + Architektur als Voraussetzung.

Improve-Linsen:

- Refactoring: Kopplung, Duplikation, uebergrosse Dateien, schlechte Modulgrenzen.
- Skalierbarkeit: Datenfluesse, Hotspots, Caching, Nebenlaeufigkeit, externe Limits.
- Testbarkeit: fehlende Testnaht, schwer isolierbare Module, fehlende Regressionstests.
- Wartbarkeit: unklare Verantwortlichkeiten, fragile Bereiche, inkonsistente Patterns.
- Sicherheit/Privacy: Auth-, Secret-, Datenzugriffs- und Eingabegrenzen.

Jede Verbesserung wird als Slice formuliert:

```text
Problem
Evidence
Nutzen
Risiko
Aufwand
Akzeptanzkriterien
Nicht anfassen
```

## Integration in normale kimiflow-Laeufe

Phase 0 bekommt eine Project-Map-Pruefung:

```text
project_map: missing | current | partially_stale | stale | skipped
```

Phase 2 liest Projektintelligenz vor neuer Code-Erkundung:

1. `.kimiflow/project/INDEX.json`
2. relevante `FACTS.jsonl`-Eintraege
3. relevante Markdown-Artefakte
4. `.kimiflow/STANDARDS.md` / `.kimiflow/DECISIONS.md`
5. Vault / claude-mem, falls vorhanden
6. gezielte Code-Erkundung nur fuer Luecken

Das bestehende kimiflow-Prinzip bleibt: Project Map hilft, ersetzt aber keine Evidence fuer konkrete Planbehauptungen.

## Token-Effizienz

- Erst Index und Fact-Slices lesen, nicht ganze Markdown-Dokumente.
- Vollscan nur auf expliziten Bootstrap/Deep-Lauf.
- Standardlauf: breit/flach, danach gezielte Deep Dives.
- Delta-Refresh pro Section statt ganzes Repo.
- Mapper schreiben direkt Artefakte; Orchestrator sammelt nur Status und Pfade.
- Markdown kompakt halten; `FACTS.jsonl` fuer wiederverwendbare Fakten.
- Keine Embeddings oder bezahlten Provider als V1-Voraussetzung.

## Fehler- und Sicherheitsverhalten

- Kein Git-Repo: Project Map kann als lokale Analyse laufen, aber Staleness/Commit-SHA wird als `NOT VERIFIED` markiert.
- Kein Vault: Vault-Save ueberspringen und im State notieren.
- Kein Subagent: sequenziell laufen.
- Unklare Architekturbehauptung: `NOT VERIFIED`, nicht als Fakt speichern.
- Secret-Gefahr: keine `.env` lesen; generierte Docs vor Commit mit bestehendem Secret-Content-Scan pruefen.
- Map-Erzeugung oder Refresh darf normale kimiflow-Laeufe nicht blockieren, ausser der User hat den Map-Lauf selbst als Hauptziel gestartet.

## Akzeptanzkriterien fuer Slice 1

AC-1: Wenn `.kimiflow/project/INDEX.json` fehlt und ein nicht-trivialer kimiflow-Lauf startet, bietet kimiflow einen ueberspringbaren Project-Map-Bootstrap an.

AC-2: Wenn der User `standard` waehlt, erzeugt kimiflow `.kimiflow/project/INDEX.json`, `FACTS.jsonl`, `CODEBASE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `FLOWS.md` und `OPEN-QUESTIONS.md`.

AC-3: Alle menschlich lesbaren Project-Map-Artefakte werden in der User-Sprache geschrieben.

AC-4: `INDEX.json` enthaelt mindestens `schema_version`, `language`, `scan_depth`, `baseline_commit`, `created_at`, `sections` und `artifacts`.

AC-5: `FACTS.jsonl` enthaelt nur belegte Fakten oder markiert Unsicherheit mit `confidence`/`NOT VERIFIED`.

AC-6: Der Bootstrap ist tokenarm: der Orchestrator liest nicht die vollstaendigen generierten Markdown-Artefakte in den Chat-Kontext zurueck.

AC-7: Bestehende kimiflow Feature-/Fix-/Audit-Flows funktionieren weiter, wenn der Bootstrap uebersprungen wird.

## Akzeptanzkriterien fuer Slice 2

AC-8: Kimiflow erkennt pro Section `current`, `stale`, `potentially_stale` oder `unknown`.

AC-9: Delta-Refresh liest nur betroffene Sections und aktualisiert deren Hashes/Commit-SHA.

AC-10: Normale Feature-/Fix-Laeufe bieten Refresh bei stale betroffenen Bereichen an, blockieren aber nicht hart.

## Akzeptanzkriterien fuer Slice 3

AC-11: User kann zwischen `kimiflow`, `kimiflow + Vault` und `kimiflow + Vault + Repo docs` waehlen.

AC-12: Vault-Notizen folgen der bestehenden Vault-Konvention und User-Sprache.

AC-13: Repo-Doku wird nur bei expliziter Auswahl geschrieben.

AC-14: Improve-Slices werden nur nach Analyse + Verstehen erzeugt und enthalten Evidence, Nutzen, Risiko, Aufwand und Akzeptanzkriterien.

## Nicht-Ziele fuer V1

- Keine eigene vollstaendige Tree-sitter-Engine.
- Keine Pflichtinstallation von Serena, Codebase-Memory oder anderen MCPs.
- Kein automatisches Refactoring aus dem Bootstrap heraus.
- Kein hartes Blockieren normaler kimiflow-Laeufe wegen fehlender Map.
- Keine riesigen Wiki-Seiten ohne maschinenlesbare Evidence.

## Offene Implementierungsentscheidung

Der Slice-1-Mapper kann zunaechst als Skill-/Prompt-Workflow umgesetzt werden. Ein spaeterer Slice kann Shell-Helper fuer `INDEX.json`, Hashing und Staleness extrahieren, sobald die Artefaktform stabil ist.
