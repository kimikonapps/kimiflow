# Kimiflow Memory Router Design

Date: 2026-06-25
Language: de
Status: draft for review

## Ziel

Kimiflow bekommt einen token-effizienten Memory Router mit automatischem Vault Learning Loop. Der User soll
nicht entscheiden muessen, was gespeichert oder geladen wird. Kimiflow soll selbst kuratieren, transparent
melden, was passiert ist, und beim naechsten Run automatisch vom gespeicherten Wissen profitieren.

Das Ziel ist nicht "mehr Kontext". Das Ziel ist besserer Kontext:

- immer ein kleines, aktuelles Projektgedaechtnis laden;
- groessere Projekt-, Run- und Vault-Wissensbestaende nur gezielt durchsuchen;
- neue Learnings nach einem Run automatisch klassifizieren und speichern;
- alte, doppelte oder stale Learnings konsolidieren;
- sensible Details lokal halten oder nur entschaerft speichern;
- normale Feature-/Fix-/Audit-Laeufe schneller und besser geerdet machen.

## Research Input

Hermes Agent ist fuer diese Spec der wichtigste Vergleichspunkt:

- Hermes nutzt bounded Memory: kleine `MEMORY.md` / `USER.md` Snapshots werden automatisch in neue Sessions
  geladen, bleiben aber hart begrenzt.
- Hermes speichert volle Session-History in SQLite mit FTS5 und nutzt Suche statt Vollkontext.
- Hermes trennt stabile Prompt-Schichten, Projektkontext, Memory und ephemere Overlays, um Prompt-Caching
  und Memory-Korrektheit zu schuetzen.
- Hermes komprimiert Kontext bei Druck statt unendlich zu wachsen.
- Hermes hat einen Curator, der agent-erzeugte Skills beobachtet, stale/archived setzt und Konsolidierung
  optional macht.

Quellen:

- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage
- https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly
- https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
- https://hermes-agent.nousresearch.com/docs/user-guide/features/curator

## Leitprinzipien

- Der User ist faul: automatische Kuratierung ist der Default.
- Transparenz statt Babysitting: Kimiflow meldet kurz, fragt aber nicht bei jedem Memory-Write.
- Token-Effizienz ist ein Feature, kein Optimierungsdetail.
- `.kimiflow/project/` bleibt die lokale, maschinennahe Source of Truth.
- Der Vault ist der kuratierte Langzeitspeicher, nicht der operative Statusspeicher.
- Repo-Doku bleibt publish-safe und wird nur bei explizitem Storage-Ziel geschrieben.
- Sicherheitsdetails, konkrete Schwachstellen, Secrets, private Pfade und rohe Findings bleiben lokal oder
  werden nur sanitisiert gespeichert.
- Jedes dauerhafte Learning braucht Evidence, Confidence und Freshness-Metadaten.
- Stale Wissen wird nicht still als Wahrheit geladen.

## Begriffe

**Memory Router**

Die Komponente, die am Anfang eines Runs entscheidet, welches gespeicherte Wissen relevant ist und in welchem
Budget es geladen wird.

**Learning Loop**

Die Komponente, die am Ende eines Runs neue Erkenntnisse extrahiert, klassifiziert, dedupliziert und in das
richtige Ziel schreibt.

**Always-on Memory**

Ein kleiner, immer geladener Projekt-Snapshot. Er ist absichtlich begrenzt und wird kuratiert, damit er nicht
zum zweiten README wird.

**On-demand Recall**

Gezielte Suche in `.kimiflow/project/`, alten Runs, Vault und optionalen Memory-Providern. Ergebnisse werden
gerankt und gekappt.

## Speicherstufen

Kimiflow unterscheidet vier Wissensstufen:

1. **Run Memory**
   `.kimiflow/<slug>/`
   Laufbezogene Artefakte: `STATE.md`, `INTENT.md`, `RESEARCH.md`, `PLAN.md`, `ACCEPTANCE.md`, Reviews,
   Logs und Reproduktionsdetails.

2. **Project Intelligence**
   `.kimiflow/project/`
   Operatives Projektwissen: Project Map, Section-Hashes, `FACTS.jsonl`, offene Findings, Improvements,
   Test- und Buildwissen, Projekt-Memory.

3. **Vault Knowledge**
   Obsidian oder kompatibles Notes-MCP.
   Kuratierte Langzeitnotizen: Entscheidungen, wiederkehrende Muster, Lessons Learned, projektuebergreifende
   Erkenntnisse und User-/Arbeitsstilpraeferenzen.

