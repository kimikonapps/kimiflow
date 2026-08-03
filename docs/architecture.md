# Architektur

Kimiflow ist eine Prompt-/Shell-Hybrid-Engine fuer explizit gestartete Laeufe und automatisch geroutete,
autorisierte substanzielle Feature-Arbeit. Normale Fixes und kleine risikoarme Arbeiten bleiben direkt. Die
Kernidee: Das Modell fuehrt den Workflow, aber kritische Gates werden durch wiederverwendbare Shell-Skripte
und persistente Artefakte geerdet.

## Schichten

| Schicht | Dateien | Aufgabe |
|---|---|---|
| Canonical Engine | `docs/render/kimiflow/`, `phases/`, `reference.md`, `docs/kimiflow-scaling-knobs.md` | Definiert den duennen Always-loaded Driver, on-demand Phasenregeln, Scope-Regeln, Project Map, Review- und Commit-Kontrakt und rendert die Host-Skills. |
| Host Packaging | `plugins/kimiflow/`, `SKILL.md`, `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/` | Baut einen allowlist-basierten Runtime-Kandidaten mit Inhalts-Fingerprint und macht dieselbe Engine fuer Claude Code und Codex installierbar. |
| Optional Controller | `hooks/kimiflow-runner.sh`, `hooks/kimiflow_core/runner.py`, `hooks/kimiflow_core/model_adapter.py`, `hooks/install-kimiflow-cli.sh` | Fuehrt dieselbe Active-Run-Engine ueber den eingebauten Codex- oder einen versionierten JSON-stdio-Adapter aus; besitzt nur Transport-Metadaten und keine zweite Workflow- oder Memory-Logik. |
| Adaptive Control | `hooks/adaptive-control.sh`, `hooks/kimiflow_core/adaptive_control.py` | Klassifiziert Scope/Intent/Domaene/Betrieb, entscheidet Context-Rollover und evidence-basierte Modellrouten; fehlende Capabilities fallen ohne User-Gate auf den bestehenden Flow zurueck. |
| Mechanical Layer | `hooks/*.sh`, `hooks/kimiflow_core/worktree_broker.py`, `hooks.json`, `hooks/hooks.json` | Implementiert Gate-Resolver, Host-Hooks, Installer, die optionale Drei-Slot-Fleet und strukturelle Checks. |
| Project Intelligence | `.kimiflow/project/`, `hooks/project-map-status.sh`, `hooks/memory-router.sh` | Baut lokale Projektkarten, erkennt Staleness, routet bounded Memory/Recall, Vault-Namespaces und Run-Retention. |
| Validation & Docs | `.github/workflows/ci.yml`, `docs/`, `examples/`, `evals/` | Verifiziert Packaging, Hooks und Verhalten; erklaert die Nutzung publish-safe. |

## Kontrollfluss

```text
User request
  -> /kimiflow in Claude Code oder $kimiflow in Codex
     ODER optional: kimiflow run -> Codex/Command-Adapter -> derselbe Kimiflow-Vertrag
  -> canonical workflow aus SKILL.md
  -> aktuelle Phase-Datei plus exakt zugewiesene Abschnitte aus reference.md
  -> mechanische Resolver/Hooks fuer Gates
  -> bei busy/dirty Primary optional ein gesperrter eigener Worktree mit FIFO/Path-Contract-Gate
  -> Artefakte unter .kimiflow/<slug>/ oder .kimiflow/project/
  -> lokaler atomarer Commit; Broker-Runs werden geprueft ff-only integriert und danach archiviert
```

Claude Code nutzt den gerenderten Root-Skill und plugin-bundled Hooks. Codex nutzt einen gerenderten
Adapter-Skill unter `skills/kimiflow/` und den in `.codex-plugin/plugin.json` deklarierten gebündelten
Hook-Vertrag. `hooks/install-codex-hooks.sh --check` validiert diesen Vertrag, schreibt aber keine User-Dateien.
Beide Skill-Dateien bleiben committed, werden aber aus
`docs/render/kimiflow/` materialisiert:

