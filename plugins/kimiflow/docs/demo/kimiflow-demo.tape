# kimiflow-demo.tape — render the SCRIPTED ILLUSTRATION (play.sh) to a GIF.
# play.sh walks the 0.4 feature flow: dialogue → current code → fresh large-run planning →
# sealed three-round review → executable evidence → root-cause review → local commit. This is a reconstruction,
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
# play.sh prints for ~23s; then hold the completed frame ~6s so the last line
# is readable before the loop restarts (static frames cost ~nothing in a GIF).
Sleep 23s
Sleep 6s