4. **Repo Docs**
   `README.md`, `docs/`, ADRs.
   Publish-safe Dokumentation fuer Menschen im Repo. Nie automatisch fuer rohe Findings oder sensible Risiken.

## Neue lokale Artefakte

V1 erweitert `.kimiflow/project/` um kleine Memory-Artefakte:

```text
.kimiflow/project/
  MEMORY.md
  LEARNINGS.jsonl
  MEMORY-INDEX.json
  RECALL.md
```

### `MEMORY.md`

Der immer geladene Snapshot. Zielgroesse: 500-900 Tokens. Inhalt:

- wichtigste Projektkonventionen;
- Test-/Build-Kommandos;
- Release-/Plugin-Prozess;
- zentrale Architekturhinweise;
- wichtige "immer beachten" Regeln;
- 3-7 aktuelle Warnungen ohne sensible Details.

`MEMORY.md` ist kein Dump. Wenn es zu lang wird, muss Kimiflow konsolidieren.

### `LEARNINGS.jsonl`

Maschinenlesbare Langform, eine Zeile pro Learning:

```json
{"id":"learn_20260625_release_flow","kind":"process","scope":"project","topic":"release","summary":"Version in Claude- und Codex-Manifest bumpen, Tag kimiflow--vX.Y.Z erstellen und GitHub Release publizieren.","evidence":["CHANGELOG.md:5",".claude-plugin/plugin.json:4",".codex-plugin/plugin.json:3"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"e1080e3","status":"current"}
```

Pflichtfelder:

- `id`
- `kind`
- `scope`
- `topic`
- `summary`
- `evidence`
- `confidence`: `high|medium|low`
- `sensitivity`: `public|normal|private|security`
- `last_verified`
- `source_commit`
- `status`: `current|stale|superseded|archived`

### `MEMORY-INDEX.json`

Billiger Lookup-Index:

```json
{
  "schema_version": 1,
  "updated_at": "2026-06-25T00:00:00Z",
  "repo_id": "github.com/swinxx/kimiflow",
  "language": "de",
  "always_on_memory_tokens_estimate": 820,
  "vault": {
    "available": false,
    "last_recall_at": null,
    "last_write_at": null
  },
  "topics": {
    "release": ["learn_20260625_release_flow"],
    "codex-plugin": ["learn_20260625_hook_cache_paths"]
  }
}
```

### `RECALL.md`

Run-lokales Protokoll der geladenen Wissensquellen. Es gehoert in den Run-Ordner oder wird als letzter
Recall-Snapshot in `.kimiflow/project/` gehalten. Inhalt:

- welche Quellen verfuegbar waren;
- welche Queries genutzt wurden;
- welche Treffer geladen wurden;
- welche Treffer wegen Budget, Staleness oder Sensitivity ausgelassen wurden;
- geschaetztes Tokenbudget.

## Slice 1: Pre-Run Hydration und Retrieval Router

Jeder nicht-triviale Kimiflow-Run startet mit einer kleinen Hydration.

Immer lesen:

1. `hooks/launcher-status.sh` oder gleichwertige Statusdaten.
2. `.kimiflow/project/INDEX.json`, falls vorhanden.
3. `.kimiflow/project/MEMORY-INDEX.json`, falls vorhanden.
4. `.kimiflow/project/MEMORY.md`, falls vorhanden und unter Budget.
5. `.kimiflow/STANDARDS.md` und `.kimiflow/DECISIONS.md`, falls vorhanden, aber gekappt.

Nicht immer lesen:

- volle Project-Map-Markdowns;
- komplette `FACTS.jsonl`;
- alte Run-Artefakte;
- ganze Vault-Notizen;
- Repo-Doku;
- Webquellen.

### Query Profile

Aus `INTENT.md`, `PROBLEM.md` oder `AUDIT-INTENT.md` erzeugt Kimiflow ein Query Profile:

```json
{
  "mode": "feature",
  "task_terms": ["memory router", "vault", "token efficiency"],
  "likely_topics": ["memory", "project-map", "vault", "launcher"],
  "likely_paths": ["SKILL.md", "reference.md", "hooks/launcher-status.sh"],
  "project_sections": ["skill_engine", "project_map", "docs_examples"],
  "risk": "small"
}
```

Das Query Profile steuert alle weiteren Reads.

### Retrieval Reihenfolge

1. **Always-on Memory**
   `MEMORY.md` laden, wenn vorhanden. Wenn >900 Tokens, nur Summary-Header + wichtigste bullets laden und
   Curator markieren.

