# Testing

Kimiflow ist ein Shell- und Plugin-zentriertes Repository. Die wichtigsten Checks pruefen deshalb Manifeste,
Hook-Wiring, Resolver-Verhalten und Smoke-Installationen fuer Claude Code und Codex.

## Lokaler Standard-Check

```bash
bash hooks/smoke-install.sh
bash hooks/smoke-install-codex.sh
bash hooks/test-kimiflow-runner.sh
bash hooks/test-install-kimiflow-cli.sh
bash hooks/test-project-map-status.sh
git diff --check
```

Fuer groessere Hook-Aenderungen sollten zusaetzlich die direkt betroffenen Unit-Tests unter `hooks/test-*.sh`
laufen.

## CI

`.github/workflows/ci.yml` laeuft auf Push und Pull Request. Die Pipeline prueft:

- Bash-Syntax fuer `hooks/*.sh`.
- Unit-Tests fuer Gate-Resolver, Commit-Hygiene, State-Gate, Test-Gate, Secret-Content-Scan,
  Test-Weakening-Scan und Project-Map-Status.
- JSON-Syntax und Manifeststruktur fuer Claude, Codex und Hook-Manifeste.
- Claude- und Codex-Smoke-Installationen.
- ShellCheck als informationalen Hinweis.

## Smoke-Tests

- `hooks/smoke-install.sh` prueft die Claude-Struktur, Hook-Manifeste, Project-Map-Vertrag und synthetische
  Hook-Payloads.
- `hooks/smoke-install-codex.sh` prueft Codex-Manifeste, Plugin-UI-Metadaten, Hook-Labels, den stabilen
  Hook-Installer und synthetische Codex-Payloads.
- `hooks/test-kimiflow-runner.sh` prueft den optionalen Codex-Terminalweg ohne Modellaufruf: sicherer Start,
  autonomes Same-Thread-Resume, materieller Wait und fehlende Kimiflow-Aktivierung.
- `hooks/test-install-kimiflow-cli.sh` prueft verwaltete Installation/`--check` und verhindert das
  Ueberschreiben eines fremden Executables.

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
