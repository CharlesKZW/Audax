Audax Documentation
===================

.. container:: hero

   Audax turns a single mission request into a disciplined autonomous workflow.
   It drafts a locked mission spec, runs implementation rounds with Claude,
   audits the repository with Codex, and persists the artifact trail under a
   dedicated workspace.

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: Get Started
      :link: getting-started
      :link-type: doc
      :class-card: surface-card

      Install the prerequisites, run Audax against a repository, inspect the
      generated workspace, and build the documentation locally.

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

      Understand the package layout, core data models, subprocess backends, and
      the invariants that keep each run auditable.

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

If you want to type the request interactively instead, run without a positional
task argument and finish stdin with ``Ctrl-D``:

.. code-block:: bash

   python audax.py

What Audax Guarantees
---------------------

* The mission spec is reviewed before implementation and then locked as
  markdown, PDF, and a checksum manifest.
* Claude and Codex run through explicit CLI adapters instead of ad hoc shell
  glue, so their subprocess behavior is centralized.
* Review loops persist both Claude summaries and Codex JSON reviews, which
  makes failures inspectable after the fact.
* Hung agent CLI subprocesses are cut off by a configurable timeout instead of
  stalling the mission forever.

.. toctree::
   :hidden:

   getting-started
   workflow
   architecture
   cli-reference
   api
