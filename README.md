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
- `--workspace-dir audax_artifacts`
- `--heartbeat-seconds 5`
- `--subprocess-timeout-seconds 1800` (use `0` to disable)

## Behavior

- Claude drafts `mission_spec.md`
- Codex reviews the draft against the request and repo rules
- Optional user approval happens before the mission is locked
- Each run creates a timestamped session directory under `audax_artifacts/sessions/`
- The mission is locked as `mission_spec.md`, `mission_spec.pdf`, and `mission_spec.lock.json` inside that session
- Claude implements against the locked mission
- Codex reviews for bugs, missing requirements, repo-policy gaps, and test gaps
- The loop repeats until success or the implementation round limit is hit
- Each Claude/Codex subprocess is timed out after 1800 seconds by default to avoid hung runs
- Raw partial agent output is not streamed; heartbeat lines show activity instead

For ex post analysis, each session keeps:

- `prompts/`: timestamped prompts sent to Claude and Codex for every round
- `claude/`: timestamped Claude outputs for mission drafting and implementation rounds
- `codex/`: timestamped Codex JSON reviews for each round
- `events.jsonl`: append-only structured event log with timestamps
- `session_manifest.json`: structured session metadata and artifact inventory
- `run_report.json`: final session summary

The workspace root also keeps `latest.json`, which points to the most recent session.

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
