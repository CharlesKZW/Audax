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
        "Create a new draft for mission_spec.md."
        if not current_spec
        else "Revise the existing mission_spec.md draft."
    )
    current_block = (
        ""
        if not current_spec
        else f"\nExisting mission_spec.md draft:\n{current_spec}\n"
    )
    feedback_block = (
        "\nFeedback to address:\nNone.\n"
        if not pending_feedback.strip()
        else f"\nFeedback to address:\n{pending_feedback}\n"
    )
    return textwrap.dedent(
        f"""
        You are preparing mission_spec.md for an autonomous coding mission.
        {mode_instructions}

        Original user request:
        {task}

        Repo policy context:
        {repo_context}
        {current_block}
        {feedback_block}
        Write markdown only for mission_spec.md. Use these sections exactly:
        1. Mission
        2. Mission Success Criteria
        3. Required Behaviors
        4. Test Plan
        5. Constraints And Non-Goals

        Rules:
        - Every requirement must be falsifiable and observable.
        - When the request is ambiguous, prefer the more audacious interpretation.
        - The Test Plan must directly prove or falsify the success criteria.
        - Capture the spirit of the user request, not just the narrowest wording.
        - Do not include meta commentary, chain-of-thought, or explanations outside the markdown file.
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

        Approval standard:
        - Approve only if the spec fully captures the spirit of the user request.
        - Approve only if each success criterion is falsifiable and concrete.
        - Approve only if the Test Plan can actually verify the mission.
        - When the spec is underspecified, prefer rejecting it and asking for the more audacious version.

        Every issue should explain what is missing or too weak and how to strengthen it.
        """
    ).strip()


def build_implementation_prompt(
    *,
    task: str,
    repo_context: str,
    mission_spec: str,
    mission_pdf_path: Path,
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
        - Markdown path: {mission_pdf_path.with_suffix('.md')}
        - PDF path: {mission_pdf_path}
        - Mission markdown sha256: {locked_spec.markdown_sha256}
        - Mission PDF sha256: {locked_spec.pdf_sha256}

        The mission spec is locked. Do not modify mission_spec.md, mission_spec.pdf, or mission_spec.lock.json.

        Repo policy context:
        {repo_context}

        Locked mission_spec.md contents:
        {mission_spec}

        Reviewer feedback to address:
        {feedback_block}

        Instructions:
        - Implement all remaining mission requirements directly in the repository.
        - Respect repo rules such as tests, documentation, and synchronization requirements.
        - Run the relevant tests or checks when possible.
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
    mission_pdf_path: Path,
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
        - Markdown path: {mission_pdf_path.with_suffix('.md')}
        - PDF path: {mission_pdf_path}
        - Mission markdown sha256: {locked_spec.markdown_sha256}
        - Mission PDF sha256: {locked_spec.pdf_sha256}

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
        - If the implementation is clean but incomplete, still report issues and set mission_accomplished to false.
        """
    ).strip()
