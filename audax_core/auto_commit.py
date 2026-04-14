"""Per-round ``git commit`` helper used by the orchestrator.

The orchestrator owns the side-channel plumbing (event log, stdout
messaging). This module stays pure: it invokes ``git``, classifies what
happened, and returns structured outcomes. Callers decide how to surface
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess

from .ui import parse_markdown_sections


@dataclass
class CommitInfo:
    """A single commit produced during an implementation round."""

    sha: str
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:12]


@dataclass
class CommitOutcome:
    """Structured result of a single auto-commit action.

    For ``start_session`` only ``status`` (and ``branch`` for
    ``branch_created``) are populated. For ``commit_round``, ``round_commits``
    enumerates every commit that landed during the round (Claude's own
    commits plus Audax's trailing sweeper, if any). ``sweeper_sha`` points
    at the sweeper commit specifically, or is empty when Audax did not need
    to commit anything.
    """

    status: str
    round_commits: list[CommitInfo] = field(default_factory=list)
    sweeper_sha: str = ""
    branch: str = ""
    error: str = ""


class AutoCommitter:
    """Commit repository changes after each implementation round."""

    def __init__(
        self,
        repo_root: Path,
        *,
        enabled: bool = True,
        use_session_branch: bool = False,
    ) -> None:
        self.repo_root = repo_root
        self.enabled = enabled
        self.use_session_branch = use_session_branch
        self._session_active = False
        self._branch_name = ""
        self._last_seen_head = ""

    # --- lifecycle hooks -------------------------------------------------

    def start_session(self, session_id: str) -> CommitOutcome:
        """Prepare the repo for auto-committing and return what happened.

        Status values:
        * ``"disabled"`` — flag is off, nothing to do.
        * ``"not_a_repo"`` — auto-commit was requested but cwd is not a git
          repository; commits will be skipped for the rest of the session.
        * ``"branch_created"`` — session branch was created and checked out.
        * ``"on_current_branch"`` — auto-commit active, no branch switch.
        * ``"failed"`` — branch creation or other setup failed.
        """
        if not self.enabled:
            return CommitOutcome(status="disabled")
        if not self._is_git_repo():
            return CommitOutcome(status="not_a_repo")
        try:
            if self.use_session_branch:
                branch = f"audax/{session_id}"
                self._run_git(["checkout", "-b", branch])
                self._branch_name = branch
            self._last_seen_head = self._run_git(["rev-parse", "HEAD"]).strip()
        except RuntimeError as exc:
            return CommitOutcome(status="failed", error=str(exc))
        self._session_active = True
        if self.use_session_branch:
            return CommitOutcome(status="branch_created", branch=self._branch_name)
        return CommitOutcome(status="on_current_branch")

    def commit_round(
        self,
        *,
        round_num: int,
        session_id: str,
        implementer_summary: str,
    ) -> CommitOutcome:
        """Capture all commits produced during an implementation round.

        Claude is expected to commit logical chunks as it goes. Audax then
        runs a sweeper (``git add -A && git commit``) to capture any
        trailing uncommitted work. The returned outcome enumerates every
        commit that landed between the prior round's HEAD and the current
        HEAD, in chronological order, with the sweeper commit (if any)
        highlighted separately.
        """
        if not self._session_active:
            return CommitOutcome(status="inactive")
        try:
            self._run_git(["add", "-A"])
            sweeper_sha = ""
            if self._has_staged_changes():
                message = self._format_commit_message(
                    round_num=round_num,
                    session_id=session_id,
                    implementer_summary=implementer_summary,
                )
                self._run_git(["commit", "-m", message])
                sweeper_sha = self._run_git(["rev-parse", "HEAD"]).strip()

            commits = self._commits_since(self._last_seen_head)
            self._last_seen_head = self._run_git(["rev-parse", "HEAD"]).strip()
        except RuntimeError as exc:
            return CommitOutcome(status="failed", error=str(exc))
        if not commits:
            return CommitOutcome(status="no_changes")
        return CommitOutcome(
            status="committed",
            round_commits=commits,
            sweeper_sha=sweeper_sha,
        )

    def _commits_since(self, base_ref: str) -> list[CommitInfo]:
        """Enumerate commits reachable from HEAD but not from ``base_ref``."""
        if not base_ref:
            return []
        output = self._run_git(
            ["log", "--reverse", "--pretty=%H%x00%s", f"{base_ref}..HEAD"]
        )
        commits: list[CommitInfo] = []
        for raw in output.splitlines():
            if not raw.strip():
                continue
            sha, _, subject = raw.partition("\x00")
            commits.append(CommitInfo(sha=sha.strip(), subject=subject.strip()))
        return commits

    # --- internals -------------------------------------------------------

    def _format_commit_message(
        self,
        *,
        round_num: int,
        session_id: str,
        implementer_summary: str,
    ) -> str:
        sections = parse_markdown_sections(implementer_summary)
        accomplished = _lookup_section(sections, "Accomplished")
        risks = _lookup_section(sections, "Remaining Risks")
        title_tail = accomplished[0] if accomplished else "work in progress"
        title = f"audax round {round_num}: {_truncate(title_tail, 68)}"

        body_parts: list[str] = []
        if accomplished:
            body_parts.append("Accomplished:")
            body_parts.extend(f"- {item}" for item in accomplished)
        if risks:
            if body_parts:
                body_parts.append("")
            body_parts.append("Remaining risks:")
            body_parts.extend(f"- {item}" for item in risks)
        body = "\n".join(body_parts) if body_parts else implementer_summary.strip()
        trailer = f"Audax-Session: {session_id}\nAudax-Round: {round_num}"
        return f"{title}\n\n{body}\n\n{trailer}"

    def _is_git_repo(self) -> bool:
        try:
            self._run_git(["rev-parse", "--git-dir"])
        except RuntimeError:
            return False
        return True

    def _has_staged_changes(self) -> bool:
        output = self._run_git(["diff", "--cached", "--name-only"]).strip()
        return bool(output)

    def _run_git(self, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
        return completed.stdout


def _lookup_section(sections: dict[str, list[str]], name: str) -> list[str]:
    lowered = {key.lower(): value for key, value in sections.items()}
    if name.lower() in lowered:
        return lowered[name.lower()]
    for key, value in lowered.items():
        if name.lower() in key:
            return value
    return []


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
