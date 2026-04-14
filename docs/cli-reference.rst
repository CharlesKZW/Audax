CLI Reference
=============

Synopsis
--------

.. code-block:: text

   python audax.py [options] [task ...]
   python audax.py continue [options] [session_id]

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
   Hard timeout for each Claude or Codex subprocess, in seconds. **Unset by
   default**, which means Audax never kills an agent subprocess on its own.
   Pass a positive number to cap hung runs, or ``0`` to explicitly disable
   the timeout.

``--claude-cmd``
   Override the Claude CLI executable name or path. Defaults to ``claude`` or
   the ``CLAUDE_CMD`` environment variable when set.

``--codex-cmd``
   Override the Codex CLI executable name or path. Defaults to ``codex`` or the
   ``CODEX_CMD`` environment variable when set.

``--auto-commit`` / ``--no-auto-commit``
   Commit repository changes after each implementation round. **Enabled by
   default.**

   The **implementer** (Claude by default, Codex on fallback) is instructed
   in its prompt to commit logical chunks of work as it goes — so a single
   round may produce multiple implementer-authored commits. After the
   round, Audax runs a **sweeper** (``git add -A`` followed by ``git
   commit`` with a deterministic message: title ``audax round <N>: <first
   line of Accomplished>`` and trailer lines ``Audax-Session: <id>`` and
   ``Audax-Round: <N>``) to capture any trailing uncommitted work. If the
   implementer already committed everything, the sweeper is a no-op.

   The orchestrator then enumerates every commit that landed during the
   round — both the implementer's and the sweeper's — in chronological
   order, and prints one line per commit to stdout (tagged
   ``[implementer]`` or ``[sweeper]``). The full list is also persisted in
   a single ``auto_commit_round`` event in ``events.jsonl``.

   If the target directory is not a git repository, auto-commit is skipped
   with a single log line and no commits are attempted.

``--session-branch`` / ``--no-session-branch``
   Check out a fresh ``audax/<session_id>`` branch at session start and
   commit rounds onto it. **Off by default** (commits land on the current
   branch). Useful as a lightweight isolation story — merge the branch back
   with ``git merge audax/<session_id>`` when satisfied, or delete it.

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

The ``continue`` Subcommand
---------------------------

Resume an interrupted session against its already-locked mission spec
instead of starting a fresh one. Only sessions that have a
``mission_spec.lock.json`` and have not already succeeded are resumable.
The SHA-256 digest in the lock manifest is re-verified before the
implementation loop restarts, so a mutated ``mission_spec.md`` causes the
resume to fail fast.

.. code-block:: bash

   python audax.py continue
   python audax.py continue 20260413T181500Z_pid42

``session_id`` (positional, optional)
   Session directory name under ``audax_artifacts/sessions/``. When omitted,
   the most recent incomplete session in the workspace is resumed.

``--implementation-rounds``
   Maximum number of implementation-review rounds in the resumed run.
   Default: ``5``.

``--workspace-dir``
   Workspace root to scan for the session. Default: ``audax_artifacts``.

``--heartbeat-seconds``
   Interval between sparse progress updates. Default: ``5`` seconds.

``--subprocess-timeout-seconds``
   Same semantics as the fresh-run flag. Unset by default.

``--claude-cmd`` / ``--codex-cmd``
   Override the backend CLI executables.

``--auto-commit`` / ``--no-auto-commit``
   Same semantics as the fresh-run flag. Enabled by default; resume
   continues committing rounds onto the current branch.

``--session-branch`` / ``--no-session-branch``
   Same semantics as the fresh-run flag. Off by default. If the branch
   already exists from the original session, pass ``--session-branch`` on
   resume only if you have manually re-checked-out that branch; otherwise
   just leave auto-commit on the current branch.

Drafting and approval are skipped on resume; only the implementation loop
runs. New prompt and output artifacts are written into the existing session
directory with fresh timestamps, and an explicit ``session_resumed`` event
is appended to ``events.jsonl`` so the chronology is preserved.

**Prior-review rehydration.** When the last completed round before the
disruption ended with Codex flagging unresolved issues, those issues are
rehydrated from the session's latest ``codex/*_implementation_codex_round_*
.json`` review and fed to Claude as the first resumed round's
``Reviewer feedback`` block. This preserves the invariant that every Codex
judgment eventually reaches Claude — the reviewer's last word survives the
resume boundary instead of being rediscovered by repo inspection alone. A
``resume_feedback_rehydrated`` event is appended to ``events.jsonl`` when
this happens.

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
