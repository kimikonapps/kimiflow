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
- `hooks/test-firstmate-integration.sh --static` prueft, dass das Pi-Paket genau einen Skill plus eine ruhende
  Rollen-Extension ausliefert, keine eigene Pi-/Herdr-Bridge enthaelt und die FirstMate-Grenze dokumentiert ist.
- `hooks/test-firstmate-integration.sh --live` fuehrt zuerst die unveraenderten Stock-FirstMate-Tests fuer
  Pi-Primary und Herdr ohne erlaubte Skips aus. Danach laesst es einen echten Stock-FirstMate-Primary den
  sichtbaren Pi/Herdr-Main starten, Main einen sichtbaren Research-Scout starten, einen Pi-Trust-Dialog ueber
  FirstMates normalen Steuerweg bearbeiten und einen echten needs-decision→Captain-Antwort→Main-Resume-Pfad
  belegen. Captain- und Main-Homes/Endpoints muessen verschieden sein; der bestehende Plan und alle Produktbytes
  im Originalprojekt bleiben unveraendert, waehrend der erwartete lokale Captain-Runtime-State entstehen darf.
  Der Live-Test exportiert denselben geprueften FirstMate-Checkout an die Unit-Suite; ein Skip des echten
  `fm-merge-local.sh`-Tests macht Live-Acceptance rot. Unit-/Stock-Tests decken ausserdem Ship-Mode und Calm ab.
  Ein Retry-only-Pass wird berichtet, gilt aber nicht als saubere Acceptance.
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
