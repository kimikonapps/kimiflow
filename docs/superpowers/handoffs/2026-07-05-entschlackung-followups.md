# Handoff 2026-07-05 — Nach der Entschlackung: kimiflow als Projekt-Treiber weiter verbessern

## Stand
- Entschlackung vollständig umgesetzt: **15 atomare Commits** auf `main` (`2e4a081..ad11876`), **ungepusht**. Netto −3.144 Zeilen; reference.md 1557→1207; 10 Hook-Skripte + 1 Stop-Hook entfernt; Feature-Check auf read-only P7-Ensemble eingedampft; Quick-Path überspringt Recall/Vault-Pulse bei small; Map nur noch quick-Tier. Gates: 30 Suiten grün, beide Smokes grün, release-consistency grün.
- Plan + Evidenz + Abweichungen: `.kimiflow/plans/2026-07-04-entschlackung.md` (lokal/ignored, mit Status-Block). Telemetrie: `~/.kimiflow/metrics/token-economics.jsonl` (host-weit, nur Memory-Ökonomie, directional). Nutzungs-Evidenz: Run-Artefakte unter `.kimiflow/*/`.
- Review-Prozess der Entschlackung: Opus-Review je Posten, Codex-Cross-Family-Pass je Welle; ~20 Findings, 0 BLOCKER, alle eingearbeitet oder begründet abgelehnt.

## Arbeitsmodell (VERBINDLICH für alle Folge-Sessions)
- **Orchestrator = Session-Modell** (z. B. Fable): denkt, plant, schreibt Pläne/Handoffs, triagiert Reviews, committet.
- **ALLE Subagenten (Build, Research, Audit, Review, Verify): NIEMALS Fable.** Claude-Host → `model: opus`; Cross-Family-Review → Codex (Chain Codex→Gemini/agy→same-family).
- Quellen: `~/.claude/CLAUDE.md` Regel 11 (global) + kimiflow reference.md „Model routing (per-role)" (Spec seit `2e4a081`).
- Je Punkt: atomare Commits mit benannten Pfaden, keine AI-Attributions, Tests+Smokes+release-consistency vor Commit, Opus-Review vor Commit, Codex mindestens je Meilenstein.

## Punkte

### A — Project-Map von kimiflow selbst refreshen [READY]
**Problem:** `.kimiflow/project/`-Map (Inhalte Stand 28.06.) beschreibt Subsysteme, die es nicht mehr gibt (Background-Handles, Agentic-Readiness, Workqueue, Explore, FEATURE-CHECK-Maschinerie).
**Auftrag** (Opus, lokal, KEIN Commit — `.kimiflow/` ist git-ignored): `hooks/project-map-status.sh status` → betroffene Sektionen via `refresh --changed` / `refresh --section` aktualisieren; entfernte Subsysteme aus den Map-Texten tilgen. Das ist zugleich der **erste Realtest des neuen Pflegepfads** — festhalten, ob er praktikabel ist.
**Verify:** `project-map-status.sh status` ohne `stale`/`potentially_stale`; `grep -ri "background-run\|agentic\|workqueue\|explore mode" .kimiflow/project/` leer (bzw. nur legitime Historie in MEMORY/LEARNINGS).

### B — Parity-Harness auf Golden-Tests gegen HEAD umstellen [READY, Design nötig]
**Problem:** `hooks/test-kimiflow-core-parity.sh` vergleicht gegen die eingefrorene pre-R1-Baseline `72282e6` und strippt inzwischen **vier** entfernte Feldgruppen (background, agentic_readiness, feature_checks, improvements) — die Strip-Liste wächst mit jedem Umbau. Die Port-Fidelity-Garantie hat ihren Zweck erfüllt (Python-Ports seit Tagen live, 43 Unit-Tests grün).
**Auftrag** (Opus): Testtyp umstellen — die Cases behalten, aber Erwartungen als **Golden-Snapshots des aktuellen Verhaltens** einfrieren; Baseline-Materialisierung (`git archive 72282e6`) + Strip-Block entfernen. **ACHTUNG:** Die Baseline einfach auf HEAD zeigen wäre tautologisch (die alten Bash-Implementierungen existieren dort nicht mehr — heutige Shell-Skripte sind exec-Wrapper auf die Python-Ports). Es ist eine Umstellung des Testtyps, kein Re-Pointing. Falls sich das nicht sauber umsetzen lässt → Vorschlag statt Umsetzung.
**Verify:** Suite grün mit denselben Case-Namen; kein Strip-Block mehr; Negativtest belegt, dass ein manipuliertes Feld rot wird (nicht vakuum-grün).

