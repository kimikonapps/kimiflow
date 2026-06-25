# Kimiflow Repo-Doku

Diese Doku ist eine publish-safe Ableitung aus der lokalen Kimiflow-Deep-Map. Sie beschreibt Architektur,
Codebase und Verifikation, ohne lokale Findings, Schwachstellenlisten oder projektspezifische Arbeitsnotizen
zu veroeffentlichen.

## Einstieg

- [Architektur](architecture.md) - wie Canonical Engine, Host-Adapter, Hooks und Project Intelligence zusammenspielen.
- [Codebase](codebase.md) - wo die wichtigen Dateien liegen und welche Aenderungen wohin gehoeren.
- [Testing](testing.md) - lokale Checks, CI, Smoke-Tests und Release-Verifikation.
- [Demo](demo/README.md) - Material fuer die animierte Kimiflow-Demo.
- [Superpowers-Vergleich](kimiflow-vs-claude-md-vs-superpowers.md) - Kontext zu Kimiflow im Vergleich zu anderen Workflow-Ansaetzen.

## Was hier bewusst nicht steht

Konkrete Bugs, Risiken, offene Findings und Verbesserungsvorschlaege aus Deep-Analysen bleiben lokal in
`.kimiflow/project/`. Diese Dateien sind durch `.gitignore` ausgeschlossen und dienen als Arbeitsqueue fuer
gezielte Folge-Fixes. Repo-Doku bleibt kuratiert, teamtauglich und publish-safe.
