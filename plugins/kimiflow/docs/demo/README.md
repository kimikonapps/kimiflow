# docs/demo — the kimiflow demo GIF

The README's short demo links here. The committed clip is a scripted overview of the current feature
flow: product dialogue → explicit scope readiness → current-code inspection → focused research →
evidence-first planning and verification → relevance-aware review → atomic local commit.

Two ways to produce it. **Prefer A** — a real recording is the honest, credible artifact. B is a
cosmetic placeholder, clearly labelled.

## A — record a real run (recommended)

A genuine capture of `/kimiflow` driving an actual bug fix. This is what should ship in the README.

**Tools:** [`asciinema`](https://asciinema.org) to record, [`agg`](https://github.com/asciinema/agg)
to convert to GIF.
```bash
brew install asciinema agg     # macOS · or see each project's install docs
```

**A reproducible demo bug** (so the recording is repeatable): reuse the
[`02-risky-bugfix`](../../examples/02-risky-bugfix.md) scenario — a token refresh that throws on a
rotated refresh token — or any small real bug in a throwaway repo where the diagnose-gate and a
plan-gate round actually fire. A run where every gate passes first try is a boring demo; pick one
that makes a gate *work*.

**Record → convert:**
```bash
asciinema rec kimiflow.cast -c "claude"        # then, inside: /kimiflow --fix <the bug>
# drive the run; Ctrl-D after the verified local commit and learning summary
agg --theme monokai --font-size 18 kimiflow.cast kimiflow.gif
```

**Tips:** terminal ~100×30, a high-contrast theme, and include the scope discussion, both explicit
confirmations, one executable-evidence gate, relevance classification, and the final local commit.
`kimiflow.cast` is plain JSON; trim dead air before converting if a phase ran long.

## B — branded illustration (placeholder, clearly labelled)

A deterministic, scripted reconstruction for a clean branded loop **before** a real capture exists.
It is **not** a model run — [`play.sh`](play.sh) walks the current confirmed-intent and evidence-first
feature flow with pacing and ANSI colour. Edit `play.sh` to change wording; re-run the tape to re-render.
([`play-refusals.sh`](play-refusals.sh) holds an alternative *"four moments the gates refuse"* montage
— point the tape's `bash play.sh` at it to render that instead.)

**Tool:** [`vhs`](https://github.com/charmbracelet/vhs).
```bash
brew install vhs
vhs kimiflow-demo.tape          # → kimiflow.gif
```

If you ship B, caption it as an illustration in the README. Replace it with an A capture as soon as
you have one.

## Embedding

Drop `kimiflow.gif` next to this file and reference it from the top README, e.g.:
```markdown
![kimiflow demo](docs/demo/kimiflow.gif)
```
Keep it under ~3 MB so GitHub renders it inline; `agg` (`--font-size`, `--speed`) and `vhs`
(`Set Width/Height`, `Set PlaybackSpeed`) both expose size/speed knobs.

> The committed `kimiflow.gif` is the **scripted illustration** (Path B — the current feature-flow
> overview). Swap in a real Path-A capture when you have one: same filename, so the README picks it
> up automatically.
