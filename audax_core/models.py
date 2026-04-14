"""Shared data models, defaults, and backend protocols for Audax."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Protocol

DEFAULT_SPEC_ROUNDS = 10
DEFAULT_IMPLEMENTATION_ROUNDS = 50
DEFAULT_HEARTBEAT_SECONDS = 5.0
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 1800.0
DEFAULT_WORKSPACE_DIR = "audax_artifacts"
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

_SESSION_ID_LOCK = threading.Lock()
_ALLOCATED_SESSION_KEYS: set[tuple[str, str]] = set()


def utc_timestamp() -> str:
    """Return the current UTC time in ISO-8601 ``Z`` format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_timestamp_token() -> str:
    """Return the current UTC time formatted for path-safe artifact names."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def session_id_from_timestamp(timestamp: str, *, pid: int | None = None) -> str:
    """Derive a path-safe session id from an ISO timestamp."""
    token = timestamp.replace("-", "").replace(":", "")
    return f"{token}_pid{pid or os.getpid()}"


def allocate_session_id(
    workspace_dir: Path,
    timestamp: str,
    *,
    pid: int | None = None,
) -> str:
    """Allocate a session id that is unique for the workspace and process."""
    base_session_id = session_id_from_timestamp(timestamp, pid=pid)
    workspace_key = str(workspace_dir.resolve())
    sessions_dir = Path(workspace_key) / "sessions"

    with _SESSION_ID_LOCK:
        suffix = 1
        while True:
            candidate = (
                base_session_id
                if suffix == 1
                else f"{base_session_id}_r{suffix:02d}"
            )
            reservation_key = (workspace_key, candidate)
            if reservation_key in _ALLOCATED_SESSION_KEYS:
                suffix += 1
                continue
            if (sessions_dir / candidate).exists():
                suffix += 1
                continue
            _ALLOCATED_SESSION_KEYS.add(reservation_key)
            return candidate


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
    session_id: str
    started_at: str
    sessions_dir: Path
    session_dir: Path
    mission_spec_md: Path
    mission_spec_pdf: Path
    mission_spec_lock: Path
    prompts_dir: Path
    logs_dir: Path
    reviews_dir: Path
    event_log_path: Path
    session_manifest_path: Path
    latest_path: Path
    report_path: Path

    @classmethod
    def from_workspace(
        cls,
        workspace_dir: Path,
        *,
        session_id: str | None = None,
        started_at: str | None = None,
    ) -> "MissionArtifacts":
        """Construct the canonical artifact layout under a workspace directory."""
        started_at = started_at or utc_timestamp()
        session_id = session_id or allocate_session_id(workspace_dir, started_at)
        sessions_dir = workspace_dir / "sessions"
        session_dir = sessions_dir / session_id
        return cls(
            workspace_dir=workspace_dir,
            session_id=session_id,
            started_at=started_at,
            sessions_dir=sessions_dir,
            session_dir=session_dir,
            mission_spec_md=session_dir / "mission_spec.md",
            mission_spec_pdf=session_dir / "mission_spec.pdf",
            mission_spec_lock=session_dir / "mission_spec.lock.json",
            prompts_dir=session_dir / "prompts",
            logs_dir=session_dir / "claude",
            reviews_dir=session_dir / "codex",
            event_log_path=session_dir / "events.jsonl",
            session_manifest_path=session_dir / "session_manifest.json",
            latest_path=workspace_dir / "latest.json",
            report_path=session_dir / "run_report.json",
        )

    def ensure_directories(self) -> None:
        """Create the workspace, logs, and reviews directories if needed."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)

    def prompt_path(
        self,
        stem: str,
        round_num: int,
        suffix: str = "txt",
        *,
        timestamp_token: str | None = None,
    ) -> Path:
        """Return the path for a timestamped prompt artifact."""
        return self.prompts_dir / self._round_artifact_name(
            stem,
            round_num,
            suffix,
            timestamp_token=timestamp_token,
        )

    def log_path(
        self,
        stem: str,
        round_num: int,
        suffix: str,
        *,
        timestamp_token: str | None = None,
    ) -> Path:
        """Return the path for a Claude-produced per-round log artifact."""
        return self.logs_dir / self._round_artifact_name(
            stem,
            round_num,
            suffix,
            timestamp_token=timestamp_token,
        )

    def review_path(
        self,
        stem: str,
        round_num: int,
        *,
        timestamp_token: str | None = None,
    ) -> Path:
        """Return the path for a Codex-produced per-round review artifact."""
        return self.reviews_dir / self._round_artifact_name(
            stem,
            round_num,
            "json",
            timestamp_token=timestamp_token,
        )

    def append_event(self, event_type: str, **fields: Any) -> None:
        """Append a structured event to the session event log."""
        payload = {"timestamp": utc_timestamp(), "type": event_type, **self._json_ready(fields)}
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Write a JSON document with indentation and a trailing newline."""
        path.write_text(
            json.dumps(self._json_ready(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_latest_pointer(self, payload: dict[str, Any]) -> None:
        """Update the workspace-level pointer to the most recent session."""
        self.write_json(self.latest_path, payload)

    def _round_artifact_name(
        self,
        stem: str,
        round_num: int,
        suffix: str,
        *,
        timestamp_token: str | None = None,
    ) -> str:
        token = timestamp_token or utc_timestamp_token()
        return f"{token}_{stem}_round_{round_num:02d}.{suffix}"

    @classmethod
    def _json_ready(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_ready(item) for item in value]
        return value


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
    session_id: str
    session_dir: str
    workspace_dir: str
    task: str
    started_at: str
    ended_at: str
    mission_spec_rounds: int
    implementation_rounds: int
    final_summary: str
    mission_spec_md: str
    mission_spec_pdf: str
    event_log_path: str
    session_manifest_path: str
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
