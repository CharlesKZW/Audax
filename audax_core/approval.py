"""Interactive mission approval helpers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TextIO

from .models import ApprovalDecision, MissionReview
from .ui import read_task_interactive, render_mission_approval_card, supports_rich_terminal


def _normalize_response(response: str) -> str:
    """Collapse whitespace and punctuation variants in approval responses."""
    return " ".join(response.strip().lower().replace("-", " ").split())


def _stdin_is_tty() -> bool:
    """Return whether stdin is attached to an interactive terminal."""
    isatty = getattr(sys.stdin, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def _read_requested_changes(target: TextIO) -> str:
    """Read human feedback for a rejected mission spec."""
    if supports_rich_terminal(target) and _stdin_is_tty():
        target.write("Enter requested changes for the mission spec.\n")
        target.write("Press Enter to submit · Option+Enter inserts a new line.\n")
        target.flush()
        return read_task_interactive(stream=target).strip()

    target.write("Enter requested changes. Submit an empty line to finish.\n")
    target.flush()
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            line = ""
        if not line:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def interactive_mission_approval(
    mission_spec: str,
    mission_spec_path: Path,
    review: MissionReview | None = None,
    stream: TextIO | None = None,
) -> ApprovalDecision:
    """Collect a terminal approval decision for a drafted mission spec."""
    target = stream or sys.stdout
    target.write("\n")
    target.write(
        render_mission_approval_card(
            mission_spec_path=mission_spec_path,
            mission_spec=mission_spec,
            review=review,
            stream=target,
        )
    )
    target.flush()

    while True:
        try:
            response = _normalize_response(
                input("Approve mission spec? [approve/request changes/abort]: ")
            )
        except EOFError:
            return ApprovalDecision(approved=False, aborted=True)

        if response in {"approve", "a", "yes", "y"}:
            return ApprovalDecision(approved=True)
        if response in {"abort", "q", "quit"}:
            return ApprovalDecision(approved=False, aborted=True)
        if response in {
            "request",
            "r",
            "change",
            "changes",
            "request change",
            "request changes",
            "needs changes",
            "need changes",
            "no",
            "n",
        }:
            feedback = _read_requested_changes(target)
            if feedback:
                return ApprovalDecision(approved=False, feedback=feedback)
            target.write("Requested changes were empty.\n")
            target.flush()
            continue
        target.write("Please answer: approve, request changes, or abort.\n")
        target.flush()
