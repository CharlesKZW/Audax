"""Mission-loop orchestration for spec drafting, implementation, and review."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys
from typing import Callable, TextIO

from .approval import interactive_mission_approval
from .artifacts import assert_mission_spec_locked, lock_mission_spec
from .models import (
    ApprovalDecision,
    ClaudeBackend,
    CodexBackend,
    ImplementationReview,
    LockedMissionSpec,
    LoopConfig,
    MissionArtifacts,
    RunSummary,
    utc_timestamp,
)
from .prompts import (
    build_implementation_prompt,
    build_implementation_review_prompt,
    build_mission_review_prompt,
    build_mission_spec_prompt,
)
from .repo_rules import build_repo_context
from .reviews import (
    combine_spec_feedback,
    implementation_review_schema,
    implementation_review_to_dict,
    mission_review_schema,
    mission_review_to_dict,
    parse_implementation_review,
    parse_mission_review,
    render_review_feedback,
)


class ReviewLoopOrchestrator:
    """Coordinate the end-to-end Claude and Codex mission loop."""

    def __init__(
        self,
        config: LoopConfig,
        artifacts: MissionArtifacts,
        claude: ClaudeBackend,
        codex: CodexBackend,
        *,
        approval_gate: Callable[[str, Path], ApprovalDecision] | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self.config = config
        self.artifacts = artifacts
        self.claude = claude
        self.codex = codex
        self.approval_gate = approval_gate or interactive_mission_approval
        self.output_stream = output_stream or sys.stdout
        self._mission_spec_rounds_run = 0
        self._implementation_rounds_run = 0
        self.artifacts.ensure_directories()

    def run(self, task: str) -> RunSummary:
        """Execute the full mission lifecycle and persist a run report."""
        self._mission_spec_rounds_run = 0
        self._implementation_rounds_run = 0
        final_summary = ""
        error = ""
        success = False
        ended_at = ""
        interrupted = False

        try:
            self._write_session_manifest(task=task, status="running")
            self.artifacts.write_latest_pointer(
                {
                    "updated_at": utc_timestamp(),
                    "session_id": self.artifacts.session_id,
                    "session_dir": str(self.artifacts.session_dir),
                    "workspace_dir": str(self.artifacts.workspace_dir),
                    "task": task,
                    "status": "running",
                    "report_path": str(self.artifacts.report_path),
                }
            )
            self.artifacts.append_event(
                "session_started",
                session_id=self.artifacts.session_id,
                session_dir=self.artifacts.session_dir,
                task=task,
                repo_root=self.config.repo_root,
                workspace_dir=self.config.workspace_dir,
                config=self._config_snapshot(),
            )
            self._print_header(task)
            locked_spec = self._prepare_and_lock_mission_spec(task)
            implementation_review = self._run_implementation_loop(task, locked_spec)
            final_summary = implementation_review.summary
            success = implementation_review.mission_accomplished and not implementation_review.has_issues
            ended_at = utc_timestamp()
            return RunSummary(
                success=success,
                session_id=self.artifacts.session_id,
                session_dir=str(self.artifacts.session_dir),
                workspace_dir=str(self.artifacts.workspace_dir),
                task=task,
                started_at=self.artifacts.started_at,
                ended_at=ended_at,
                mission_spec_rounds=self._mission_spec_rounds_run,
                implementation_rounds=self._implementation_rounds_run,
                final_summary=final_summary,
                mission_spec_md=str(self.artifacts.mission_spec_md),
                mission_spec_pdf=str(self.artifacts.mission_spec_pdf),
                event_log_path=str(self.artifacts.event_log_path),
                session_manifest_path=str(self.artifacts.session_manifest_path),
                report_path=str(self.artifacts.report_path),
            )
        except KeyboardInterrupt:
            interrupted = True
            error = "Interrupted"
            raise
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            ended_at = ended_at or utc_timestamp()
            status = "succeeded" if success else "interrupted" if interrupted else "failed"
            report = RunSummary(
                success=success,
                session_id=self.artifacts.session_id,
                session_dir=str(self.artifacts.session_dir),
                workspace_dir=str(self.artifacts.workspace_dir),
                task=task,
                started_at=self.artifacts.started_at,
                ended_at=ended_at,
                mission_spec_rounds=self._mission_spec_rounds_run,
                implementation_rounds=self._implementation_rounds_run,
                final_summary=final_summary,
                mission_spec_md=str(self.artifacts.mission_spec_md),
                mission_spec_pdf=str(self.artifacts.mission_spec_pdf),
                event_log_path=str(self.artifacts.event_log_path),
                session_manifest_path=str(self.artifacts.session_manifest_path),
                report_path=str(self.artifacts.report_path),
                error=error,
            )
            self.artifacts.write_json(self.artifacts.report_path, asdict(report))
            self._write_session_manifest(
                task=task,
                status=status,
                ended_at=ended_at,
                error=error,
                final_summary=final_summary,
                success=success,
            )
            self.artifacts.write_latest_pointer(
                {
                    "updated_at": utc_timestamp(),
                    "session_id": self.artifacts.session_id,
                    "session_dir": str(self.artifacts.session_dir),
                    "workspace_dir": str(self.artifacts.workspace_dir),
                    "task": task,
                    "status": status,
                    "report_path": str(self.artifacts.report_path),
                    "event_log_path": str(self.artifacts.event_log_path),
                }
            )
            self.artifacts.append_event(
                "session_finished",
                session_id=self.artifacts.session_id,
                status=status,
                success=success,
                error=error,
                report_path=self.artifacts.report_path,
            )

    def _prepare_and_lock_mission_spec(self, task: str) -> LockedMissionSpec:
        """Draft, review, optionally approve, and lock the mission spec."""
        current_spec = ""
        codex_feedback = ""
        user_feedback = ""

        for round_num in range(1, self.config.max_spec_rounds + 1):
            self._mission_spec_rounds_run = round_num
            repo_context = build_repo_context(
                repo_root=self.config.repo_root,
                workspace_dir=self.config.workspace_dir,
            )
            self._write_line(f"[Mission {round_num}] Claude drafting mission spec")
            prompt = build_mission_spec_prompt(
                task=task,
                repo_context=repo_context,
                current_spec=current_spec,
                pending_feedback=combine_spec_feedback(
                    user_feedback=user_feedback,
                    codex_feedback=codex_feedback,
                ),
            )
            prompt_path = self.artifacts.prompt_path("mission_spec_claude", round_num)
            prompt_path.write_text(prompt + "\n", encoding="utf-8")
            self.artifacts.append_event(
                "prompt_written",
                actor="claude",
                phase="mission_spec",
                round=round_num,
                path=prompt_path,
            )
            current_spec = self.claude.run(prompt, label=f"Claude mission spec round {round_num}").strip()
            if not current_spec:
                raise RuntimeError(f"Claude returned an empty mission spec in round {round_num}")

            self.artifacts.mission_spec_md.write_text(current_spec + "\n", encoding="utf-8")
            claude_output_path = self.artifacts.log_path("mission_spec_claude", round_num, "md")
            claude_output_path.write_text(
                current_spec + "\n",
                encoding="utf-8",
            )
            self.artifacts.append_event(
                "output_written",
                actor="claude",
                phase="mission_spec",
                round=round_num,
                path=claude_output_path,
            )

            self._write_line(f"[Mission {round_num}] Codex reviewing mission spec")
            review_prompt = build_mission_review_prompt(
                task=task,
                repo_context=repo_context,
                mission_spec=current_spec,
            )
            review_prompt_path = self.artifacts.prompt_path("mission_spec_codex", round_num)
            review_prompt_path.write_text(review_prompt + "\n", encoding="utf-8")
            self.artifacts.append_event(
                "prompt_written",
                actor="codex",
                phase="mission_spec_review",
                round=round_num,
                path=review_prompt_path,
            )
            review = parse_mission_review(
                self.codex.run_json(
                    review_prompt,
                    label=f"Codex mission spec review round {round_num}",
                    schema=mission_review_schema(),
                )
            )
            review_path = self.artifacts.review_path("mission_spec_codex", round_num)
            self.artifacts.write_json(
                review_path,
                mission_review_to_dict(review),
            )
            self.artifacts.append_event(
                "output_written",
                actor="codex",
                phase="mission_spec_review",
                round=round_num,
                path=review_path,
                approved=review.approved,
            )

            if not review.approved:
                codex_feedback = render_review_feedback(review.issues, summary=review.summary)
                continue

            codex_feedback = ""
            if self.config.require_mission_approval:
                decision = self.approval_gate(current_spec, self.artifacts.mission_spec_md)
                if decision.aborted:
                    raise RuntimeError("Mission approval aborted by user")
                if not decision.approved:
                    if not decision.feedback.strip():
                        raise RuntimeError("Mission approval requested changes but no feedback was provided")
                    user_feedback = decision.feedback.strip()
                    continue

            locked_spec = lock_mission_spec(current_spec, self.artifacts, task)
            self.artifacts.append_event(
                "mission_locked",
                mission_spec_md=self.artifacts.mission_spec_md,
                mission_spec_pdf=self.artifacts.mission_spec_pdf,
                mission_spec_lock=self.artifacts.mission_spec_lock,
                markdown_sha256=locked_spec.markdown_sha256,
                pdf_sha256=locked_spec.pdf_sha256,
            )
            self._write_line(
                f"[Mission] locked at {self.artifacts.mission_spec_pdf} "
                f"(sha256 {locked_spec.markdown_sha256[:12]}...)"
            )
            return locked_spec

        raise RuntimeError(f"Mission spec failed to converge within {self.config.max_spec_rounds} round(s)")

    def _run_implementation_loop(
        self,
        task: str,
        locked_spec: LockedMissionSpec,
    ) -> ImplementationReview:
        """Iterate implementation and review rounds until success or failure."""
        review_feedback = ""

        for round_num in range(1, self.config.max_implementation_rounds + 1):
            self._implementation_rounds_run = round_num
            assert_mission_spec_locked(self.artifacts)
            repo_context = build_repo_context(
                repo_root=self.config.repo_root,
                workspace_dir=self.config.workspace_dir,
            )
            self._write_line(f"[Implementation {round_num}] Claude implementing mission")
            implementation_prompt = build_implementation_prompt(
                task=task,
                repo_context=repo_context,
                mission_spec=self.artifacts.mission_spec_md.read_text(encoding="utf-8"),
                mission_pdf_path=self.artifacts.mission_spec_pdf,
                locked_spec=locked_spec,
                review_feedback=review_feedback,
            )
            implementation_prompt_path = self.artifacts.prompt_path("implementation_claude", round_num)
            implementation_prompt_path.write_text(implementation_prompt + "\n", encoding="utf-8")
            self.artifacts.append_event(
                "prompt_written",
                actor="claude",
                phase="implementation",
                round=round_num,
                path=implementation_prompt_path,
            )
            claude_summary = self.claude.run(
                implementation_prompt,
                label=f"Claude implementation round {round_num}",
            ).strip()
            if not claude_summary:
                raise RuntimeError(f"Claude returned an empty implementation summary in round {round_num}")

            implementation_output_path = self.artifacts.log_path("implementation_claude", round_num, "md")
            implementation_output_path.write_text(
                claude_summary + "\n",
                encoding="utf-8",
            )
            self.artifacts.append_event(
                "output_written",
                actor="claude",
                phase="implementation",
                round=round_num,
                path=implementation_output_path,
            )
            assert_mission_spec_locked(self.artifacts)

            self._write_line(f"[Implementation {round_num}] Codex reviewing implementation")
            implementation_review_prompt = build_implementation_review_prompt(
                task=task,
                repo_context=repo_context,
                mission_spec=self.artifacts.mission_spec_md.read_text(encoding="utf-8"),
                mission_pdf_path=self.artifacts.mission_spec_pdf,
                claude_summary=claude_summary,
                locked_spec=locked_spec,
            )
            implementation_review_prompt_path = self.artifacts.prompt_path(
                "implementation_codex",
                round_num,
            )
            implementation_review_prompt_path.write_text(
                implementation_review_prompt + "\n",
                encoding="utf-8",
            )
            self.artifacts.append_event(
                "prompt_written",
                actor="codex",
                phase="implementation_review",
                round=round_num,
                path=implementation_review_prompt_path,
            )
            review = parse_implementation_review(
                self.codex.run_json(
                    implementation_review_prompt,
                    label=f"Codex implementation review round {round_num}",
                    schema=implementation_review_schema(),
                )
            )
            implementation_review_path = self.artifacts.review_path("implementation_codex", round_num)
            self.artifacts.write_json(
                implementation_review_path,
                implementation_review_to_dict(review),
            )
            self.artifacts.append_event(
                "output_written",
                actor="codex",
                phase="implementation_review",
                round=round_num,
                path=implementation_review_path,
                mission_accomplished=review.mission_accomplished,
                has_issues=review.has_issues,
            )

            if review.mission_accomplished and not review.has_issues:
                self._write_line(f"[Implementation] mission complete in {round_num} round(s)")
                return review

            review_feedback = render_review_feedback(review.issues, summary=review.summary)

        raise RuntimeError(
            f"Implementation failed to converge within "
            f"{self.config.max_implementation_rounds} round(s)"
        )

    def _print_header(self, task: str) -> None:
        """Render a short run header to the configured output stream."""
        self._write_line(f"{'=' * 60}")
        self._write_line("Audax collaborative mission loop")
        self._write_line(f"Task: {task}")
        self._write_line(f"Repo: {self.config.repo_root}")
        self._write_line(f"Workspace: {self.config.workspace_dir}")
        self._write_line(
            f"Spec rounds max: {self.config.max_spec_rounds} | "
            f"Implementation rounds max: {self.config.max_implementation_rounds}"
        )
        self._write_line(f"{'=' * 60}")

    def _write_line(self, message: str) -> None:
        """Write a single status line immediately."""
        self.output_stream.write(f"{message}\n")
        self.output_stream.flush()

    def _config_snapshot(self) -> dict[str, object]:
        return {
            "repo_root": str(self.config.repo_root),
            "workspace_dir": str(self.config.workspace_dir),
            "max_spec_rounds": self.config.max_spec_rounds,
            "max_implementation_rounds": self.config.max_implementation_rounds,
            "require_mission_approval": self.config.require_mission_approval,
            "heartbeat_seconds": self.config.heartbeat_seconds,
            "subprocess_timeout_seconds": self.config.subprocess_timeout_seconds,
            "claude_cmd": self.config.claude_cmd,
            "codex_cmd": self.config.codex_cmd,
        }

    def _write_session_manifest(
        self,
        *,
        task: str,
        status: str,
        ended_at: str = "",
        error: str = "",
        final_summary: str = "",
        success: bool | None = None,
    ) -> None:
        self.artifacts.write_json(
            self.artifacts.session_manifest_path,
            {
                "session_id": self.artifacts.session_id,
                "task": task,
                "status": status,
                "success": success,
                "started_at": self.artifacts.started_at,
                "ended_at": ended_at,
                "error": error,
                "final_summary": final_summary,
                "repo_root": str(self.config.repo_root),
                "workspace_dir": str(self.config.workspace_dir),
                "session_dir": str(self.artifacts.session_dir),
                "config": self._config_snapshot(),
                "artifacts": {
                    "session_manifest_path": str(self.artifacts.session_manifest_path),
                    "event_log_path": str(self.artifacts.event_log_path),
                    "mission_spec_md": str(self.artifacts.mission_spec_md),
                    "mission_spec_pdf": str(self.artifacts.mission_spec_pdf),
                    "mission_spec_lock": str(self.artifacts.mission_spec_lock),
                    "prompts_dir": str(self.artifacts.prompts_dir),
                    "claude_dir": str(self.artifacts.logs_dir),
                    "codex_dir": str(self.artifacts.reviews_dir),
                    "report_path": str(self.artifacts.report_path),
                },
            },
        )
