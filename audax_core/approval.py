"""Interactive mission approval helpers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TextIO

from .models import ApprovalDecision, MissionReview
from .ui import render_mission_approval_card


def _normalize_response(response: str) -> str:
    """Collapse whitespace and punctuation variants in approval responses."""
    return " ".join(response.strip().lower().replace("-", " ").split())


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
            feedback = "\n".join(lines).strip()
            if feedback:
                return ApprovalDecision(approved=False, feedback=feedback)
            target.write("Requested changes were empty.\n")
            target.flush()
            continue
        target.write("Please answer: approve, request changes, or abort.\n")
        target.flush()
