Architecture
============

Design Goals
------------

Audax is optimized for repositories that want stronger operational discipline
than a single unconstrained agent session:

* locked specifications before implementation begins,
* structured review payloads instead of free-form pass/fail text,
* repository-aware prompting grounded in local rule files, and
* a durable artifact trail for auditing or debugging runs later.

Deliberate Tradeoffs
--------------------

A few design choices are load-bearing and worth calling out explicitly. They
are also the places Audax chooses to absorb risk in exchange for keeping the
review loop autonomous.

Claude implements, Codex reviews — with automatic fallback
   The role split is intentional. Informal observation suggests:

   * **Claude** tends to be **more creative and fluent** at drafting specs
     and producing implementations that thread a large surface area.
   * **Codex** tends to be **more reliable and grounded** when inspecting
     real repository state on complex tasks and refusing to sign off on
     something that is almost-but-not-quite right.

   Audax therefore defaults to Claude on the implementer side and Codex on
   the reviewer side. But both backends now satisfy both roles, so any of
   the four combinations in the 2x2 (implementer × reviewer) space can run.
   If a preferred backend fails on a given round — capacity error, rate
   limit, subprocess crash, or, when Claude is acting as reviewer, a JSON
   parse failure — the orchestrator falls through to the next candidate
   for that role within the same round. Fallback is **per-round, not
   sticky**: if Claude failed in round 3, Codex covers round 3, and round 4
   tries Claude first again, on the assumption that capacity issues are
   transient.

   When fallback fires, Audax prints a stdout line such as
   ``[Round 3] implementation: claude failed (model at capacity); trying
   next candidate`` and appends a ``role_fallback_triggered`` event to
   ``events.jsonl``, so the swap is visible both interactively and in the
   forensic trail.

   .. warning::

      **Open to correction.** This pairing is based on informal observation,
      not a benchmark. If you see the opposite, please open an issue — the
      preferred order can be swapped by editing the
      ``implementers``/``reviewers`` lists built in ``audax_core/app.py``.

Two frontier models at maximum reasoning effort
   Claude Opus with ``reasoning effort=max`` is paired with Codex
   ``gpt-5.4`` at ``model_reasoning_effort=xhigh``. Using two top-tier models
   on opposite sides of the loop makes the review signal meaningful, but it
   also makes each run expensive and slow relative to a one-shot call to a
   default model. The constants live at the top of ``audax_core/backends.py``
   and can be lowered for cheaper runs.

Agent safety rails are disabled by default
   Both adapters pass the "dangerous" bypass flags so the orchestrator can
   drive a multi-round implementation and review loop without stopping for
   per-action approval. Claude runs with ``--dangerously-skip-permissions``
   and Codex runs with ``--dangerously-bypass-approvals-and-sandbox``. This
   is safe only inside an environment where autonomous file system writes
   and shell execution are acceptable, such as a dedicated git worktree, an
   ephemeral container, or a disposable VM. The mission lock with SHA-256
   digest verification is the structural guardrail Audax adds on top.

Heartbeat output instead of streamed partial chatter
   Audax intentionally suppresses raw partial agent output and emits
   low-frequency ``working...`` / ``still working`` heartbeats. This keeps
   terminal logs readable and prevents partial model output from being
   mistaken for a final response, at the cost of giving up a real-time view
   of what the agent is typing.

External CLI binaries, not direct API calls
   Audax shells out to the installed ``claude`` and ``codex`` CLIs instead of
   calling the Anthropic or OpenAI HTTP APIs directly. This piggybacks on the
   authentication, retry, and model-selection behavior of those CLIs but
   means Audax cannot run where the binaries are not installed.

Artifact Format Choices
-----------------------

Audax deliberately mixes a few file types based on how each artifact is used:

* Markdown for the mission spec and Claude outputs, because those artifacts are
  primarily read and diffed by humans.
* JSON for lock manifests, session manifests, and Codex reviews, because those
  artifacts benefit from stable machine-readable structure.
* JSON Lines for ``events.jsonl``, because append-only chronological logs are
  easier to stream and post-process in that format than in a single large JSON
  array.

Package Map
-----------

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Module
     - Responsibility
   * - ``audax_core.app``
     - Top-level CLI parsing, command availability checks, and orchestrator
       construction.
   * - ``audax_core.orchestrator``
     - Mission drafting, approval, locking, implementation, review, and final
       run-report persistence.
   * - ``audax_core.backends``
     - Adapters for the real Claude and Codex CLIs, including Claude stream
       parsing and Codex JSON schema execution.
   * - ``audax_core.progress``
     - Heartbeat-style progress reporting and subprocess lifecycle management.
   * - ``audax_core.artifacts``
     - Mission lock creation and SHA-256 integrity verification.
   * - ``audax_core.repo_rules``
     - Discovery and bounded rendering of repository policy files.
   * - ``audax_core.prompts``
     - Prompt templates for mission drafting, implementation, and review.
   * - ``audax_core.reviews``
     - JSON schemas and parsing helpers for Codex review payloads.
   * - ``audax_core.models``
     - Shared dataclasses, defaults, and backend protocols.
   * - ``audax_core.approval``
     - Interactive mission approval for terminal-based workflows.

Core Invariants
---------------

The implementation relies on a few explicit invariants:

Immutable mission artifacts
   After approval, the mission spec becomes a locked contract. The orchestrator
   verifies the SHA-256 digest of the locked markdown around each implementation
   round.

Outcome-level mission contract
   The mission spec is reviewed for user-observable success criteria and major
   architectural decisions, not exact UI strings, test IDs, selectors, or
   low-level implementation mechanics unless those specifics are part of the
   requested public contract.

Structured review exchange
   Codex is always asked for JSON that conforms to a schema. This makes the
   review loop easier to parse, persist, and feed back into Claude.

Repository-root execution
   Both external CLIs execute with the repository root as their current working
   directory so file edits, tests, and path references resolve naturally.

Bounded loops
   Mission drafting and implementation each have explicit maximum round counts.
   Individual agent subprocesses run without a timeout by default; pass
   ``--subprocess-timeout-seconds`` to opt in to a hard ceiling.

Extension Points
----------------

Audax is small enough that most extensions land cleanly in one of three places:

Alternative backend adapters
   Implement the ``ClaudeBackend`` or ``CodexBackend`` protocol and pass the
   adapter into ``ReviewLoopOrchestrator``.

Custom approval gates
   Supply a different ``approval_gate`` callable when constructing the
   orchestrator if terminal prompts are not appropriate for your environment.

Prompt strategy changes
   Update the prompt builders if you need additional repo metadata, more rigid
   policy framing, or different review instructions.

Operational Notes
-----------------

Audax intentionally avoids streaming raw agent output to stdout. Instead it
prints heartbeat lines such as ``working...`` and ``still working`` so the user
gets progress signals without leaking partial model chatter into the terminal
log.
