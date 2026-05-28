Audax — The Pitch
=================

.. container:: hero

   **Land the highest-stakes changes in finance code — the ones a single AI
   agent botches in one shot — with the discipline of code review built into
   the loop.**

The Problem
-----------

In quantitative finance and fintech engineering, the most valuable code
changes are exactly the ones you cannot afford to get subtly wrong:

* refactoring a **risk or pricing model**,
* migrating a **backtesting or market-data pipeline**,
* changing a **portfolio-construction or P&L engine**,
* building an **extraction pipeline over filings and disclosures**.

The failure mode here is not a crash. A silent **lookahead-leakage** bug in a
backtest, or an off-by-one in a P&L calculation, does not throw an error — it
produces a *confident, wrong number* that can flow straight into a research
result or a trade.

Frontier coding agents are excellent in the small, but on changes this large
they routinely drift: they miss a requirement, introduce a regression, and
then report success anyway. Today the only remedy is a human babysitting the
agent — clarifying, re-prompting, and catching regressions by hand, round
after round. That does not scale, and it is most fragile precisely where
correctness matters most.

The Solution
------------

**Audax replaces that ad hoc human-in-the-loop with a disciplined, auditable
build-review loop between two independent frontier models.**

#. The human states the mission **once**. Audax locks the original prompt as a
   contract (text + a SHA-256 manifest) so the agent cannot quietly redefine
   its own task mid-run.
#. **Claude** implements against the locked mission.
#. **Codex** independently reviews the *live repository state* and returns
   structured JSON findings — bugs, missing requirements, repo-policy gaps,
   and test gaps — rather than free-form prose.
#. The **orchestrator** feeds those findings back into the implementer and
   loops, round after round, until the mission is satisfied or the round
   budget is spent.

Every prompt, every implementation output, every review, and an append-only
``events.jsonl`` chronology are persisted to a timestamped session directory,
so any run is reproducible and inspectable after the fact.

Why This Is Different
---------------------

* **Two model families, two roles.** The implementer and the reviewer are
  different frontier models. The reviewer's job is to *refuse to sign off* on
  work that is almost-but-not-quite right — the judgment a human reviewer
  normally supplies.
* **A contract that can't drift.** The locked, hash-verified mission is
  re-checked around every round; if the contract text is mutated, the run
  fails fast.
* **An audit trail by construction.** For regulated and finance workflows,
  "the agent did it" is not enough — you need to show *what* it did and *why*
  a change was accepted. Audax produces that trail automatically.

The Prototype Works
-------------------

Audax is a **working, open-source command-line tool — not a slide deck or a
mockup.** Anyone can clone the repository and run it:

.. code-block:: bash

   pip install -r requirements.txt
   python audax.py "Refactor the risk-model module and add regression tests"

A real run produces:

* a per-round **Round Report** in the terminal (what was accomplished, the
  reviewer's color-coded findings, and a progress bar over the mission
  criteria),
* a full **session directory** of prompts, outputs, and reviews under
  ``audax_artifacts/``, and
* a **resumable session** — ``python audax.py continue`` picks an interrupted
  run back up against its already-locked contract.

.. note::

   Audax requires two external CLIs — ``claude`` and ``codex`` — installed and
   authenticated on your ``PATH``. It shells out to them rather than calling
   any API directly, so there are no keys to manage in the repo. See
   :doc:`getting-started` for setup.

Honest Limitations
------------------

.. warning::

   * **Not battle-tested.** Treat odd artifacts and unusual agent behavior as
     expected until you have built your own confidence.
   * **Expensive and slow.** Defaults run frontier models at maximum reasoning
     effort across multiple rounds. This optimizes for output quality over
     price and latency.
   * **Safety rails are off by design.** Both CLIs run with their interactive
     permission and sandbox protections disabled so the loop can proceed
     autonomously — so Audax should only be pointed at a disposable worktree,
     container, or VM, never your main tree.
   * **The implementer/reviewer split is a hypothesis,** based on informal
     observation rather than a benchmark.

What's Next
-----------

The clearest path to proving the core claim is a **finance-specific
benchmark**: a frozen task set covering model auditing, lookahead-leakage
detection, SEC-style numeric extraction, and risk reasoning, run against
one-shot Claude, one-shot Codex, and the full Audax loop. The headline metric
is simple — *how often does the review loop catch a finance-specific error
that a one-shot model ships?* That is the number that decides whether the
extra cost and latency are worth it. Cheaper model tiers and a hosted runner
round out the roadmap.