2. **Project Map Facts**
   `FACTS.jsonl` nach `area`, `topic`, `path`, `kind` und Task-Terms filtern. V1 darf `rg`/`jq`/Shell nutzen,
   spaeter kann ein Helper daraus werden.

3. **Project Map Sections**
   Nur relevante Markdown-Sektionen lesen. Wenn `project-map-status.sh status --affected <path>` stale oder
   unknown meldet, gezielte Delta-Refresh-Empfehlung vor tieferem Lesen.

4. **Local Learnings**
   `LEARNINGS.jsonl` nach Topic/Path/Mode filtern. `status != current` nicht laden, ausser als Warnung.

5. **Vault Recall**
   Nur wenn Vault-MCP verfuegbar ist. Queries:
   - Repo-ID / GitHub URL / Projektname;
   - Task-Terms;
   - Topic-Tags;
   - "kimiflow" + aktueller Modus.

   Maximal Top 5 Treffer bewerten, Top 3 Inhalte oder Snippets laden.

6. **Alte Runs**
   Nur wenn Query Profile direkt einen alten Slug, ein altes Finding oder ein geparktes Thema trifft. Maximal
   2 Run-Summaries laden, nie volle Logs.

7. **Web Research**
   Nur fuer Luecken, stale Vault-Hits oder aktuelle externe Fakten.

### Ranking

Treffer werden einfach und erklaerbar gerankt:

```text
score =
  project_match
+ topic_match
+ path_match
+ mode_match
+ freshness
+ confidence
- sensitivity_penalty
- staleness_penalty
```

V1 braucht kein Embedding und keinen bezahlten Provider. Plain text search reicht.

### Token Budgets

Default-Budgets pro Run:

| Scope | Always-on | Project facts | Map sections | Vault | Alte Runs | Gesamt Recall |
|---|---:|---:|---:|---:|---:|---:|
| trivial | 0-300 | 0 | 0 | 0 | 0 | 300 |
| small | 500-900 | 800 | 1200 | 1200 | 600 | 3500 |
| large | 700-1200 | 1600 | 2500 | 2000 | 1000 | 7000 |
| project-map deep | 700-1200 | offen nach Fokus | offen nach Fokus | 2000 | 1000 | bewusst groesser |

Wenn das Budget ueberschritten wird, laedt Kimiflow weniger Treffer und schreibt in `RECALL.md`, was ausgelassen
wurde. Es erweitert nicht still den Kontext.

## Slice 2: Automatischer Post-Run Learning Loop

Nach Phase 2 und nach Phase 7 prueft Kimiflow, ob neue dauerhafte Learnings entstanden sind.

### Kandidatenquellen

- neue/verifizierte Architekturfacts;
- getestete Build- und Testkommandos;
- Release-/Installationsprozess;
- wiederkehrende Fehlerursachen;
- User-Korrekturen;
- Entscheidungen mit Warum;
- Projektkonventionen;
- Findings, die als dauerhafte Warnung relevant bleiben;
- geloeste Open Questions.

### Klassifizierung

Kimiflow klassifiziert jeden Kandidaten:

| Klasse | Ziel | Beispiel |
|---|---|---|
| `run_only` | `.kimiflow/<slug>/` | temporaerer Debug-Log, einmalige Repro |
| `project_memory` | `.kimiflow/project/MEMORY.md` + `LEARNINGS.jsonl` | Testkommando, Release-Ablauf, Architekturregel |
| `vault` | Obsidian | dauerhafte Entscheidung, wiederkehrendes Pattern, projektuebergreifendes Learning |
| `repo_doc_candidate` | nur Vorschlag, kein Auto-Write | publish-safe Architekturabschnitt |
| `skip` | nirgends | triviale Version, offensichtliche Shell-Ausgabe |

### Auto-Write Default

Kimiflow fragt nicht fuer jeden Write. Default:

- `project_memory`: automatisch schreiben.
- `vault`: automatisch schreiben, wenn Vault verfuegbar, Inhalt dauerhaft nuetzlich und nicht sensibel ist.
- `repo_doc_candidate`: nicht automatisch schreiben; nur als Vorschlag oder bei Storage-Ziel `repo-docs`.
- `security`/`private`: lokal halten oder sanitisiert in Vault, nie konkret in Repo-Doku.

Nach dem Run reicht eine kurze Meldung:

```text
Memory aktualisiert: 2 Projekt-Learnings, 1 Vault-Notiz, 0 Repo-Doku.
```

Details gibt es nur auf Wunsch ueber Launcher/Memory-Status.

### Dedup und Update

