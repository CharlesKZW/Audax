"""Prompt builders for spec drafting, implementation, and review rounds."""

from __future__ import annotations

from pathlib import Path
import textwrap

from .models import LockedMissionSpec, MISSION_MODE_DIRECT, MISSION_MODE_SPEC


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
        Return a terse mission spec as markdown in your response text only.
        Do NOT create, write, or edit any file on disk. The orchestrator
        captures your response and persists it under the session directory.

        Format:
        - Return only a markdown bullet list.
        - Use 1-9 bullets.
        - Do not include headings, sections, prose introductions, a task plan,
          a test plan, or implementation notes.

        Rules:
        - Treat the spec as a human approval artifact for nontechnical users:
          every bullet must be understandable without implementation context.
        - Each bullet must describe one qualitative behavior or user-visible
          outcome that defines mission success.
        - Every bullet must be absolutely necessary for mission success.
        - Write the shortest complete draft; prefer broad, non-overlapping
          behaviors over detailed sub-requirements.
        - Omit background, rationale, restatements, nice-to-haves, obvious
          implementation hygiene, and low-risk edge cases.
        - Avoid technical details, implementation steps, file paths,
          function/class names, test IDs/selectors, fixture names, test names,
          exact UI strings, and command names unless the user explicitly made
          that literal part of the public contract.
        - Avoid quantitative thresholds, counts, timings, percentages, and
          other numeric acceptance details unless the user explicitly requested
          that exact public behavior.
        - When the request is ambiguous, prefer the more audacious interpretation.
        - Capture the spirit of the user request, not just the narrowest wording,
          while keeping only the behaviors required to judge success.
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

        Also return high_stakes_decisions: a short list of the major
        user-visible, public-contract, data, security, integration, destructive
        change, migration/rollback, or scope decisions worth explicit human
        approval. Do not list low-level implementation details. Return an empty
        list when there are no such decisions.

        Approval standard:
        - Approve only if the spec fully captures the spirit of the user request.
        - Approve only if the spec is only a markdown bullet list with 1-9
          bullets.
        - Approve only if every bullet is a qualitative behavior or
          user-visible outcome that a nontechnical user can understand.
        - Approve only if every bullet is absolutely necessary for mission
          success.
        - Reject unnecessary qualitative behaviors that are not required to
          satisfy the original request.
        - Reject technical details, implementation steps, file paths,
          function/class names, test IDs/selectors, fixture names, test names,
          exact UI strings, command names, quantitative thresholds, counts,
          timings, percentages, and other numeric acceptance details unless the
          user explicitly requested that literal public contract.
        - Reject background, rationale, restatements, nice-to-haves, obvious
          implementation hygiene, low-risk edge cases, duplicate points,
          overlapping bullets, and other low-signal lines.
        - Prefer concise drafts; reject bloated or over-prescriptive specs.
        - When the spec is underspecified, prefer rejecting it and asking for the more audacious version.

        Issues must describe only the problem, evidence, severity, and why it blocks approval.
        Do not prescribe fixes or implementation strategy; the implementer owns the solution.
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
    mission_mode: str = MISSION_MODE_SPEC,
) -> str:
    """Construct the Claude prompt for an implementation round."""
    feedback_block = (
        "No outstanding reviewer feedback."
        if not review_feedback.strip()
        else review_feedback
    )
    if mission_mode == MISSION_MODE_DIRECT:
        intro = "You are implementing a locked direct instruction in the current repository."
        locked_heading = "Locked direct instruction"
        lock_notice = (
            "The original user request is the locked mission contract. "
            "Do not modify direct_instruction.txt or direct_instruction.lock.json."
        )
        contents_heading = "Locked direct instruction contents"
        implementation_rule = (
            "Implement the original user request directly in the repository."
        )
        testing_rule = (
            "Implement automated tests for deterministic, testable outcomes that "
            "show the original user request is satisfied."
        )
    else:
        intro = "You are implementing an immutable mission in the current repository."
        locked_heading = "Locked mission spec"
        lock_notice = (
            "The mission spec is locked. "
            "Do not modify mission_spec.md or mission_spec.lock.json."
        )
        contents_heading = "Locked mission_spec.md contents"
        implementation_rule = "Implement all remaining mission requirements directly in the repository."
        testing_rule = (
            "Implement automated tests for mission behavior criteria that can be "
            "covered with deterministic checks."
        )
    return textwrap.dedent(
        f"""
        {intro}

        Original user request:
        {task}

        {locked_heading}:
        - Text path: {mission_md_path}
        - Locked text sha256: {locked_spec.markdown_sha256}

        {lock_notice}

        Repo policy context:
        {repo_context}

        {contents_heading}:
        {mission_spec}

        Reviewer feedback to address:
        {feedback_block}

        Instructions:
        - {implementation_rule}
        - Respect repo rules such as tests, documentation, and synchronization requirements.
        - {testing_rule}
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
    mission_mode: str = MISSION_MODE_SPEC,
) -> str:
    """Construct the Codex review prompt for the current repository state."""
    if mission_mode == MISSION_MODE_DIRECT:
        review_intro = (
            "Review the current repository state against the original user request "
            "and repo policy context."
        )
        locked_heading = "Locked direct instruction"
        contents_heading = "Locked direct instruction contents"
        progress_block = textwrap.dedent(
            """
            Progress reporting (required fields):
            - completed_criteria: list of short human-readable descriptions of
              each distinct user-visible or repository-significant requirement
              from the original request that is currently met.
            - remaining_criteria: list of short human-readable descriptions of
              each distinct requirement from the original request that is NOT
              yet met.
            - progress_pct: integer 0-100 estimating overall mission
              completion, grounded in the completed vs remaining split. Use the
              exact ratio when possible (e.g. 3 of 5 criteria met -> 60).
            - When the original request does not enumerate discrete criteria,
              decompose it into the minimum coherent set needed to judge
              completion, then cover each decomposed criterion exactly once
              across completed_criteria and remaining_criteria.
            """
        ).strip()
    else:
        review_intro = (
            "Review the current repository state against the locked mission spec "
            "and repo policy context."
        )
        locked_heading = "Locked mission spec"
        contents_heading = "Locked mission_spec.md contents"
        progress_block = textwrap.dedent(
            """
            Progress reporting (required fields):
            - completed_criteria: list of short human-readable descriptions of
              each mission behavior bullet that is currently met.
            - remaining_criteria: list of short human-readable descriptions of
              each mission behavior bullet that is NOT yet met.
            - progress_pct: integer 0-100 estimating overall mission completion,
              grounded in the completed vs remaining split. Use the exact ratio
              when possible (e.g. 3 of 5 criteria met -> 60).
            - Draw completed_criteria and remaining_criteria directly from the
              mission spec's qualitative behavior bullets; together they should
              cover every bullet exactly once.
            """
        ).strip()
    return textwrap.dedent(
        f"""
        {review_intro}
        Inspect the repository directly. Do not rely only on Claude's summary.

        Original user request:
        {task}

        {locked_heading}:
        - Text path: {mission_md_path}
        - Locked text sha256: {locked_spec.markdown_sha256}

        Repo policy context:
        {repo_context}

        {contents_heading}:
        {mission_spec}

        Claude implementation summary:
        {claude_summary}

        Return JSON only.

        Review standard:
        - mission_accomplished is true only if the mission spec is fully satisfied.
        - has_issues is true if there is any bug, missing requirement, repo policy violation, or testing gap.
        - Use issue categories such as bug, missing_requirement, repo_policy, or test_gap.
        - Missing automated tests for deterministic, testable mission outcomes is a test_gap.
        - When the mission touches a web app or browser UI, use end-to-end Playwright checks against the running app when feasible; do not rely only on static inspection or unit/integration test output for critical user flows.
        - If the repo appears to ship a web app and feasible Playwright validation for critical user flows was not performed, treat that as a test_gap unless there is strong repository evidence that equivalent browser-level coverage already exists.
        - If the implementation is clean but incomplete, still report issues and set mission_accomplished to false.
        - Issues must describe only the problem, evidence, severity, and why it blocks completion.
        - Do not prescribe fixes or implementation strategy; the implementer owns the solution.

        {progress_block}
        """
    ).strip()