```bash
PYTHONPATH="$PWD/hooks" python3 -m kimiflow_core.render
```

Der optionale Terminal-Runner ist keine zweite Engine. Sein Controller liest
`active_run.status_json`, speichert unter `.kimiflow/session/HEADLESS_RUN.json` nur Host, Adapter, Root, Session,
Run-Pfad, Turn-Zaehler und Status; der eingebaute Adapter setzt dieselbe Session mit `codex exec resume` fort. Die eigentliche
Workflow-Wahrheit bleibt in `ACTIVE_RUN.json`, `STATE.md`, `ITEMS.jsonl`, den Gates und dem Memory Router. Der
Runner ist die einzige Interpretationsgrenze, die diese Artefakte mit Controller-Erreichbarkeit zu
`idle|starting|reachable|waiting|resumable|terminal|failed` normalisiert. Hosts konsumieren diese Sicht, statt
Receipt-Status, Active Run und PIDs erneut zu kombinieren. Ein
alternatives Harness implementiert denselben Capability- und JSON-stdio-Vertrag. Ein materieller Wait/Park wird
an den User zurueckgegeben; technische Turns laufen bis zum harten Turn-Limit plus genau einem Recovery-Turn ohne
Routinebestaetigung weiter. Zaehler bleiben `null`, wenn der Provider keine Usage liefert.
`codex app-server` bleibt eine moegliche spaetere Transport-Alternative fuer einen echten Rich Client, ist aber
keine Abhaengigkeit des schlanken CLI-Wegs.

Die Pi-Integration besteht aus dem Workflow-Skill und genau einer ruhenden `kimiflow_crew`-Extension. Sie wird
nur auf expliziten Kimiflow-Aufruf aktiv und besitzt keine eigene Herdr-Bridge, keinen Prozess-Broker, keine
Transport-Receipts, keine parallele Recovery-Wahrheit und keine eigene Calm-Darstellung. Standalone Pi fuehrt
Kimiflow weiterhin in der aktuellen Sitzung aus.

Fuer die Ein-Gespraech-/sichtbare-Crew-Oberflaeche wird unveraendertes FirstMate als eigene Distribution
installiert. Die urspruengliche Pi-Sitzung ist der frei ansprechbare Captain und startet einen sichtbaren normalen
FirstMate-Ship als Kimiflow Main. Main besitzt den kompletten Kimiflow-Ablauf und seine Scouts/Ships; Worker
duerfen keine weitere Crew starten. FirstMate besitzt Briefs, sichtbare Endpoints, Worktrees, Status,
Backend-Lifecycle, Recovery, Delivery, Cleanup und `/calm`. Fragen laufen Main→Captain→User und zurueck, nie als
zweites User-Gespraech im Main- oder Worker-Tab.

Die Rollenmatrix sperrt Crew-Tools mechanisch; sie ist keine Betriebssystem-Sandbox fuer Pis Shell. Enge Briefs
und Kimiflows Git-/Evidence-Gates bei Integration und Abschluss machen unerlaubte Produktwrites sichtbar.

Captain- und Main-Homes sind getrennt und projekt-/run-spezifisch; deshalb blockieren parallele Projekte einander
nicht. Main lebt in einem langlebigen Control-Worktree, schreibt dort seinen Run-State und delegiert alle
Produktwrites an sichtbare FirstMate Ships gegen das urspruengliche Projekt. Research und Review verwenden
sichtbare Scouts. Runtime-Git-Metadaten werden eng begrenzt vorbereitet: keine getrackte `.kimiflow`, lokaler
Exclude fuer ungetrackten State und nur ein belegter, eigener reversibler Default-Branch-Marker. Integration
bleibt Stock `fm-merge-local.sh` ohne Kimiflow-Fallback.

