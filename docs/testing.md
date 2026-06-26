# Testing

Kimiflow ist ein Shell- und Plugin-zentriertes Repository. Die wichtigsten Checks pruefen deshalb Manifeste,
Hook-Wiring, Resolver-Verhalten und Smoke-Installationen fuer Claude Code und Codex.

## Lokaler Standard-Check

```bash
bash hooks/smoke-install.sh
bash hooks/smoke-install-codex.sh
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

Maintainer-Releases sollten ueber den Projekt-Helper laufen:

```bash
hooks/release.sh --next patch --summary "kurzes Release-Thema" --yes
```

Der Helper startet nur aus einem sauberen Working Tree, prueft Remote-/Tag-Konflikte, bump't die Claude- und
Codex-Manifeste, aktualisiert `CHANGELOG.md` und `COMPATIBILITY.md`, laesst Syntax-, Unit-, Smoke- und
Advisory-Checks laufen, staged nur bekannte Release-Dateien, committet, taggt, pusht und erstellt oder
aktualisiert den GitHub Release. Mit `--dry-run` kann der Zielstand vorab geprueft werden.

Der geplante `0.2.0` Public-History-Reset ist kein normaler Release. Dafuer liegt das manuelle Runbook unter
[`docs/history-reset-checklist.md`](history-reset-checklist.md).
