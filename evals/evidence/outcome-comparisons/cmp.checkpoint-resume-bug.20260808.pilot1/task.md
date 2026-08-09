Fix the checkpoint-resume bug described below.

Retry workers occasionally skip retries after resuming a corrupt checkpoint. Harden `load_checkpoint` to implement the complete contract in `README.md` without changing its public return shape. Preserve `save_checkpoint` behavior, add regression tests, and run the test suite. Do not edit `README.md`.
