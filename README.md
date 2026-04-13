# Audax

`audax.py` is the top-level launcher for the mission-driven Claude/Codex loop. The repo is now split into:

- `audax.py`: thin CLI entrypoint
- `audax_core/`: orchestration, prompts, backends, artifact locking, repo-rule discovery, and progress reporting
- `tests/`: unit tests plus optional live CLI smoke tests

## Usage

Run the tool and type the mission prompt into stdin:

```bash
python audax.py
```

It will print instructions, wait for your prompt, and start after you press `Ctrl-D`.

You can still pass the mission request as arguments:

```bash
python audax.py "Add JWT auth middleware with refresh token rotation"
```

Useful flags:

- `--spec-rounds 10`
- `--implementation-rounds 50`
- `--require-approval`
- `--workspace-dir .audax`
- `--heartbeat-seconds 5`
- `--subprocess-timeout-seconds 1800` (use `0` to disable)

## Behavior

- Claude drafts `mission_spec.md`
- Codex reviews the draft against the request and repo rules
- Optional user approval happens before the mission is locked
- The mission is locked as `.audax/mission_spec.md`, `.audax/mission_spec.pdf`, and `.audax/mission_spec.lock.json`
- Claude implements against the locked mission
- Codex reviews for bugs, missing requirements, repo-policy gaps, and test gaps
- The loop repeats until success or the implementation round limit is hit
- Each Claude/Codex subprocess is timed out after 1800 seconds by default to avoid hung runs
- Raw partial agent output is not streamed; heartbeat lines show activity instead

Per-round Claude outputs and Codex reviews are saved under `.audax/logs/` and `.audax/reviews/`. A final run report is written to `.audax/run_report.json`.

## Tests

Unit tests:

```bash
pytest -q
```

Live CLI smoke tests:

```bash
AUDAX_RUN_LIVE_CLI_TESTS=1 pytest -q tests/test_live_cli.py
```

## Documentation

Build the Sphinx HTML site locally:

```bash
python -m pip install -r docs/requirements.txt
python -m sphinx -W -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` in a browser to inspect the generated site.
