"""Shared data models, defaults, and backend protocols for Audax."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Protocol

DEFAULT_SPEC_ROUNDS = 10
DEFAULT_IMPLEMENTATION_ROUNDS = 50
DEFAULT_HEARTBEAT_SECONDS = 5.0
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 1800.0
DEFAULT_WORKSPACE_DIR = ".audax"
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
CODEX_CMD = os.environ.get("CODEX_CMD", "codex")
RULE_FILENAMES = (
    "CLAUDE.md",
    "Claude.md",
    "AGENTS.md",
    "agents.md",
    "CONTRIBUTING.md",
    "README.md",
)
MAX_RULE_FILES = 8
MAX_RULE_BYTES = 24_000


@dataclass
class LoopConfig:
    """Runtime configuration for a single Audax mission run."""

    repo_root: Path
    workspace_dir: Path
    max_spec_rounds: int = DEFAULT_SPEC_ROUNDS
    max_implementation_rounds: int = DEFAULT_IMPLEMENTATION_ROUNDS
    require_mission_approval: bool = False
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    subprocess_timeout_seconds: float | None = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    claude_cmd: str = CLAUDE_CMD
    codex_cmd: str = CODEX_CMD


@dataclass
class MissionArtifacts:
    """Paths for all generated artifacts produced during a mission run."""

    workspace_dir: Path
    mission_spec_md: Path
    mission_spec_pdf: Path
    mission_spec_lock: Path
    logs_dir: Path
    reviews_dir: Path
    report_path: Path

    @classmethod
    def from_workspace(cls, workspace_dir: Path) -> "MissionArtifacts":
        """Construct the canonical artifact layout under a workspace directory."""
        return cls(
            workspace_dir=workspace_dir,
            mission_spec_md=workspace_dir / "mission_spec.md",
            mission_spec_pdf=workspace_dir / "mission_spec.pdf",
            mission_spec_lock=workspace_dir / "mission_spec.lock.json",
            logs_dir=workspace_dir / "logs",
            reviews_dir=workspace_dir / "reviews",
            report_path=workspace_dir / "run_report.json",
        )

    def ensure_directories(self) -> None:
        """Create the workspace, logs, and reviews directories if needed."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)

    def log_path(self, stem: str, round_num: int, suffix: str) -> Path:
        """Return the path for a Claude-produced per-round log artifact."""
        return self.logs_dir / f"{stem}_round_{round_num:02d}.{suffix}"

    def review_path(self, stem: str, round_num: int) -> Path:
        """Return the path for a Codex-produced per-round review artifact."""
        return self.reviews_dir / f"{stem}_round_{round_num:02d}.json"


@dataclass
class ReviewIssue:
    """A structured issue reported by a review round."""

    severity: str
    title: str
    details: str
    category: str = "issue"
    suggested_fix: str = ""


@dataclass
class MissionReview:
    """Structured result for reviewing a drafted mission spec."""

    approved: bool
    summary: str
    issues: list[ReviewIssue]


@dataclass
class ImplementationReview:
    """Structured result for reviewing the repository after implementation."""

    mission_accomplished: bool
    has_issues: bool
    summary: str
    issues: list[ReviewIssue]


@dataclass
class ApprovalDecision:
    """User approval outcome for a mission spec."""

    approved: bool
    feedback: str = ""
    aborted: bool = False


@dataclass
class LockedMissionSpec:
    """Immutable mission-spec content and its artifact digests."""

    markdown_text: str
    markdown_sha256: str
    pdf_sha256: str


@dataclass
class RunSummary:
    """Persisted summary for a completed or failed mission run."""

    success: bool
    mission_spec_rounds: int
    implementation_rounds: int
    final_summary: str
    mission_spec_md: str
    mission_spec_pdf: str
    report_path: str
    error: str = ""


class ClaudeBackend(Protocol):
    """Protocol implemented by Claude backends used by the orchestrator."""

    def run(self, prompt: str, label: str) -> str:
        ...


class CodexBackend(Protocol):
    """Protocol implemented by Codex backends used by the orchestrator."""

    def run_json(self, prompt: str, label: str, schema: dict[str, Any]) -> dict[str, Any]:
        ...