Vor dem Schreiben sucht Kimiflow nach bestehenden Eintraegen mit gleicher Repo-ID, Topic und Kind.

- Gleiches Learning, gleiche Aussage: `last_verified` aktualisieren.
- Gleiche Aussage, neue Evidence: Evidence ergaenzen.
- Widerspruch: alter Eintrag `stale` oder `superseded`, neuer Eintrag `current`.
- Aehnliche Learnings: zusammenfuehren, wenn dadurch `MEMORY.md` kuerzer wird.

### Sensitivity Regeln

`public`

Kann in Repo-Doku, Vault und `.kimiflow` landen.

`normal`

Kann in Vault und `.kimiflow`; Repo-Doku nur publish-safe.

`private`

Vault nur, wenn es fuer den User dauerhaft nuetzlich ist und keine lokalen privaten Pfade/Secrets enthaelt.
Sonst `.kimiflow` lokal.

`security`

Konkrete Schwachstellen, Exploit-Pfade, Secret-Namen, private Pfade und Angriffsdetails bleiben lokal in
`.kimiflow/project/FINDINGS.md`, `RISKS.md` oder Run-Artefakten. Vault erhaelt hoechstens eine sanitisiert
formulierte Erinnerung ohne Ausnutzungsanleitung.

## Slice 3: Curator und Launcher-Integration

Damit Memory nicht vermuellt, bekommt Kimiflow einen Curator.

### Trigger

Der Curator laeuft nicht als Daemon. Er wird opportunistisch gestartet:

- im Launcher, wenn `MEMORY.md` ueber Budget ist;
- nach N abgeschlossenen Runs, default 10;
- wenn `LEARNINGS.jsonl` viele stale/superseded Eintraege hat;
- wenn Vault-Index-Duplikate gefunden werden;
- manuell ueber `kimiflow memory curate`.

### Curator-Verhalten

Deterministisch und billig zuerst:

- stale/superseded Learnings aus `MEMORY.md` entfernen;
- doppelte Learnings zusammenfuehren;
- `MEMORY.md` wieder unter Budget bringen;
- `MEMORY-INDEX.json` neu schreiben;
- Vault-Dedupe nur ueber Index/Metadaten, nicht ueber Vollscan.

LLM-Konsolidierung ist optional und nur fuer groessere Memory-Pflege gedacht. V1 braucht sie nicht.

### Launcher-Anzeige

`launcher-status.sh` kann spaeter ergaenzen:

```json
{
  "memory": {
    "project_memory_present": true,
    "always_on_tokens_estimate": 820,
    "learnings_current": 14,
    "learnings_stale": 2,
    "vault_available": true,
    "curation_recommended": false
  }
}
```

Im Launcher:

```text
Memory: aktuell - 14 Learnings - Vault verbunden
```

Drilldown:

```text
1. Geladene Learnings anzeigen
2. Memory kuratieren
3. Vault-Verbindung pruefen
4. Ein Learning vergessen/ersetzen
5. Zurueck
```

## Integration in Kimiflow-Phasen

### Phase 0

- Status und Map-Staleness lesen.
- `MEMORY-INDEX.json` und `MEMORY.md` lesen.
- Curator nur empfehlen oder kurz deterministic ausfuehren, wenn Budget ueberschritten ist.

### Phase 1

- Intent/Problem/Audit-Scope erzeugt Query Profile.
- Keine breite Recherche.

### Phase 2

- Memory Router laeuft vor frischer Code-Erkundung.
- Vault/claude-mem Treffer ersetzen Web Research, wenn sie frisch und passend sind.
- Relevante Project-Map-Sektionen werden gezielt gelesen.
- Am Ende von Phase 2 koennen Forschungs- und Architektur-Learnings gespeichert werden, aber nur mit Evidence.

### Phase 3/4

- Plan nutzt `RECALL.md` als Beleg, welche gespeicherten Annahmen geladen wurden.
- Plan-Gate darf stale oder unbewiesene Memory-Claims beanstanden.

### Phase 5/6

- Keine neuen Memory-Writes waehrend Umsetzung ausser run-lokale Notizen.
- Verifikation entscheidet, welche Learnings wirklich dauerhaft sind.

### Phase 7

- Post-run Learning Review.
- `MEMORY.md`, `LEARNINGS.jsonl`, `MEMORY-INDEX.json` aktualisieren.
- Optional Vault-Notiz schreiben.
- Kurze Zusammenfassung in Chat, Details in Artefakte.

## User Controls

Default ist automatisch. Trotzdem braucht der User einfache Korrekturen:

