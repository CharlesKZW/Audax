"""Mission artifact creation and locking via a SHA-256 digest of the markdown."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import LockedMissionSpec, MissionArtifacts, utc_timestamp


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_locked_text(
    *,
    text: str,
    text_path: Path,
    lock_path: Path,
    task: str,
    session_id: str,
    session_dir: Path,
    text_path_key: str,
) -> LockedMissionSpec:
    """Write a locked text artifact and return its normalized contents + digest."""
    text_path.write_text(text.strip() + "\n", encoding="utf-8")
    manifest = {
        "session_id": session_id,
        "locked_at": utc_timestamp(),
        "task": task,
        "markdown_sha256": sha256_file(text_path),
        "session_dir": str(session_dir),
        text_path_key: str(text_path),
    }
    lock_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return LockedMissionSpec(
        markdown_text=text_path.read_text(encoding="utf-8"),
        markdown_sha256=manifest["markdown_sha256"],
    )


def lock_mission_spec(markdown_text: str, artifacts: MissionArtifacts, task: str) -> LockedMissionSpec:
    """Write the mission spec markdown and pin it with a SHA-256 lock manifest."""
    return _write_locked_text(
        text=markdown_text,
        text_path=artifacts.mission_spec_md,
        lock_path=artifacts.mission_spec_lock,
        task=task,
        session_id=artifacts.session_id,
        session_dir=artifacts.session_dir,
        text_path_key="mission_spec_md",
    )


def lock_direct_instruction(
    instruction_text: str,
    artifacts: MissionArtifacts,
    task: str,
) -> LockedMissionSpec:
    """Write the original direct instruction and pin it with a SHA-256 lock manifest."""
    return _write_locked_text(
        text=instruction_text,
        text_path=artifacts.direct_instruction_txt,
        lock_path=artifacts.direct_instruction_lock,
        task=task,
        session_id=artifacts.session_id,
        session_dir=artifacts.session_dir,
        text_path_key="direct_instruction_txt",
    )


def _assert_locked_text(*, text_path: Path, lock_path: Path, missing_message: str, mismatch_message: str) -> None:
    """Verify that a locked text artifact still matches its recorded digest."""
    if not lock_path.exists():
        raise RuntimeError(missing_message)
    manifest = json.loads(lock_path.read_text(encoding="utf-8"))
    expected_md_hash = str(manifest.get("markdown_sha256", ""))
    current_md_hash = sha256_file(text_path)
    if current_md_hash != expected_md_hash:
        raise RuntimeError(mismatch_message)


def assert_mission_spec_locked(artifacts: MissionArtifacts) -> None:
    """Verify that the locked mission spec markdown digest has not drifted."""
    _assert_locked_text(
        text_path=artifacts.mission_spec_md,
        lock_path=artifacts.mission_spec_lock,
        missing_message="Mission spec lock file is missing",
        mismatch_message="Mission spec lock mismatch: locked mission markdown was modified",
    )


def assert_direct_instruction_locked(artifacts: MissionArtifacts) -> None:
    """Verify that the locked direct-instruction digest has not drifted."""
    _assert_locked_text(
        text_path=artifacts.direct_instruction_txt,
        lock_path=artifacts.direct_instruction_lock,
        missing_message="Direct instruction lock file is missing",
        mismatch_message="Direct instruction lock mismatch: locked prompt text was modified",
    )


def load_locked_mission_spec(artifacts: MissionArtifacts) -> LockedMissionSpec:
    """Load the current locked mission spec after validating its digest."""
    assert_mission_spec_locked(artifacts)
    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    markdown_text = artifacts.mission_spec_md.read_text(encoding="utf-8")
    return LockedMissionSpec(
        markdown_text=markdown_text,
        markdown_sha256=str(manifest["markdown_sha256"]),
    )


def load_locked_direct_instruction(artifacts: MissionArtifacts) -> LockedMissionSpec:
    """Load the current locked direct instruction after validating its digest."""
    assert_direct_instruction_locked(artifacts)
    manifest = json.loads(artifacts.direct_instruction_lock.read_text(encoding="utf-8"))
    instruction_text = artifacts.direct_instruction_txt.read_text(encoding="utf-8")
    return LockedMissionSpec(
        markdown_text=instruction_text,
        markdown_sha256=str(manifest["markdown_sha256"]),
    )
