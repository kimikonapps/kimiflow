# Projektregeln fuer Claude Code

## Kimiflow-Routing

- Nicht vor jeder Aenderung nach `Kimiflow` oder `direkt` fragen. Route die Aufgabe selbststaendig.
- Explizites `Kimiflow` startet Kimiflow; explizites `direkt` umgeht Kimiflow. Beide Angaben haben Vorrang vor automatischem Routing.
- Bugfixes, Reviews, Refactors, Cleanup, Doku-/Config-Arbeit und kleine risikoarme Features direkt erledigen. Dafuer Kimiflow nur auf ausdruecklichen Wunsch starten.
- Kimiflow automatisch fuer substanzielle Feature-Arbeit starten, wenn mindestens ein materieller Grund vorliegt: mehrere Produktoberflaechen oder Subsysteme, eine neue externe Integration oder ein relevanter Datenfluss, Migration/Security/Permissions/Public-API/Architektur-Risiko oder echter Discovery-Bedarf wegen materiell unklarem Intent bzw. Acceptance.
- Reine Dateianzahl ist kein ausreichender Grund. Ist kein materieller Trigger klar, direkt arbeiten; keine Routing-Rueckfrage stellen.
- Eine Wunschformulierung wie "ich haette gerne", "sollten wir", "waere es sinnvoll" oder eine fachliche Idee ist noch keine Bau-Freigabe.
- Reine Erklaerungen, Read-only-Inspektion, Statusabfragen und kleine Diagnose-Kommandos sind erlaubt, solange nichts am Projekt geaendert wird.
