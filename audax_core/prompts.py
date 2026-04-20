"""Prompt builders for spec drafting, implementation, and review rounds."""

from __future__ import annotations

from pathlib import Path
import textwrap

from .models import LockedMissionSpec


def build_mission_spec_prompt(
    *,
    task: str,
    repo_context: str,
    current_spec: str,
    pending_feedback: str,
) -> str:
    """Construct the Claude prompt used to draft or revise ``mission_spec.md``."""
    mode_instructions = (
        "Create a new draft for the mission spec."
        if not current_spec
        else "Revise the existing mission spec draft."
    )
    current_block = (
        ""
        if not current_spec
        else f"\nExisting mission spec draft:\n{current_spec}\n"
    )
    feedback_block = (
        "\nFeedback to address:\nNone.\n"
        if not pending_feedback.strip()
        else f"\nFeedback to address:\n{pending_feedback}\n"
    )
    return textwrap.dedent(
        f"""
        You are preparing the mission spec for an autonomous coding mission.
        {mode_instructions}

        Original user request:
        {task}

        Repo policy context:
        {repo_context}
        {current_block}
        {feedback_block}
        Return the mission spec as markdown in your response text only.
        Do NOT create, write, or edit any file on disk. The orchestrator
        captures your response and persists it under the session directory.

        Use these sections exactly:
        1. Mission
        2. Mission Success Criteria
        3. Test Plan

        Rules:
        - Keep the draft concise and avoid duplicate or low-signal bullets.
        - Every requirement must be falsifiable and observable.
        - Put required behaviors inside Mission Success Criteria; do not create a separate Required Behaviors section.
        - When the request is ambiguous, prefer the more audacious interpretation.
        - Any success criterion that can be expressed as a deterministic test should be implemented as a test and reflected in the Test Plan.
        - The Test Plan must directly prove or falsify the success criteria.
        - Capture the spirit of the user request, not just the narrowest wording.
        - Do not include meta commentary, chain-of-thought, or explanations outside the markdown body.
        """
    ).strip()


def build_mission_review_prompt(*, task: str, repo_context: str, mission_spec: str) -> str:
    """Construct the Codex review prompt for a drafted mission spec."""
    return textwrap.dedent(
        f"""
        Review the draft mission_spec.md against the original user request and repo policy context.

        Original user request:
        {task}

        Repo policy context:
        {repo_context}

        Draft mission_spec.md:
        {mission_spec}

        Return JSON only.

        Also return high_stakes_decisions: a short list of the specific decisions
        worth explicit human approval because they materially affect scope,
        user-visible behavior, destructive change risk, migration/rollback
        posture, or other controversial tradeoffs. Return an empty list when
        there are no such decisions.

        Approval standard:
        - Approve only if the spec fully captures the spirit of the user request.
        - Approve only if each success criterion is falsifiable and concrete.
        - Approve only if Mission Success Criteria already includes the required behaviors instead of splitting them into a second section.
        - Approve only if the Test Plan can actually verify the mission.
        - Approve only if deterministic, testable criteria are represented as tests in the Test Plan.
        - Prefer concise drafts; reject bloated or duplicative specs.
        - When the spec is underspecified, prefer rejecting it and asking for the more audacious version.

        Every issue should explain what is missing or too weak and how to strengthen it.
        """
    ).strip()


def build_implementation_prompt(
    *,
    task: str,
    repo_context: str,
    mission_spec: str,
    mission_md_path: Path,
    locked_spec: LockedMissionSpec,
    review_feedback: str,
) -> str:
    """Construct the Claude prompt for an implementation round."""
    feedback_block = (
        "No outstanding reviewer feedback."
        if not review_feedback.strip()
        else review_feedback
    )
    return textwrap.dedent(
        f"""
        You are implementing an immutable mission in the current repository.

        Original user request:
        {task}

        Locked mission spec:
        - Markdown path: {mission_md_path}
        - Mission markdown sha256: {locked_spec.markdown_sha256}

        The mission spec is locked. Do not modify mission_spec.md or mission_spec.lock.json.

        Repo policy context:
        {repo_context}

        Locked mission_spec.md contents:
        {mission_spec}

        Reviewer feedback to address:
        {feedback_block}

        Instructions:
        - Implement all remaining mission requirements directly in the repository.
        - Respect repo rules such as tests, documentation, and synchronization requirements.
        - Implement automated tests for any mission success criteria that can be proven with deterministic tests.
        - Run the relevant tests or checks when possible.
        - Version control: if the repo is a git repository, commit logical
          chunks of work as you make them with clear, descriptive commit
          messages. Prefer several small, reviewable commits over one
          monolithic dump at the end. Do not push. Do not modify git config.
          Audax will make a final sweeper commit after your round to capture
          any trailing uncommitted work, so there is no need to batch
          everything into one commit yourself.
        - Return a concise markdown summary with these sections exactly:
          - Accomplished
          - Tests Run
          - Remaining Risks
        """
    ).strip()


def build_implementation_review_prompt(
    *,
    task: str,
    repo_context: str,
    mission_spec: str,
    mission_md_path: Path,
    claude_summary: str,
    locked_spec: LockedMissionSpec,
) -> str:
    """Construct the Codex review prompt for the current repository state."""
    return textwrap.dedent(
        f"""
        Review the current repository state against the locked mission spec and repo policy context.
        Inspect the repository directly. Do not rely only on Claude's summary.

        Original user request:
        {task}

        Locked mission spec:
        - Markdown path: {mission_md_path}
        - Mission markdown sha256: {locked_spec.markdown_sha256}

        Repo policy context:
        {repo_context}

        Locked mission_spec.md contents:
        {mission_spec}

        Claude implementation summary:
        {claude_summary}

        Return JSON only.

        Review standard:
        - mission_accomplished is true only if the mission spec is fully satisfied.
        - has_issues is true if there is any bug, missing requirement, repo policy violation, or testing gap.
        - Use issue categories such as bug, missing_requirement, repo_policy, or test_gap.
        - Missing automated tests for deterministic, testable success criteria is a test_gap.
        - If the implementation is clean but incomplete, still report issues and set mission_accomplished to false.

        Progress reporting (required fields):
        - completed_criteria: list of short human-readable descriptions of
          each mission success criterion that is currently met.
        - remaining_criteria: list of short human-readable descriptions of
          each mission success criterion that is NOT yet met.
        - progress_pct: integer 0-100 estimating overall mission completion,
          grounded in the completed vs remaining split. Use the exact ratio
          when possible (e.g. 3 of 5 criteria met -> 60).
        - Draw completed_criteria and remaining_criteria directly from the
          mission spec's Mission Success Criteria section; together they
          should cover every criterion exactly once.
        """
    ).strip()
