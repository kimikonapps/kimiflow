# Testing

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_plugin.py --check
git diff --check
```

Run the same suite on Linux/Python 3.9 and macOS/Python 3.14. It exercises the remaining check helper,
including failing commands, source/index drift, ignored output, timeouts and surviving children, plus
exact package contents, source/render parity, version consistency and unsafe output/source paths.

Tests for deleted engines are removed with those engines. No gate-compliance suite, model pressure
calibration or duplicate module wrapper remains. Historical coverage belongs to the matching development commit (see MIGRATION.md).
A passing test suite says nothing about whether the skill improves model outcomes; use the separate
paired-task protocol in `evals/README.md` for that question.
