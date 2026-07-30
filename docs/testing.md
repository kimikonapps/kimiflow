# Testing

Kimiflow ist ein Shell- und Plugin-zentriertes Repository. Die wichtigsten Checks pruefen deshalb Manifeste,
Hook-Wiring, Resolver-Verhalten und Smoke-Installationen fuer Claude Code und Codex.

## Lokaler Standard-Check

```bash
bash hooks/ci-test-plan.sh verify full
bash hooks/ci-test-plan.sh run full
git diff --check
```

Der Plan inventarisiert jede `hooks/test-*.sh`-Oberflaeche genau einmal. Fokussierte Tests duerfen waehrend der
Entwicklung zusaetzlich laufen, ersetzen den vollstaendigen Plan aber nicht.

## CI

`.github/workflows/ci.yml` laeuft auf Push und Pull Request. Die Pipeline prueft:

- Bash-Syntax fuer `hooks/*.sh`.
- Auf Ubuntu den vollstaendigen, deterministischen Testplan ohne erlaubte Pflicht-Skips.
- Auf macOS die portabilitaetskritischen Python-Vertraege plus eine kleine Shell-Lane fuer Active Run,
  Product Intake und Host-Hook-Wiring.
- JSON-Syntax und Manifeststruktur fuer Claude, Codex und Hook-Manifeste.
- Claude- und Codex-Smoke-Installationen.
- ShellCheck-Fehler als hartes Gate; Warnungen bleiben informational.

## Smoke-Tests

- `hooks/smoke-install.sh` prueft die Claude-Struktur, Hook-Manifeste, Project-Map-Vertrag und synthetische
  Hook-Payloads.
- `hooks/smoke-install-codex.sh` prueft Codex-Manifeste, Plugin-UI-Metadaten, Hook-Labels, den stabilen
  Hook-Installer und synthetische Codex-Payloads.
- `hooks/test-kimiflow-runner.sh` prueft den optionalen Codex-Terminalweg ohne Modellaufruf: sicherer Start,
  autonomes Same-Thread-Resume, materieller Wait und fehlende Kimiflow-Aktivierung.
- `hooks/test-install-kimiflow-cli.sh` prueft verwaltete Installation/`--check` und verhindert das
  Ueberschreiben eines fremden Executables.
- `hooks/test-pi-host.sh` prueft den Pi-0.82.x-JSON-/Session-Vertrag, providerneutrale
  `provider/model:thinking`-Auswahl, eine unveraenderliche verifizierte Worker-Extension, Workflow-Kontext,
  Preflight sowie generationgebundene Subagents.
- `node --test hosts/pi/tests/captain.test.mjs` prueft natuerliche modellaufrufbare Aktivierung und den
  aequivalenten Slash-Weg ueber den vorhandenen Runner, fortgesetzte Konversation sowie deduplizierte Attention.
- `hooks/test-pi-kimiflow-e2e.sh` prueft einen vollstaendigen deterministischen Primary-Pi-Request bis zum
  echten `active-run finish` mit Phase-Reads, Conformance, Baseline und terminalem Kimiflow-Receipt; ein
  Pi-`agent_end` darf den Run nicht abschliessen.
- Der Acceptance-Selector-Audit verlangt, dass alle in `ACCEPTANCE.md` benannten Tests als echte Harnesses
  existieren und ausfuehrbar sind.

## Project-Map-Status

Der lokale Project-Map-Status wird mit diesem Skript geprueft:

```bash
KIMIFLOW_HOST=codex hooks/project-map-status.sh status
```

Wenn relevante Dateien geaendert wurden, koennen betroffene Sections gezielt aktualisiert werden:

```bash
hooks/project-map-status.sh refresh --section <name>
```

## Release-Checks

Bei Versionsbumps sollten mindestens README, CHANGELOG, COMPATIBILITY, Plugin-Manifeste, Marketplace-Metadaten
und GitHub Release konsistent sein. Echte Plugin-Installation und Plugin-Browser-Darstellung bleiben teilweise
manuelle Host-Checks, weil sie von Claude Code bzw. Codex selbst abhaengen.
