# Audax

[![Documentation Status](https://readthedocs.org/projects/audax-implement-review/badge/?version=latest)](https://audax-implement-review.readthedocs.io/en/latest/?badge=latest)

> **Audax lands _audacious_ engineering changes and features that a single frontier agent
> can rarely pull off in one shot.**

Most ambitious refactors, migrations, and features only converge after many
rounds of human back-and-forth — clarifying requirements, catching
regressions, and re-prompting the agent until the work actually holds
together. Audax collapses that loop into **one long-running, structured
session**:

- 🧑‍💻 **Human** states the mission once and approves a _locked_ mission spec.
- ✍️ **Claude** drafts the spec, then implements against the locked mission.
- 🔍 **Codex** reviews both the spec and the live repo, emitting structured
  JSON findings.
- 🔁 **Orchestrator** feeds reviews back into Claude until the mission is
  accomplished or the round budget is spent.

Audax tries to make audacious work reliable by construction rather than by hope.

---

## Why Claude Implements And Codex Reviews

> [!NOTE]
> **This split is a working hypothesis.**
> In practice:
> - **Claude** feels **more creative and fluent** at drafting specs and
>   producing implementations that thread a large surface area.
> - **Codex** feels **more reliable and grounded** when it comes to
>   inspecting real repository state on complex tasks and refusing to sign
>   off on something that is almost-but-not-quite right.
>
> So Audax defaults to **Claude on the implementer side** and **Codex on the
> reviewer side**, because that matches where each model seems strongest.
>
> **Both backends now serve both roles.** Any of the four combinations in the
> 2×2 (implementer × reviewer) space can run. If a preferred backend fails on
> a given round — capacity error, rate limit, subprocess crash, or (when
> Claude is acting as reviewer) a JSON parse failure — the orchestrator
> falls through to the next candidate for that role within the same round.
> Fallback is **per-round, not sticky**: if Claude failed on round 3, Codex
> covers round 3 and round 4 retries Claude first, on the assumption that
> capacity issues are transient. When fallback fires, Audax prints a stdout
> line and appends a `role_fallback_triggered` event to `events.jsonl`, so
> the swap is visible both interactively and in the forensic trail.
>
> ⚠️ **Open to correction.** The default ordering is based on informal
> observation, not a benchmark. If you see the opposite, please open an
> issue — the preferred order can be swapped by editing the
> `implementers`/`reviewers` lists built in `audax_core/app.py`.

---

## Repository Layout

| Path | Purpose |
| --- | --- |
| `audax.py` | Thin CLI entrypoint. |
| `audax_core/` | Orchestration, prompts, backends, artifact locking, repo-rule discovery, approval gate, progress reporting. |
| `docs/` | Sphinx source for the documentation site (`pydata_sphinx_theme`). |
| `tests/` | Unit tests plus optional live CLI smoke tests. |

---

## How It Works

```
user task
  ─▶ Claude drafts mission_spec.md
  ─▶ Codex reviews the draft against the task and repo rules
  ─▶ human approval (default)
  ─▶ mission spec is locked as markdown + SHA-256 checksum manifest
  ─▶ Claude implements against the locked mission
  ─▶ Codex reviews the live repository state (structured JSON)
  ─▶ repeat until success or round limit
```

Mission specs are intentionally outcome-level. Success criteria should describe
user-observable behavior, while the spec captures only major architectural
decisions that affect public contracts, data flow, migrations, rollback,
security, or integrations. It should avoid low-level mechanics, exact UI copy,
test IDs/selectors, fixture names, file paths, function names, and test names
unless those literals are part of the requested public contract.

Each run creates a timestamped session directory under
`audax_artifacts/sessions/`. Every prompt, every Claude output, every Codex
review, and an append-only `events.jsonl` chronology are persisted inside
that session, so any run is **reproducible and inspectable** after the fact.

---

## ⚠️ Safety And Cost Warnings

> [!WARNING]
> Audax is opinionated, and several of those opinions are dangerous.
> **Read this before pointing it at a repository you care about.**

### 🔓 Agent safety rails are disabled by default

Audax invokes both CLIs in modes that skip their interactive permission and
sandbox protections:

- **Claude** → `--dangerously-skip-permissions`
  (`CLAUDE_SKIP_PERMISSIONS = True`).
  Claude will read, write, and run shell commands against your repository
  **without asking for per-action approval**.
- **Codex** → `--dangerously-bypass-approvals-and-sandbox`
  (`CODEX_BYPASS_APPROVALS_AND_SANDBOX = True`).
  Codex runs its review **without the normal sandbox and approval gates**.

This is a deliberate design choice — the review loop has to run autonomously
across many rounds. The practical consequence is that you should **only** run
Audax inside an environment where autonomous file system writes and shell
execution are acceptable:

- ✅ a dedicated **git worktree**
- ✅ an **ephemeral container**
- ✅ a **disposable VM**
- ❌ **not** the main working tree with uncommitted changes

### 💸 Frontier models at maximum reasoning effort

Defaults in `audax_core/backends.py`:

| Agent | Model | Reasoning Effort | Output |
| --- | --- | --- | --- |
| Claude | `opus` | `max` | streamed JSON + partial messages |
| Codex | `gpt-5.4` | `xhigh` | JSON validated against a schema |

These settings optimize for **output quality over price and latency**.
Expect each run to be significantly more expensive and slower than a one-shot
call to a default model, especially across multiple rounds. Edit the
constants at the top of `audax_core/backends.py` if you want cheaper runs.

### 🧰 External CLI dependency

Audax does **not** call the Anthropic or OpenAI APIs directly. It shells out
to the `claude` and `codex` binaries and expects them to be installed,
authenticated, and on your `PATH`. The exact flags Audax passes are visible
in `audax_core/backends.py` and on the startup card printed before each run.

### 🧪 New and unverified

This project is **not battle-tested**. Treat failures, odd artifacts, and
unusual agent behavior as expected until you have run it enough times to
build your own confidence.

---

## Usage

Run the tool and type the mission prompt into stdin:

```bash
python audax.py
```

Audax prints a startup card summarizing the active configuration, waits for
your prompt, and starts after you press `Ctrl-D`.

You can also pass the mission request as arguments:

```bash
python audax.py "Add JWT auth middleware with refresh token rotation"
```

**Useful flags:**

- `--spec-rounds 3`
- `--implementation-rounds 5`
- `--require-approval` / `--no-require-approval`
- `--workspace-dir audax_artifacts`
- `--heartbeat-seconds 5`
- `--subprocess-timeout-seconds` (no timeout by default; set a number to cap hung subprocesses, or `0` to explicitly disable)
- `--claude-cmd` / `--codex-cmd` to override the backend CLI names
- `--auto-commit` / `--no-auto-commit` — the implementer (Claude by default, Codex on fallback) is instructed to commit logical chunks as it works; Audax then runs a final **sweeper** commit per round to capture any trailing uncommitted work, so one round can yield many commits rather than one monolithic dump (default **on**; silently skipped when not a git repo)
- `--session-branch` / `--no-session-branch` — check out a dedicated `audax/<session_id>` branch to keep commits off your main branch (default **off**; commits land on the current branch)

### Resuming An Interrupted Session

If a session is killed mid-implementation (Ctrl-C, SIGTERM, crash, reboot),
you can pick it back up without re-drafting the mission spec:

```bash
python audax.py continue                     # resume the most recent incomplete session
python audax.py continue 20260413T181500Z_pid42   # resume a specific session
```

Only sessions that already have a locked mission spec
(`mission_spec.lock.json`) are resumable. The SHA-256 digest in the lock
manifest is re-verified before any implementation round runs, so resume will
refuse to continue if `mission_spec.md` has been tampered with.

Drafting and approval are skipped on resume — the locked contract from the
original session is reused as-is, and only the implementation loop runs.
The previous session's last Codex implementation review is also rehydrated
as the first resumed round's reviewer feedback, so the unresolved
objections flow forward as structured input instead of being rediscovered
from repo state.

---

## Behavior

- Claude drafts `mission_spec.md`.
- Codex reviews the draft against the request and repo rules.
- The draft is expected to stay focused on user-observable outcomes and major
  architectural decisions, not implementation minutiae or exact test/UI
  identifiers.
- User approval is **required by default** before the mission is locked.
- If Codex still has open objections when spec rounds are exhausted, Audax
  ships the latest draft for a final human decision and shows the latest
  reject message.
- Each run creates a timestamped session directory under
  `audax_artifacts/sessions/`.
- The mission is locked as `mission_spec.md` and `mission_spec.lock.json`
  (SHA-256 manifest) inside that session.
- Claude implements against the locked mission.
- Codex reviews for bugs, missing requirements, repo-policy gaps, and test
  gaps.
- The loop repeats until success or the implementation round limit is hit.
- Claude/Codex subprocesses have **no timeout** by default. Pass
  `--subprocess-timeout-seconds <n>` if you want a hard ceiling on hung runs.
- Raw partial agent output is **not** streamed; heartbeat lines show
  activity instead.
- After each implementation round, Audax prints a three-box **Round Report**
  to the terminal: Implementer (parsed `Accomplished` / `Tests Run` /
  `Remaining Risks`), Reviewer (summary + color-coded issues by severity),
  and Progress (a percentage bar plus a two-column
  completed-vs-remaining criteria table).

**Per-session artifacts (for ex post analysis):**

- `prompts/` — timestamped prompts sent to Claude and Codex for every round.
- `claude/` — timestamped Claude outputs for mission drafting and
  implementation rounds.
- `codex/` — timestamped Codex JSON reviews for each round.
- `events.jsonl` — append-only structured event log with timestamps.
- `session_manifest.json` — structured session metadata and artifact
  inventory.
- `run_report.json` — final session summary.

The workspace root also keeps `latest.json`, which points to the most recent
session.

---

## Prerequisites

- **Python** 3.12 or newer
- The **`claude`** CLI, installed and authenticated
- The **`codex`** CLI, installed and authenticated
- **`pytest`** for the local test suite

---

## Tests

Unit tests:

```bash
pytest -q
```

Live CLI smoke tests (hit the real `claude` and `codex` binaries):

```bash
AUDAX_RUN_LIVE_CLI_TESTS=1 pytest -q tests/test_live_cli.py
```

---

## Documentation

Full documentation is hosted on Read the Docs:
**<https://audax-implement-review.readthedocs.io/en/latest/>**

It covers the workflow, architecture, CLI reference, and API reference.

---

## Contributing

> [!IMPORTANT]
> The documentation source under `docs/` is intentionally kept **in
> lockstep** with the code in `audax_core/`. If you change a CLI flag, a
> default, or a backend setting, please update both the README and the
> matching `.rst` page in the same commit so the rendered HTML does not
> drift out of sync.

---

## License

Audax is released under the [MIT License](LICENSE).
