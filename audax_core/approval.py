"""Interactive mission approval helpers."""

from __future__ import annotations

from pathlib import Path

from .models import ApprovalDecision


def _normalize_response(response: str) -> str:
    """Collapse whitespace and punctuation variants in approval responses."""
    return " ".join(response.strip().lower().replace("-", " ").split())


def interactive_mission_approval(mission_spec: str, mission_spec_path: Path) -> ApprovalDecision:
    """Collect a terminal approval decision for a drafted mission spec."""
    print(f"\nMission spec ready for approval: {mission_spec_path}")
    print("\n--- mission_spec.md ---")
    print(mission_spec.rstrip())
    print("--- end mission_spec.md ---\n")

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
            print("Enter requested changes. Submit an empty line to finish.")
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
            print("Requested changes were empty.")
            continue
        print("Please answer: approve, request changes, or abort.")
