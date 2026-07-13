# kimiflow-demo.tape — render the SCRIPTED ILLUSTRATION (play.sh) to a GIF.
# play.sh walks the current core flow: launcher → project state → adaptive discovery →
# gated feature/fix loop → commit-gate → learning loop. This is a reconstruction,
# NOT a captured model session. For a real run, see docs/demo/README.md.
#
# Render:  vhs kimiflow-demo.tape        →   kimiflow.gif

Output kimiflow.gif

Require bash
Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 900
Set Padding 28
Set Theme "Catppuccin Mocha"
Set PlaybackSpeed 1.0

Hide
Type "clear" Enter
Show
Sleep 400ms

Type "bash play.sh" Sleep 500ms Enter
# play.sh prints for ~17s; then hold the completed frame ~7s so the last line
# is readable before the loop restarts (static frames cost ~nothing in a GIF).
Sleep 17s
Sleep 7s