### C — Codex-Seat-Deadline in der Transport-Spec [VERIFY FIRST]
**Evidenz 05.07.:** Zwei Codex-Läufe über das Companion-Plugin stallten (Ergebnis lag fertig in `~/.codex/sessions/**.jsonl`, Broker lieferte nicht aus; ein Lauf starb über Nacht). Kimiflows eigener Transport (direktes `codex exec -s read-only … </dev/null`, Chain Codex→Gemini→same-family) ist davon getrennt — aber unklar, ob die Spec eine **Seat-Deadline** kennt.
**Auftrag** (Opus, read-only zuerst): reference.md „Cross-family transport" + „Model routing (per-role)" prüfen: Gibt es ein definiertes Timeout-/Stall-Verhalten je Seat („keine Antwort nach N Minuten → nächstes Kettenglied, `cross_family: degraded` in STATE.md")? Falls nein → knappe Spec-Ergänzung (1–3 Zeilen, advisory, KEIN neues Gate) vorschlagen, Opus-Review, Commit.
**Verify:** Spec nennt konkretes Deadline-Verhalten; release-consistency + Smokes grün.

### D — Push + Release 0.1.59 [AWAITING USER-OK]
15+ Commits ungepusht. Nach User-OK: erst A–C-Commits fertigstellen, dann `git push`, dann `/release` (bumpt Manifeste + COMPATIBILITY, voller Test-/Smoke-Lauf, Tag `kimiflow--v0.1.59`, GitHub-Release). Nicht ohne explizites OK pushen.

### E — Globales Setup entschlacken: GSD/Superpowers [USER DECISION]
`~/.claude` lädt GSD (~70 Skills) + Superpowers + kimiflow parallel → jede Session zahlt Kontext für alle Skill-Beschreibungen, plus Routing-Ambiguität zwischen drei Prozess-Frameworks. Optionen: **(1)** GSD+Superpowers deinstallieren, kimiflow = einzige Prozess-Spur (Empfehlung) · **(2)** auf Nischen beschneiden (`gsd-surface`-Profile) · **(3)** belassen. Umsetzung ist ein separater Auftrag im `~/.claude`-Kontext, nicht in diesem Repo.

### F — Dogfooding / Feature-Freeze [USER DECISION — größter Hebel]
2–3 Wochen kimiflow ausschließlich an echten Fremdprojekten benutzen; am kimiflow-Repo nur Bugfixes. Messen (Telemetrie läuft, seit dem Opus-Routing günstiger): Runs je Projekt, Recall-Nutzen bei `large`, Quick-Path-Reibung, Gate-Fehlalarme, Zeit bis Commit. Danach datenbasiert nachschärfen statt spekulativ bauen. Die Kernfrage bleibt: *Macht kimiflow das Bauen fremder Projekte schneller?*

### G — Klein-Backlog
- `.kimiflow/fable-leaf-routing/`: STATE als überholt/abgeschlossen markieren — die Entscheidung ist via `2e4a081` umgesetzt; dessen „Option A" wurde teilweise überstimmt (Planner blieb Session-Modell).
- Optional: 1 Eval-Szenario Leaf-Routing (Fable-Session → Implementer `model: opus`; Nicht-Fable-Session → No-Op). Nur angehen, wenn Evals ohnehin angefasst werden.
- Bekannt + bewusst offen: `docs/superpowers/plans/2026-07-02-token-restructuring-invariants.md:60` nennt noch „vault pulse mandatory small/quick" — historisches Archiv, NICHT editieren (Codex-Finding begründet abgelehnt).

## Reihenfolge-Empfehlung
A (sofort, klein) → C (klein) → B (mittel) → D (nach User-OK) → E/F (User-Entscheidungen, außerhalb dieses Repos).

## Ergebnis-Nachtrag (2026-07-05, gleiche Session)
- **A ERLEDIGT** (lokal, kein Commit): Map wieder `current` (0 stale), alle 9 Sektionen; entfernte Subsysteme aus CODEBASE/ARCHITECTURE/FLOWS/TESTING/FACTS/INDEX getilgt. **Pflegepfad-Realtest:** funktioniert (Self-Heal nach Commit bestätigt), vier Befunde: (1) Map-Dokument-Edits sind für `refresh --changed` unsichtbar (git-ignored) → als Spec-Satz dokumentiert (`4574862`); (2) überlappende nackte `hooks/`-Prefixes in mehreren Sektionen erzeugen False-Positive-Kaskaden, Adoption geht nur an die längste-Prefix-Sektion → **Backlog** (Helper-Verbesserung, erst nach mehr Dogfooding-Evidenz); (3) `refresh --changed` adoptiert auch untracked/dirty Dateien → **Backlog** (Idee: committed-only-Modus); (4) Map ist 0.1.46→0.1.58 gesprungen — kimiflow_core-Port/Cross-Family/Routing nur punktuell erfasst; ein volles `--project-map quick`-Re-Bootstrap wäre der saubere Weg (optional, lokal).
- **B ERLEDIGT** (`8a9b62e`): Golden-Snapshot-Harness, 33 Goldens eingecheckt, `LC_ALL=C`-Pin (Opus-Review fing den CI-Kollations-BLOCKER), Orphan-Guard, UPDATE_GOLDEN=1-Regeneration dokumentiert. Baseline `72282e6` + Strip-Liste vollständig entfernt.
- **C ERLEDIGT, keine Änderung nötig:** Spec deckt Seat-Deadlines bereits ab (reference.md :312–313 — ~5 min Review-/Diagnose-/Verify-Seats, ~30 min Best-of-2-Implementer, timeout = Failure → next tier + `cross_family: fallback (<reason>)`). Die Stalls vom 05.07. betrafen das Codex-COMPANION-Plugin (Orchestrator-Werkzeug), nicht kimiflows `codex exec`-Transport.
- **D/E/F unverändert offen** (User). G-Backlog + die zwei Pflegepfad-Befunde oben sind die nächsten evidenzbasierten Kandidaten.
- **G-1 ERLEDIGT** (Folge-Session 05.07., lokal, kein Commit): `.kimiflow/fable-leaf-routing/STATE.md` Status auf `superseded/abgeschlossen` gesetzt — Ziel via `2e4a081` umgesetzt, Option A teilweise überstimmt (Planner blieb Session-Modell). G-2 (Eval-Szenario) bleibt geparkt bis Evals ohnehin angefasst werden; G-3 bewusst offen.
- **Pflegepfad, zweiter Realtest** (05.07.): Die Doku-Commits `4574862`/`94eec62` stellten `docs_examples`/`project_map`/`skill_engine` auf stale; `refresh --changed` adoptierte alle drei sauber (Map wieder `current`, 0 stale; kein Inhalts-Drift — Pflegepfad steht bereits in FLOWS.md). Nebenbefund: `project-map-status.sh refresh` ignoriert unbekannte Flags stillschweigend (`--dry-run` führte real aus) → Klein-Backlog wie Befunde (2)/(3).
- **D ERLEDIGT** (05.07., User-OK): Push + Release **0.1.59** (`7da5e96`, Tag `kimiflow--v0.1.59`, https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.1.59). Der Push deckte einen **CI-BLOCKER im B-Harness** auf, gefixt in `483ab23`: Die 7 Launcher-Goldens hatten Host-State eingefroren — (1) leere `KIMIFLOW_OBSIDIAN_URL` reaktiviert die Default-Loopback-Probes (lokales Obsidian antwortete → `provider_detected_unconfigured`), (2) `env`-Overlay leakte host-gesetzte Knobs (`KIMIFLOW_OBSIDIAN_MCP_AVAILABLE` → `authenticated:true`). Fix: Case-Invocation unter `env -i` (nur `PATH` + `LC_ALL=C` durchgereicht), URL auf tote Loopback-Adresse gepinnt, Goldens regeneriert. Opus-Review fing zusätzlich das now-abhängige `cutoff_date` (heute−90) im `launcher_full`-Golden — wäre ab dem Folgetag hostunabhängig rot gewesen; jetzt in `normalize()` maskiert. Hermetik bewiesen (Suite grün unter feindlicher Env), Negativtest rot, CI grün auf Hotfix + Release. **Lehre für B-artige Umstellungen:** Golden-Snapshots brauchen vollständige Env-Hermetik (`env -i`-Allowlist, nicht Blocklist) und eine Prüfung auf now-abhängige Felder.
- **E ENTSCHIEDEN + UMGESETZT** (User-Entscheid + Ausführung 05.07.): GSD + Superpowers vollständig aus `~/.claude` entfernt — kimiflow ist die einzige Prozess-Spur. Entfernt: 394 GSD-Manifest-Dateien (67 Skills, 33 Agents, 12 Hooks, `get-shit-done/`), GSD-State (`gsd-file-manifest/install-state/migration-journal/user-files-backup`), 9 GSD-Hook-Einträge aus `settings.json` (git-hygiene-guard blieb), Superpowers-Plugin via `claude plugin uninstall` inkl. verwaistem Cache, 3 tote GSD-Referenzen in `~/.claude/CLAUDE.md` (Regeln 0/11/12). Backup: `~/.claude/backups/2026-07-05-gsd-superpowers-pre-uninstall.tar.gz` (3,3 MB) + `2026-07-05-CLAUDE.md.pre-gsd-clean`. Verifiziert: settings.json valide, 0 Restreferenzen, keine gsd-Prozesse/crontab-Einträge, übrige Plugins intakt.
- **F ENTSCHIEDEN** (User, 05.07.): **Feature-Freeze ab sofort** (2–3 Wochen): am kimiflow-Repo nur Bugfixes, Nutzung ausschließlich an echten Fremdprojekten, Telemetrie beobachten (Runs je Projekt, Recall-Nutzen bei `large`, Quick-Path-Reibung, Gate-Fehlalarme, Zeit bis Commit). Danach datenbasiert nachschärfen.