Herdr bleibt ein experimentelles FirstMate-Backend. Ein fehlgeschlagener Spawn oder Lifecycle bleibt ein
ausdruecklicher FirstMate-Fehler; Kimiflow besitzt keinen versteckten Prozess-Fallback und synthetisiert kein
`recovered`. Alte Receipts der entfernten Kimiflow-Pi/Herdr-Bridge bleiben fuer Diagnose lesbar, koennen aber
nicht fortgesetzt werden.

Adaptive Entscheidungen bleiben Teil dieser einen Engine. Der Scope-/Intent-Classifier darf Scope nur erhoehen
und offene Produktentscheidungen nur zurueck an Intake routen. Kontext-Rollover braucht eine ausgehandelte
Adapter-Capability sowie eine ID-/Digest-genaue Bestaetigung; sonst laeuft der bestehende aktuelle Kontext
begrenzt weiter. Niedrigere Modellrollen werden erst durch vergleichbare lokale Outcome-Evidence freigegeben und
bei Regression oder kritischem Risiko auf `top` zurueckgesetzt. Dadurch kann ein Host konkrete Modelle wechseln,
ohne Kimiflow-State oder Qualitaetsregeln zu duplizieren.

Ein optionaler App-Host erweitert Protokoll v1 nur ueber ausgehandelte Feature-Flags. Ohne diese Flags bleiben
Prompt und Request des bestehenden Command-Adapters unveraendert. Mit `workflow_context` erhaelt der Host
transient die kanonischen Skill-/Phasen-/Bridge-Pfade; `model_roles` transportiert nur abstrakte Rollen, waehrend
der Host die konkreten Modell-IDs besitzt. `structured_events` werden vor JSON-Parsing groessenbegrenzt, auf
oeffentliche Felder normalisiert und nicht im Receipt gespeichert. Ein SHA-256-Fingerprint bindet Features,
Anforderungen und Rollen an Resume, ohne Modell-IDs zu persistieren. `root_confinement` bleibt eine vom Host
durchzusetzende und von Kimiflow vorab pruefbare Vertrauensgrenze. Der Vertrag liegt unter
`references/adapter-protocol.md`; KimiTalk, Providerclients oder ein Netzwerkdienst sind keine Abhaengigkeiten.

`hooks/build-plugin-candidate.sh` erzeugt den Marketplace-Inhalt aus einer engen Source-Allowlist und schreibt
`RUNTIME-FINGERPRINT.json`; private Maintainer-/Run-Daten und generierte Caches sind verboten. Der Launcher kann
damit auch gleichversionige stale Installationen erkennen. `hooks/release-consistency-check.sh` rendert vor dem
Release per `--check` und faellt bei Skill-, Kandidaten-, Fingerprint- oder leerem Unreleased-Drift in
`SKILL.md` oder `skills/kimiflow/SKILL.md` fehl, ohne lokale Drift zu ueberschreiben. Derselbe Check haelt
Byte-Budgets fuer die immer geladene Prosa (`SKILL.md` <= 17,000 Bytes, Codex-Skill <= 15,000 Bytes), fuer Phase-Dateien
(`phases/*.md` jeweils <= 20,000 Bytes) und fuer die Launcher-Default-Ausgabe (JSON <= 8,000 Bytes,
Pretty <= 12,000 Bytes auf einem sauberen Fixture-Repo).

`hooks/build-runtime-release.sh` verpackt genau diesen Kandidaten byte-deterministisch und schreibt den
oeffentlichen Update-Vertrag `kimiflow-update-v1.json`. Offline-Pruefung beweist nur Artefaktintegritaet;
offizielle Kompatibilitaet verlangt feste GitHub-Origin-Reads, unveraenderliche Release-Assets und denselben
gepinnten Tag-/Source-Commit. `hooks/publish-runtime-release.sh` ist die einzige Maintainer-Publikationsgrenze:
isolierter Export des Release-Commits, Draft-Upload, Vorpruefung, Publish und feste Nachpruefung. Builder und
Publisher werden nicht in den Laufzeitkandidaten kopiert. Externe Hosts konsumieren diese kanonische Runtime und
implementieren danach optional den Adapter-Vertrag; sie besitzen keine zweite Kimiflow-Quelle.

