"""Mission artifact creation and locking via a SHA-256 digest of the markdown."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import LockedMissionSpec, MissionArtifacts, utc_timestamp


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_mission_spec(markdown_text: str, artifacts: MissionArtifacts, task: str) -> LockedMissionSpec:
    """Write the mission spec markdown and pin it with a SHA-256 lock manifest."""
    artifacts.mission_spec_md.write_text(markdown_text.strip() + "\n", encoding="utf-8")

    manifest = {
        "session_id": artifacts.session_id,
        "locked_at": utc_timestamp(),
        "task": task,
        "markdown_sha256": sha256_file(artifacts.mission_spec_md),
        "session_dir": str(artifacts.session_dir),
        "mission_spec_md": str(artifacts.mission_spec_md),
    }
    artifacts.write_json(artifacts.mission_spec_lock, manifest)
    return LockedMissionSpec(
        markdown_text=artifacts.mission_spec_md.read_text(encoding="utf-8"),
        markdown_sha256=manifest["markdown_sha256"],
    )


def assert_mission_spec_locked(artifacts: MissionArtifacts) -> None:
    """Verify that the locked mission spec markdown digest has not drifted."""
    if not artifacts.mission_spec_lock.exists():
        raise RuntimeError("Mission spec lock file is missing")
    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    expected_md_hash = str(manifest.get("markdown_sha256", ""))
    current_md_hash = sha256_file(artifacts.mission_spec_md)
    if current_md_hash != expected_md_hash:
        raise RuntimeError("Mission spec lock mismatch: locked mission markdown was modified")


def load_locked_mission_spec(artifacts: MissionArtifacts) -> LockedMissionSpec:
    """Load the current locked mission spec after validating its digest."""
    assert_mission_spec_locked(artifacts)
    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    markdown_text = artifacts.mission_spec_md.read_text(encoding="utf-8")
    return LockedMissionSpec(
        markdown_text=markdown_text,
        markdown_sha256=str(manifest["markdown_sha256"]),
    )
