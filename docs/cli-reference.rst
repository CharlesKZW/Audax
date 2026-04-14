CLI Reference
=============

Synopsis
--------

.. code-block:: text

   python audax.py [options] [task ...]

Arguments And Options
---------------------

``task``
   Mission request. If omitted, Audax reads stdin until EOF and uses the full
   captured text as the mission prompt.

``--spec-rounds``
   Maximum number of mission-drafting rounds before the run fails. Default:
   ``3``.

``--implementation-rounds``
   Maximum number of implementation-review rounds before the run fails.
   Default: ``5``.

``--workspace-dir``
   Output directory for generated mission artifacts. Audax writes timestamped
   per-run session directories underneath this root. Default:
   ``audax_artifacts`` relative to the repository root.

``--require-approval``
   Require an interactive approval decision before the mission spec is locked.
   Enabled by default.

``--no-require-approval``
   Disable the interactive mission approval gate and allow Audax to lock the
   latest draft automatically when spec rounds are exhausted.

``--heartbeat-seconds``
   Interval between sparse progress updates while Claude or Codex subprocesses
   are running. Default: ``5`` seconds.

``--subprocess-timeout-seconds``
   Hard timeout for each Claude or Codex subprocess. Default: ``1800`` seconds.
   Use ``0`` to disable the timeout.

``--claude-cmd``
   Override the Claude CLI executable name or path. Defaults to ``claude`` or
   the ``CLAUDE_CMD`` environment variable when set.

``--codex-cmd``
   Override the Codex CLI executable name or path. Defaults to ``codex`` or the
   ``CODEX_CMD`` environment variable when set.

Examples
--------

Run with a direct mission request:

.. code-block:: bash

   python audax.py "Add JWT auth middleware with refresh token rotation"

Run with tighter review bounds and a custom workspace:

.. code-block:: bash

   python audax.py \
     --spec-rounds 6 \
     --implementation-rounds 20 \
     --workspace-dir audax_auth_artifacts \
     "Harden the authentication stack and add integration coverage"

Run with interactive approval disabled:

.. code-block:: bash

   python audax.py --no-require-approval "Refactor the billing webhooks module"

Environment
-----------

``CLAUDE_CMD``
   Default executable override for the Claude CLI.

``CODEX_CMD``
   Default executable override for the Codex CLI.

``AUDAX_RUN_LIVE_CLI_TESTS``
   Enables the optional live CLI smoke tests under ``tests/test_live_cli.py``
   when set to ``1``.

Exit Codes
----------

``0``
   The mission loop completed successfully and the final implementation review
   reported no remaining issues.

``1``
   Audax rejected invalid CLI arguments, could not find a required external
   command, or encountered a runtime failure while drafting, locking, or
   reviewing the mission.

``130``
   Audax was interrupted, typically by ``Ctrl-C``.