## Wichtige Invarianten

- Explizites Kimiflow startet den Flow, explizites direkt/direkt umgeht ihn; autorisierte substanzielle
  Feature-Arbeit mit materiellem Integrations-, Datenfluss-, Security-, API-, Architektur- oder Discovery-Risiko
  wird automatisch geroutet. Normale Fixes, Reviews, Doku/Config und kleine risikoarme Features bleiben direkt.
- Gate-Entscheidungen duerfen nicht nur behauptet werden; Resolver-Skripte liefern die mechanische Wahrheit,
  wo das moeglich ist.
- Normale Laeufe persistieren State unter `.kimiflow/<slug>/`.
- Sauberes/freies Primary bleibt der Null-Overhead-Pfad. Die Fleet darf hoechstens drei eigene
  gesperrte Worktrees verwalten; weitere Runs warten FIFO, fremde/manuelle/Codex-Trees bleiben unangetastet.
- Fleet-Leases schliessen ueberlappende Primary-/Worktree-Writes aus, benennen `blocked_by` und verlangen
  nach Primary-Fortschritt Revalidierung; Konflikte bleiben als `needs-reconcile` wiederaufnehmbar.
- Integration braucht aktuelle Pfad-/Contract-/PLAN-Evidence, konfliktfreien Merge-Tree, gruene
  argv-basierte Checks auf dem kombinierten Kandidaten und veraendert Primary nur serialisiert per Fast-forward.
- Eingebettete Hosts bleiben der Standard; der optionale Terminal-Runner und seine Adapter duerfen Workflow-State, Memory,
  Gates, Provider oder Worktree-Management weder duplizieren noch ersetzen.
- App-Host-Funktionen sind opt-in; ihr Fehlen darf weder Installation noch normale Codex-, Claude- oder Legacy-CLI-Nutzung veraendern.
- Project Intelligence persistiert lokale Projektkarten und bounded Memory unter `.kimiflow/project/`.
- Vault-Kontext muss vor Run-Injektion den lokalen Projekt-Namespace-Vertrag passieren; Retention archiviert
  nur alte terminale Runs und ersetzt niemals aktive Runs oder Learnings.
- Repo-Doku ist ein kuratierter Publishing-Layer. Lokale Findings und sensible Arbeitsnotizen bleiben in
  `.kimiflow/project/` und werden nicht automatisch committed.

## Aenderungsachsen

- Always-loaded Workflow-Aenderungen beginnen in `docs/render/kimiflow/canonical/SKILL.md`; danach wird
  `SKILL.md` gerendert. Phasendetails gehoeren in `phases/*.md`, Skalierungsdetails in
  `docs/kimiflow-scaling-knobs.md`, und breite Referenz-/Maintainerregeln in `reference.md` oder `docs/`.
- Claude-spezifisches Packaging liegt in `.claude-plugin/` und `hooks/hooks.json`.
- Codex-spezifisches Packaging liegt in `.codex-plugin/`, `.agents/plugins/`, `skills/kimiflow/`,
  `docs/render/kimiflow/overlays/codex.md` und `hooks/install-codex-hooks.sh`.
- Hook-Verhalten braucht in der Regel ein passendes `hooks/test-*.sh` und Smoke-Coverage.
- Project-Map-Verhalten braucht Updates in `reference.md`, `hooks/project-map-status.sh` und
  `hooks/test-project-map-status.sh`.
- Memory-/Learning-Verhalten braucht Updates in `reference.md`, `hooks/memory-router.sh`,
  `hooks/test-memory-router.sh` und den Launcher-Smoke-Checks.