```text
kimiflow memory status
kimiflow memory show
kimiflow memory curate
kimiflow memory forget <id|topic>
kimiflow memory why-loaded <id>
```

V1 kann diese Controls als Launcher-Optionen und dokumentierte Skill-Modi definieren, bevor eigene Helper
existieren.

## Fehlerverhalten

- Kein Vault-MCP: weiterlaufen, in `STATE.md` notieren, nur `.kimiflow` nutzen.
- Vault langsam/nicht erreichbar: kurze Timeout-Grenze, kein Blocker.
- Invalides `MEMORY-INDEX.json`: ignorieren, aus `LEARNINGS.jsonl` neu aufbauen.
- `MEMORY.md` zu lang: gekappt laden und Curator empfehlen/ausfuehren.
- Widerspruechliche Learnings: neueres/evidenzstaerkeres gewinnt, altes wird stale.
- Stale Project Map: gezielter Delta-Refresh anbieten, nicht blind volle Map lesen.
- Keine Evidence: Learning nicht als `current` speichern.

## Akzeptanzkriterien

AC-1: Ein normaler nicht-trivialer Kimiflow-Run liest zuerst `INDEX.json`, `MEMORY-INDEX.json` und ein gekapptes
`MEMORY.md`, bevor er frische Code- oder Web-Recherche startet.

AC-2: Der Memory Router erzeugt ein Query Profile aus Intent/Problem/Audit-Scope und nutzt es zur Auswahl von
Project-Map-Facts, Learnings, Vault-Treffern und alten Runs.

AC-3: Kimiflow laedt nicht den ganzen Vault und nicht die komplette Project Map. V1 laedt maximal die in der
Spec definierten Top-Treffer/Budgets.

AC-4: Jeder Run schreibt ein `RECALL.md` oder gleichwertiges Recall-Protokoll mit Quellen, Treffern, ausgelassenen
Treffern und Budgethinweis.

AC-5: Nach Phase 7 klassifiziert Kimiflow neue Learnings automatisch in `run_only`, `project_memory`, `vault`,
`repo_doc_candidate` oder `skip`.

AC-6: Projekt-Learnings werden automatisch in `.kimiflow/project/LEARNINGS.jsonl` und bei Relevanz in
`MEMORY.md` gespeichert.

AC-7: Vault-Learnings werden automatisch geschrieben, wenn ein Vault-MCP verfuegbar ist und die Sensitivity-Regeln
es erlauben.

AC-8: Der User wird nicht pro Learning um Freigabe gefragt. Kimiflow meldet nur kurz, was gespeichert wurde.

AC-9: Konkrete Security-Findings, Secrets, Exploit-Pfade und private lokale Pfade werden nicht automatisch in
Vault oder Repo-Doku geschrieben.

AC-10: Doppelte oder widerspruechliche Learnings werden aktualisiert, stale markiert oder zusammengefuehrt statt
blind dupliziert.

AC-11: `MEMORY.md` hat ein hartes Budget. Wenn es zu gross wird, muss Kimiflow konsolidieren oder gekappt laden.

AC-12: Ohne Vault-MCP funktioniert der gesamte Kimiflow-Flow weiter und nutzt nur lokale `.kimiflow`-Memory.

AC-13: Launcher/Status kann Memory-Zustand sichtbar machen: vorhandene Memory, Learnings, stale Count,
Vault-Verfuegbarkeit und Curator-Empfehlung.

AC-14: Repo-Doku wird durch den Memory Router nie automatisch geschrieben; sie bleibt an explizite Storage-Ziele
gebunden.

## Nicht-Ziele fuer V1

- Keine Embedding-Datenbank als Voraussetzung.
- Kein bezahlter Provider als Voraussetzung.
- Kein kompletter Vault-Scan pro Run.
- Keine automatische Aenderung von Repo-Doku.
- Kein Hintergrund-Daemon.
- Keine LLM-basierte Curator-Pflicht.
- Kein Speichern von rohen Code-Dumps oder Logs in den Vault.
- Kein harter Blocker, wenn Memory fehlt oder stale ist.

## Offene Implementierungsreihenfolge

Empfohlene Slices:

1. Lokale Artefakte + Pre-Run Retrieval (`MEMORY.md`, `LEARNINGS.jsonl`, `MEMORY-INDEX.json`, `RECALL.md`).
2. Post-run Learning Review mit automatischer Klassifizierung.
3. Vault Recall/Write Router mit Sensitivity-Regeln.
4. Launcher Memory-Status und einfache Controls.
5. Curator fuer Dedupe, Staleness und Budgetpflege.
