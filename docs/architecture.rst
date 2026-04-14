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
* PDF for the locked mission snapshot, because it gives a portable immutable
  rendering that is hard to casually edit.

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
     - Mission lock creation, integrity verification, and minimal PDF output.
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
   verifies both the markdown and PDF digests around each implementation round.

Structured review exchange
   Codex is always asked for JSON that conforms to a schema. This makes the
   review loop easier to parse, persist, and feed back into Claude.

Repository-root execution
   Both external CLIs execute with the repository root as their current working
   directory so file edits, tests, and path references resolve naturally.

Bounded loops
   Mission drafting and implementation each have explicit maximum round counts.
   External subprocesses also have a timeout ceiling.

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
