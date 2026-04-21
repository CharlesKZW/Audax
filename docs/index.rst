Audax Documentation
===================

.. container:: hero

   **Audax lands audacious engineering changes that a single frontier agent
   can rarely pull off reliably in one shot.** Ambitious refactors, migrations, and
   features usually only converge after many rounds of human back-and-forth
   with an agent — clarifying requirements, catching regressions, and
   re-prompting until the work actually holds together. Audax collapses that
   loop into one long-running, structured session and tries to make
   audacious work reliable by construction rather than by hope.

At A Glance
-----------

.. rst-class:: audax-roles

* 🧑‍💻 **Human** — states the mission once and approves a locked contract.
* ✍️ **Claude** — drafts the spec and implements against the locked mission.
* 🔍 **Codex** — reviews both the spec and the live repo, emitting
  structured JSON findings.
* 🔁 **Orchestrator** — feeds reviews back into Claude until the mission is
  accomplished or the round budget is spent.

Purpose
-------

Audax is for **audacious** work — large, ambitious engineering changes that
a single frontier agent can rarely pull off reliably in one turn, and that
in a normal workflow would require a human to repeatedly re-prompt, clarify
requirements, catch regressions, and steer the agent back on track.

Audax replaces that ad hoc loop with a disciplined one:

* **Locked contract up front.** The human states the mission once and
  approves a locked specification (markdown + SHA-256 manifest) before any
  code is written.
* **Claude does the building.** Drafting and implementation run on a single
  strong implementer model.
* **Codex plays the reviewer role the human would otherwise play.** Reviews
  come back as structured JSON findings, not free-form prose.
* **The orchestrator keeps the session alive.** Codex findings are fed back
  into Claude across many rounds until the mission is accomplished or the
  round budget is exhausted.

The result is a long-running agent session with the operational discipline
of a code review, designed so that audacious missions **converge reliably
instead of drifting**.

Why Claude Implements And Codex Reviews
---------------------------------------

.. note::

   **This split is a working hypothesis, not a law.**

   In practice:

   * **Claude** feels **more creative and fluent** at drafting specs and
     producing implementations that thread a large surface area.
   * **Codex** feels **more reliable and grounded** when inspecting real
     repository state on complex tasks and refusing to sign off on
     something that is almost-but-not-quite right.

   So Audax defaults to **Claude on the implementer side** and **Codex on
   the reviewer side**, because that matches where each model seems
   strongest.

.. warning::

   **Open to correction.** This pairing is based on informal observation,
   not a benchmark. If you see the opposite, please open an issue — the
   roles can be swapped by editing ``audax_core/backends.py``.

.. warning::

   Audax runs the two agent CLIs with their interactive safety rails
   **disabled** so the review loop can proceed autonomously. It also
   defaults to frontier models at maximum reasoning effort. Read
   :doc:`getting-started` before pointing Audax at a repository you care
   about.

Explore The Docs
----------------

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Get Started
      :link: getting-started
      :link-type: doc
      :class-card: surface-card

      Install the prerequisites, review the safety warnings, run Audax
      against a repository, inspect the generated workspace, and build the
      documentation locally.

   .. grid-item-card:: Workflow
      :link: workflow
      :link-type: doc
      :class-card: surface-card

      Follow the lifecycle from mission drafting through approval, locking,
      implementation, review, and failure handling.

   .. grid-item-card:: Architecture
      :link: architecture
      :link-type: doc
      :class-card: surface-card

      Understand the package layout, core data models, subprocess backends,
      the Claude/Codex role split, and the invariants that keep each run
      auditable.

   .. grid-item-card:: API Reference
      :link: api
      :link-type: doc
      :class-card: surface-card

      Browse the Python API for the orchestrator, CLI entrypoints, backend
      adapters, artifact helpers, and review schemas.

Quick Start
-----------

Run Audax from the repository root and pass the mission request directly:

.. code-block:: bash

   python audax.py "Add JWT auth middleware with refresh token rotation"

If you want to type the request interactively instead, run without a
positional task argument and finish stdin with ``Ctrl-D``:

.. code-block:: bash

   python audax.py

What Audax Guarantees
---------------------

* **Outcome-level mission specs.** Drafts are reviewed to keep success
  criteria focused on user-observable behavior and major architectural
  decisions, avoiding unnecessary exact UI strings, test identifiers, and
  implementation details.
* **Locked mission contract.** The spec is reviewed before implementation
  and then locked as markdown plus a SHA-256 checksum manifest. The
  orchestrator verifies the markdown digest around every implementation
  round and fails fast if the contract has been mutated.
* **Centralized backend behavior.** Claude and Codex run through explicit
  CLI adapters instead of ad hoc shell glue, so the exact flags used are
  visible in ``audax_core/backends.py`` and on the startup card.
* **Inspectable failures.** Review loops persist both Claude summaries and
  Codex JSON reviews, so every run is auditable after the fact.
* **Opt-in subprocess timeout.** No subprocess timeout by default; pass
  ``--subprocess-timeout-seconds`` to cap hung agent CLIs.
* **Resumable sessions.** ``python audax.py continue`` re-enters an
  interrupted session against its already-locked mission spec, without
  re-drafting or re-approving.

.. toctree::
   :hidden:

   getting-started
   workflow
   architecture
   cli-reference
   api
