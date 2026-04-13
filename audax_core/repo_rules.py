"""Discovery of repository policy files used to ground the mission loop."""

from __future__ import annotations

import os
from pathlib import Path

from .models import MAX_RULE_BYTES, MAX_RULE_FILES, RULE_FILENAMES


def discover_rule_files(repo_root: Path, workspace_dir: Path) -> list[Path]:
    """Find rule and policy files while excluding the generated workspace."""
    repo_root = repo_root.resolve()
    workspace_dir = workspace_dir.resolve()
    found: list[Path] = []
    seen: set[Path] = set()

    for rule_name in RULE_FILENAMES:
        direct = repo_root / rule_name
        if direct.is_file():
            resolved = direct.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(direct)

    for current_root, dirnames, filenames in os.walk(repo_root):
        current_path = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if name != ".git" and (current_path / name).resolve() != workspace_dir
        ]
        for rule_name in RULE_FILENAMES:
            if rule_name not in filenames:
                continue
            candidate = current_path / rule_name
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)
            if len(found) >= MAX_RULE_FILES:
                return found

    return found


def build_repo_context(repo_root: Path, workspace_dir: Path) -> str:
    """Assemble a bounded text snapshot of repository policy files."""
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
