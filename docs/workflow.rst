Workflow
========

Execution Model
---------------

Audax runs as a bounded control loop with separate drafting and implementation
phases:

.. code-block:: text

   user task
     -> Claude drafts mission_spec.md
     -> Codex reviews the draft against the task and repo rules
     -> human approval by default
     -> mission spec is locked as markdown + SHA-256 checksum manifest
     -> Claude implements against the locked mission
     -> Codex reviews the live repository state
     -> repeat until success or round limit

Mission Drafting
----------------

During mission drafting, Audax builds a repository-context snapshot from files
such as ``CLAUDE.md``, ``AGENTS.md``, ``CONTRIBUTING.md``, and ``README.md``.
Claude is then asked to produce a markdown file with a strict structure:

* Mission
* Mission Success Criteria
* Required Behaviors
* Test Plan
* Constraints And Non-Goals

Codex reviews that draft through a JSON schema instead of free-form prose. If
Codex rejects the draft, Audax renders the issues into compact feedback and
feeds them into the next Claude round. If the drafting budget is exhausted
before Codex approves, Audax ships the latest draft for a final human decision
and surfaces the latest reject message.

Mission Approval And Locking
----------------------------

By default, a user can:

* approve the draft and continue,
* request changes with explicit feedback, or
* abort the mission entirely.

Once the spec is accepted, Audax locks it by writing two artifacts:

* ``mission_spec.md``
* ``mission_spec.lock.json``

Both files live inside a timestamped session directory beneath the workspace
root, so a later run never overwrites the forensic trail of an earlier run.

Subsequent implementation rounds verify the SHA-256 digest in the lock
manifest before and after Claude edits the repository. If the locked mission
markdown changes unexpectedly, the run fails immediately.

Implementation And Review
-------------------------

Implementation rounds keep Claude focused on the immutable mission by passing:

* the locked markdown contents,
* the markdown path,
* the SHA-256 digest of the locked markdown,
* repository policy context, and
* any structured feedback from the previous Codex review.

Codex then inspects the repository directly and returns a structured payload
with:

* ``mission_accomplished``
* ``has_issues``
* ``summary``
* ``issues``
* ``completed_criteria`` — mission success criteria currently met.
* ``remaining_criteria`` — mission success criteria still unmet.
* ``progress_pct`` — integer 0-100 grounded in the completed vs remaining
  split.

The loop only succeeds when the mission is fully accomplished and no issues
remain.

After each implementation round, Audax prints a three-box **Round Report** to
the terminal:

1. **Implementer** — the ``Accomplished`` / ``Tests Run`` / ``Remaining
   Risks`` sections parsed from the implementer's markdown summary.
2. **Reviewer** — ``mission_accomplished`` / ``has_issues`` flags, the
   review summary, and each outstanding issue rendered with severity and
   category tags.
3. **Progress** — a color-coded progress bar with the percentage plus a
   two-column completed-vs-remaining list of criteria.

The boxes use bold labels and color-coded severity tags so the important
signals are easy to spot without wading through plain text.

Session Forensics
-----------------

Audax keeps every session self-contained for ex post analysis:

* Exact prompts sent to Claude and Codex are preserved under ``prompts/``.
* Claude outputs are preserved under ``claude/``.
* Codex structured reviews are preserved under ``codex/``.
* ``events.jsonl`` records the session chronology as append-only JSON Lines,
  which makes it straightforward to grep, diff, stream, or post-process with
  tools like ``jq``.
* ``session_manifest.json`` captures stable session metadata and the artifact
  inventory in a single structured document.

Failure Semantics
-----------------

Audax is designed to leave a useful trail even when a run fails:

* Mission-spec failures still write the partial draft and the run report.
* Implementation failures still preserve per-round prompts, Claude outputs, and
  Codex reviews.
* ``run_report.json`` records the actual number of rounds completed before the
  error, not just the successful terminal state.
* Interrupted runs are marked as interrupted in the session metadata instead of
  being silently truncated.
* Claude and Codex subprocesses run without a timeout by default; pass
  ``--subprocess-timeout-seconds`` to opt in to a hard ceiling on wedged
  external CLIs.

Resuming After Disruption
-------------------------

A session that was killed mid-implementation is recoverable as long as its
mission spec was already locked. Run ``python audax.py continue`` to pick up
the most recent incomplete session, or ``python audax.py continue
<session_id>`` to target a specific one. Resume skips drafting and approval
entirely: the existing ``mission_spec.md`` and ``mission_spec.lock.json`` are
rehydrated, the SHA-256 digest is re-verified, and the implementation loop
restarts. The last Codex implementation review from the prior session is
also rehydrated as the first resumed round's reviewer feedback, so the
unresolved objections flow forward as structured input rather than being
rediscovered from repo state. See :doc:`cli-reference` for all resume flags.
