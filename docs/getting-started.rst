Getting Started
===============

Prerequisites
-------------

Audax is intentionally lightweight, but it does assume a few tools are already
available in your environment:

* Python 3.12 or newer
* The ``claude`` CLI, installed and authenticated
* The ``codex`` CLI, installed and authenticated
* ``pytest`` for the local test suite

Sphinx itself is only required if you want to build this documentation site.

Repository Setup
----------------

Clone the repository and work from the project root so the CLI can resolve the
current repository as the active mission target.

To install the documentation dependencies in one step:

.. code-block:: bash

   python -m pip install -r docs/requirements.txt

Running Audax
-------------

The simplest invocation passes the mission request as positional arguments:

.. code-block:: bash

   python audax.py "Add JWT auth middleware with refresh token rotation"

Useful runtime options:

* ``--spec-rounds`` bounds the number of draft-and-review cycles used to refine
  ``mission_spec.md``.
* ``--implementation-rounds`` bounds the implementation-review loop after the
  mission is locked.
* ``--workspace-dir`` moves generated artifacts out of the default
  ``audax_artifacts`` directory.
* ``--require-approval`` keeps the default interactive approval gate enabled
  before the mission is locked.
* ``--no-require-approval`` disables that approval gate.
* ``--subprocess-timeout-seconds`` terminates a wedged ``claude`` or ``codex``
  subprocess instead of waiting forever.

Artifact Layout
---------------

Each run writes its state beneath the configured workspace directory. The
default layout is:

.. code-block:: text

   audax_artifacts/
     latest.json
     sessions/
       20260413T181500Z_pid42/
         session_manifest.json
         events.jsonl
         mission_spec.md
         mission_spec.pdf
         mission_spec.lock.json
         run_report.json
         prompts/
         claude/
         codex/

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Path
     - Purpose
   * - ``latest.json``
     - Pointer to the most recent session, useful when browsing many runs.
   * - ``sessions/<timestamp>_pid<id>/``
     - Timestamped session root for one Audax invocation. This makes session
       history durable and keeps each run self-contained.
   * - ``session_manifest.json``
     - Structured metadata for the run, including configuration, status,
       artifact inventory, and start/end timestamps.
   * - ``events.jsonl``
     - Append-only event log. JSON Lines is used here because it is stable,
       diffable, and easy to analyze after the fact with standard tools.
   * - ``mission_spec.md``
     - Locked mission source used by implementation and review prompts.
   * - ``mission_spec.pdf``
     - Human-readable immutable snapshot of the mission spec.
   * - ``mission_spec.lock.json``
     - Checksum manifest used to detect unauthorized changes to locked mission
       artifacts.
   * - ``prompts/``
     - Timestamped prompts sent to Claude and Codex for every mission and
       review round.
   * - ``claude/``
     - Timestamped Claude outputs for mission drafting and implementation
       rounds, stored as markdown or plain text.
   * - ``codex/``
     - Timestamped Codex structured review payloads, stored as JSON.
   * - ``run_report.json``
     - Final success or failure summary, including timestamps, round counts,
       and any error message.

Tests
-----

Run the local unit suite:

.. code-block:: bash

   pytest -q

Run the optional live smoke tests against the installed real CLIs:

.. code-block:: bash

   AUDAX_RUN_LIVE_CLI_TESTS=1 pytest -q tests/test_live_cli.py -vv

Building The Docs
-----------------

Build the HTML site with warnings treated as errors:

.. code-block:: bash

   python -m sphinx -W -b html docs docs/_build/html

The generated entry point will be:

.. code-block:: text

   docs/_build/html/index.html
