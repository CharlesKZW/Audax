"""Schemas and parsing helpers for Codex review payloads."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import ImplementationReview, MissionReview, ReviewIssue


def mission_review_schema() -> dict[str, Any]:
    """Return the JSON schema for mission-spec review responses."""
    return {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {
                "type": "array",
                "items": issue_schema(include_category=False),
            },
        },
        "required": ["approved", "summary", "issues"],
        "additionalProperties": False,
    }


def implementation_review_schema() -> dict[str, Any]:
    """Return the JSON schema for implementation review responses."""
    return {
        "type": "object",
        "properties": {
            "mission_accomplished": {"type": "boolean"},
            "has_issues": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {
                "type": "array",
                "items": issue_schema(include_category=True),
            },
        },
        "required": ["mission_accomplished", "has_issues", "summary", "issues"],
        "additionalProperties": False,
    }


def issue_schema(*, include_category: bool) -> dict[str, Any]:
    """Return the JSON schema fragment used for structured review issues."""
    properties: dict[str, Any] = {
        "severity": {"type": "string"},
        "title": {"type": "string"},
        "details": {"type": "string"},
        "suggested_fix": {"type": "string"},
    }
    required = ["severity", "title", "details", "suggested_fix"]
    if include_category:
        properties["category"] = {"type": "string"}
        required.append("category")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def parse_mission_review(payload: dict[str, Any]) -> MissionReview:
    """Convert a raw mission review payload into a typed model."""
    return MissionReview(
        approved=bool(payload.get("approved", False)),
        summary=str(payload.get("summary", "")).strip(),
        issues=parse_issues(payload.get("issues", []), default_category="spec_gap"),
    )


def parse_implementation_review(payload: dict[str, Any]) -> ImplementationReview:
    """Convert a raw implementation review payload into a typed model."""
    return ImplementationReview(
        mission_accomplished=bool(payload.get("mission_accomplished", False)),
        has_issues=bool(payload.get("has_issues", False)),
        summary=str(payload.get("summary", "")).strip(),
        issues=parse_issues(payload.get("issues", []), default_category="issue"),
    )


def parse_issues(payload: Any, *, default_category: str) -> list[ReviewIssue]:
    """Parse a best-effort list of review issues from arbitrary JSON."""
    issues: list[ReviewIssue] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            ReviewIssue(
                severity=str(item.get("severity", "medium")).strip() or "medium",
                title=str(item.get("title", "")).strip(),
                details=str(item.get("details", "")).strip(),
                category=str(item.get("category", default_category)).strip() or default_category,
                suggested_fix=str(item.get("suggested_fix", "")).strip(),
            )
        )
    return issues


def mission_review_to_dict(review: MissionReview) -> dict[str, Any]:
    """Serialize a mission review model into a JSON-friendly dictionary."""
    return {
        "approved": review.approved,
        "summary": review.summary,
        "issues": [asdict(issue) for issue in review.issues],
    }


def implementation_review_to_dict(review: ImplementationReview) -> dict[str, Any]:
    """Serialize an implementation review model into a JSON-friendly dictionary."""
    return {
        "mission_accomplished": review.mission_accomplished,
        "has_issues": review.has_issues,
        "summary": review.summary,
        "issues": [asdict(issue) for issue in review.issues],
    }


def render_review_feedback(issues: list[ReviewIssue], *, summary: str) -> str:
    """Render review issues into the plain-text feedback loop fed back to Claude."""
    if not issues:
        return summary.strip() or "No review issues recorded."

    lines: list[str] = []
    if summary.strip():
        lines.append(f"Summary: {summary.strip()}")
        lines.append("")
    for idx, issue in enumerate(issues, start=1):
        lines.append(
            f"{idx}. [{issue.severity.upper()} | {issue.category}] {issue.title}\n"
            f"{issue.details}"
        )
        if issue.suggested_fix:
            lines.append(f"Suggested fix: {issue.suggested_fix}")
        lines.append("")
    return "\n".join(lines).strip()


def combine_spec_feedback(*, user_feedback: str, codex_feedback: str) -> str:
    """Compose user-approval and Codex review feedback for the next spec round.

    User feedback must persist across Codex rejection rounds until the user re-approves,
    so both sources are labelled and concatenated rather than overwritten.
    """
    parts: list[str] = []
    if user_feedback.strip():
        parts.append(
            "User-requested changes (preserve across rounds until satisfied):\n"
            f"{user_feedback.strip()}"
        )
    if codex_feedback.strip():
        parts.append(f"Reviewer feedback:\n{codex_feedback.strip()}")
    return "\n\n".join(parts)
