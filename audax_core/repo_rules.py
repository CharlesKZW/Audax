"""Discovery of repository policy files used to ground the mission loop."""

from __future__ import annotations

import os
from pathlib import Path

from .models import MAX_RULE_BYTES, MAX_RULE_FILES, RULE_FILENAMES

IGNORED_RULE_DIRNAMES = {
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "_build",
}


def _file_identity(path: Path) -> tuple[int, int] | None:
    """Return (device, inode) for a file, or None if it cannot be stat'd.

    Used to deduplicate rule-file discovery across case-insensitive filesystems,
    hardlinks, and symlink aliases where the string path differs but the underlying
    file is identical.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _should_skip_dir(current_path: Path, name: str, workspace_dir: Path) -> bool:
    """Return whether a directory should be skipped during rule discovery."""
    if name in IGNORED_RULE_DIRNAMES:
        return True
    try:
        return (current_path / name).resolve() == workspace_dir
    except OSError:
        return False


def discover_rule_files(repo_root: Path, workspace_dir: Path) -> list[Path]:
    """Find rule and policy files while excluding the generated workspace."""
    repo_root = repo_root.resolve()
    workspace_dir = workspace_dir.resolve()
    found: list[Path] = []
    seen: set[tuple[int, int]] = set()

    for rule_name in RULE_FILENAMES:
        direct = repo_root / rule_name
        if not direct.is_file():
            continue
        identity = _file_identity(direct)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        found.append(direct)

    for current_root, dirnames, filenames in os.walk(repo_root):
        current_path = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if not _should_skip_dir(current_path, name, workspace_dir)
        ]
        for rule_name in RULE_FILENAMES:
            if rule_name not in filenames:
                continue
            candidate = current_path / rule_name
            identity = _file_identity(candidate)
            if identity is None or identity in seen:
                continue
            seen.add(identity)
            found.append(candidate)
            if len(found) >= MAX_RULE_FILES:
                return found

    return found


def build_repo_context(repo_root: Path, workspace_dir: Path) -> str:
    """Assemble a bounded text snapshot of repository policy files."""
    repo_root = repo_root.resolve()
    rule_files = discover_rule_files(repo_root, workspace_dir)
    if not rule_files:
        return "No repo policy files were discovered."

    sections: list[str] = []
    for path in rule_files[:MAX_RULE_FILES]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        trimmed = text[:MAX_RULE_BYTES]
        if len(text) > MAX_RULE_BYTES:
            trimmed += "\n...[truncated]..."
        rel_path = path.relative_to(repo_root)
        sections.append(f"FILE: {rel_path}\n{trimmed.strip()}")
    return "\n\n".join(sections)
