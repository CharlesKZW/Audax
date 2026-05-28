# AI Usage Statement

Audax is, by design, a project *about* AI tools — and it was also *built with*
AI tools. This statement discloses both, as required by the course.

## AI tools used

- **Claude Code** (Anthropic, Claude Opus) — primary coding assistant used to
  implement the orchestrator, CLI adapters, progress UI, and tests, and to
  draft documentation.
- **OpenAI Codex CLI** (GPT-5.5) — used as an independent reviewer of changes
  during development, the same role it plays *inside* Audax.
- **Audax itself** — we dogfooded the tool on its own codebase: a number of
  changes were landed through Audax's own build-review loop.

## Where AI helped

- Implementation of the `audax_core/` modules (orchestration loop, subprocess
  backends, artifact locking, repo-rule discovery, approval gate, progress
  reporting).
- Test scaffolding under `tests/`.
- Documentation prose (this README and the Sphinx site under `docs/`).

## What the team produced that an AI would not have on its own

- The **core design thesis**: separating the *implementer* and *reviewer*
  across two different frontier model families, behind a locked, SHA-256–
  verified mission contract that prevents the agent from quietly redefining
  its own task.
- The **per-round role-fallback policy**, the **append-only audit-trail**
  design (`events.jsonl`, per-session artifacts), and the **reduced
  mission-spec** contract rules.
- The product decisions: *what to build*, *what to cut* (an earlier
  finance-benchmark track was scoped out), and how to evaluate the result.

## How we verified AI output

- Ran the unit test suite (`pytest`) and exercised the CLI manually.
- Used Codex review — and Audax's own loop — as a second set of eyes on
  changes before merging.
- Read and edited all AI-generated code and prose before committing. We
  treated any agent "success" claim as unverified until independently checked
  against the repo state.

## Paid services and cost

- Audax does **not** call the Anthropic or OpenAI APIs directly; it shells out
  to the `claude` and `codex` CLIs, which require paid Anthropic and OpenAI
  accounts. Authentication is handled by each CLI on the developer's machine.
- Development usage was covered by standard subscriptions and did **not**
  exceed the \$50/team threshold that requires instructor approval.

## Secrets and data handling

- No API keys, tokens, or other secrets are committed to this repository.
- Per-run artifacts are written under `audax_artifacts/`, which is gitignored
  and excluded from the submission.
- Audax operates on the user's own repository and transmits data only through
  the `claude` and `codex` CLIs the user has already authenticated.
