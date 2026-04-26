"""Shared data models, defaults, and backend protocols for Audax."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Protocol

DEFAULT_SPEC_ROUNDS = 3
DEFAULT_IMPLEMENTATION_ROUNDS = 5
DEFAULT_HEARTBEAT_SECONDS = 5.0
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS: float | None = None
DEFAULT_WORKSPACE_DIR = "audax_artifacts"
MISSION_MODE_SPEC = "mission-spec"
MISSION_MODE_DIRECT = "direct-instruction"
DEFAULT_MISSION_MODE = MISSION_MODE_DIRECT
MISSION_MODE_CHOICES = (
    MISSION_MODE_DIRECT,
    MISSION_MODE_SPEC,
)
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


def _has_nonempty_text_artifact(path: Path) -> bool:
    """Return whether a text artifact exists and has non-empty contents."""
    if not path.exists():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _find_incomplete_sessions_with_contract(
    workspace_dir: Path,
    *,
    include_draft_specs: bool,
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Return incomplete sessions with usable locked or draft contracts."""
    sessions_dir = workspace_dir / "sessions"
    if not sessions_dir.is_dir():
        return []

    found: list[tuple[str, Path, dict[str, Any]]] = []
    for entry in sorted(sessions_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not entry.is_dir():
            continue
        has_locked_spec = (entry / "mission_spec.lock.json").exists()
        has_locked_direct_instruction = (entry / "direct_instruction.lock.json").exists()
        if not has_locked_spec and not has_locked_direct_instruction and not include_draft_specs:
            continue
        if (
            not has_locked_spec
            and not has_locked_direct_instruction
            and not _has_nonempty_text_artifact(entry / "mission_spec.md")
            and not _has_nonempty_text_artifact(entry / "direct_instruction.txt")
        ):
            continue
        manifest_path = entry / "session_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("status") == "succeeded":
            continue
        found.append((entry.name, entry, manifest))
    return found


def find_resumable_sessions(
    workspace_dir: Path,
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Return resumable sessions newest first.

    A session is resumable when its mission contract has already been locked
    (for example ``mission_spec.lock.json`` or ``direct_instruction.lock.json``)
    and the run has not been recorded as ``succeeded``. Each tuple is
    ``(session_id, session_dir, manifest)``.
    """
    return _find_incomplete_sessions_with_contract(
        workspace_dir,
        include_draft_specs=False,
    )


def find_continuable_sessions(
    workspace_dir: Path,
) -> list[tuple[str, Path, dict[str, Any]]]:
    """Return incomplete sessions with either locked or draft mission contracts.

    Unlike :func:`find_resumable_sessions`, this also includes sessions that do
    not yet have a lock file but do have a non-empty saved contract text such
    as ``mission_spec.md`` or ``direct_instruction.txt``. This supports
    ``audax continue`` after an interrupted run left behind draft contract
    state.
    """
    return _find_incomplete_sessions_with_contract(
        workspace_dir,
        include_draft_specs=True,
    )


def load_session_manifest(workspace_dir: Path, session_id: str) -> dict[str, Any]:
    """Read ``session_manifest.json`` for an existing session id."""
    manifest_path = workspace_dir / "sessions" / session_id / "session_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Session manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


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
    mission_mode: str = DEFAULT_MISSION_MODE
    max_spec_rounds: int = DEFAULT_SPEC_ROUNDS
    max_implementation_rounds: int = DEFAULT_IMPLEMENTATION_ROUNDS
    require_mission_approval: bool = True
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    subprocess_timeout_seconds: float | None = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    claude_cmd: str = CLAUDE_CMD
    codex_cmd: str = CODEX_CMD

    def __post_init__(self) -> None:
        if self.mission_mode == MISSION_MODE_DIRECT:
            self.max_spec_rounds = 0
            self.require_mission_approval = False


@dataclass
class MissionArtifacts:
    """Paths for all generated artifacts produced during a mission run."""

    workspace_dir: Path
    session_id: str
    started_at: str
    sessions_dir: Path
    session_dir: Path
    mission_spec_md: Path
    mission_spec_lock: Path
    direct_instruction_txt: Path
    direct_instruction_lock: Path
    prompts_dir: Path
    outputs_dir: Path
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
            mission_spec_lock=session_dir / "mission_spec.lock.json",
            direct_instruction_txt=session_dir / "direct_instruction.txt",
            direct_instruction_lock=session_dir / "direct_instruction.lock.json",
            prompts_dir=session_dir / "prompts",
            outputs_dir=session_dir / "outputs",
            reviews_dir=session_dir / "reviews",
            event_log_path=session_dir / "events.jsonl",
            session_manifest_path=session_dir / "session_manifest.json",
            latest_path=workspace_dir / "latest.json",
            report_path=session_dir / "run_report.json",
        )

    def ensure_directories(self) -> None:
        """Create the workspace, outputs, and reviews directories if needed."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
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

    def output_path(
        self,
        stem: str,
        round_num: int,
        suffix: str,
        *,
        timestamp_token: str | None = None,
    ) -> Path:
        """Return the path for a per-round drafter or implementer text artifact."""
        return self.outputs_dir / self._round_artifact_name(
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
        """Return the path for a per-round reviewer JSON artifact."""
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


@dataclass
class MissionReview:
    """Structured result for reviewing a drafted mission spec."""

    approved: bool
    summary: str
    issues: list[ReviewIssue]
    high_stakes_decisions: list[str] = field(default_factory=list)


@dataclass
class ImplementationReview:
    """Structured result for reviewing the repository after implementation."""

    mission_accomplished: bool
    has_issues: bool
    summary: str
    issues: list[ReviewIssue]
    completed_criteria: list[str] = field(default_factory=list)
    remaining_criteria: list[str] = field(default_factory=list)
    progress_pct: int = 0


@dataclass
class ApprovalDecision:
    """User approval outcome for a mission spec."""

    approved: bool
    feedback: str = ""
    aborted: bool = False


@dataclass
class LockedMissionSpec:
    """Immutable mission-spec content and its markdown digest."""

    markdown_text: str
    markdown_sha256: str


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
    mission_mode: str
    final_summary: str
    mission_spec_md: str
    direct_instruction_txt: str
    locked_contract_path: str
    locked_contract_label: str
    event_log_path: str
    session_manifest_path: str
    report_path: str
    error: str = ""
    latest_mission_spec_review_approved: bool | None = None
    latest_mission_spec_review_summary: str = ""
    latest_mission_spec_review_feedback: str = ""


class ClaudeBackend(Protocol):
    """Protocol implemented by Claude backends used by the orchestrator."""

    def run(self, prompt: str, label: str) -> str:
        ...


class CodexBackend(Protocol):
    """Protocol implemented by Codex backends used by the orchestrator."""

    def run_json(self, prompt: str, label: str, schema: dict[str, Any]) -> dict[str, Any]:
        ...
