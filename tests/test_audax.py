from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import time

import pytest

from audax_core import (
    ApprovalDecision,
    HeartbeatProgress,
    LoopConfig,
    MissionArtifacts,
    ReviewLoopOrchestrator,
    assert_mission_spec_locked,
    lock_mission_spec,
)
from audax_core.approval import interactive_mission_approval
from audax_core.backends import ClaudeCLI, parse_claude_stream_output
from audax_core.app import (
    build_startup_card_info_lines,
    continue_main,
    main,
    parse_args,
    parse_continue_args,
    read_task,
)
from audax_core.models import (
    DEFAULT_WORKSPACE_DIR,
    find_resumable_sessions,
    load_session_manifest,
    session_id_from_timestamp,
)
from audax_core.progress import QuietProcessRunner
from audax_core.repo_rules import build_repo_context, discover_rule_files
from audax_core.ui import render_session_header_card, render_startup_card


class FakeClaude:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def run(self, prompt: str, label: str) -> str:
        self.calls.append((label, prompt))
        if not self.responses:
            raise AssertionError("FakeClaude ran out of scripted responses")
        return self.responses.pop(0)


class FakeCodex:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def run_json(self, prompt: str, label: str, schema: dict) -> dict:
        self.calls.append((label, prompt, schema))
        if not self.responses:
            raise AssertionError("FakeCodex ran out of scripted responses")
        return self.responses.pop(0)


def make_config(repo_root: Path, *, require_approval: bool = False) -> LoopConfig:
    return LoopConfig(
        repo_root=repo_root,
        workspace_dir=repo_root / DEFAULT_WORKSPACE_DIR,
        max_spec_rounds=4,
        max_implementation_rounds=4,
        require_mission_approval=require_approval,
        heartbeat_seconds=0.01,
    )


def test_mission_artifacts_use_timestamped_session_layout(tmp_path: Path) -> None:
    artifacts = MissionArtifacts.from_workspace(
        tmp_path / DEFAULT_WORKSPACE_DIR,
        session_id="20260413T181500Z_pid42",
        started_at="2026-04-13T18:15:00Z",
    )
    artifacts.ensure_directories()

    assert artifacts.workspace_dir == tmp_path / DEFAULT_WORKSPACE_DIR
    assert artifacts.session_dir == artifacts.workspace_dir / "sessions" / "20260413T181500Z_pid42"
    assert artifacts.mission_spec_md == artifacts.session_dir / "mission_spec.md"
    assert artifacts.event_log_path == artifacts.session_dir / "events.jsonl"

    prompt_path = artifacts.prompt_path(
        "mission_spec_claude",
        1,
        timestamp_token="20260413T181501Z",
    )
    review_path = artifacts.review_path(
        "mission_spec_codex",
        1,
        timestamp_token="20260413T181502Z",
    )

    assert prompt_path.name == "20260413T181501Z_mission_spec_claude_round_01.txt"
    assert review_path.name == "20260413T181502Z_mission_spec_codex_round_01.json"


def test_mission_artifacts_allocate_unique_session_ids_when_timestamp_collides(tmp_path: Path) -> None:
    workspace_dir = tmp_path / DEFAULT_WORKSPACE_DIR
    started_at = "2026-04-13T18:15:00Z"
    base_session_id = session_id_from_timestamp(started_at)
    preexisting_session_dir = workspace_dir / "sessions" / base_session_id
    preexisting_session_dir.mkdir(parents=True)

    first = MissionArtifacts.from_workspace(workspace_dir, started_at=started_at)
    second = MissionArtifacts.from_workspace(workspace_dir, started_at=started_at)

    assert first.session_id == f"{base_session_id}_r02"
    assert second.session_id == f"{base_session_id}_r03"
    assert first.session_dir != second.session_dir


def test_full_run_retries_until_success_and_locks_spec(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "CLAUDE.md").write_text("Always keep docs and tests aligned.\n", encoding="utf-8")
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)
    output = io.StringIO()

    claude = FakeClaude(
        [
            "# Mission\nDraft one\n\n## Mission Success Criteria\nToo vague\n",
            "# Mission\nShip feature X\n\n## Mission Success Criteria\n- Behavior is observable\n\n"
            "## Required Behaviors\n- Does the thing\n\n## Test Plan\n- Run pytest\n\n"
            "## Constraints And Non-Goals\n- None\n",
            "## Accomplished\n- Implemented feature X\n\n## Tests Run\n- pytest -q\n\n"
            "## Remaining Risks\n- Missing regression coverage\n",
            "## Accomplished\n- Added the missing regression coverage\n\n## Tests Run\n- pytest -q\n\n"
            "## Remaining Risks\n- None\n",
        ]
    )
    codex = FakeCodex(
        [
            {
                "approved": False,
                "summary": "The spec is vague.",
                "issues": [
                    {
                        "severity": "high",
                        "title": "Missing falsifiable criteria",
                        "details": "State exact observable success conditions.",
                        "suggested_fix": "Rewrite the success criteria and test plan.",
                    }
                ],
            },
            {
                "approved": True,
                "summary": "Mission spec matches the request.",
                "issues": [],
            },
            {
                "mission_accomplished": False,
                "has_issues": True,
                "summary": "Implementation is incomplete.",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "test_gap",
                        "title": "Regression test missing",
                        "details": "The implementation summary says coverage is missing.",
                        "suggested_fix": "Add the regression test and rerun pytest.",
                    }
                ],
            },
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda mission_spec, path: ApprovalDecision(approved=True),
        output_stream=output,
    )

    result = orchestrator.run("Build feature X")

    assert result.success is True
    assert result.workspace_dir == str(artifacts.workspace_dir)
    assert result.session_dir == str(artifacts.session_dir)
    assert result.mission_spec_rounds == 2
    assert result.implementation_rounds == 2
    assert artifacts.session_dir.parent == artifacts.workspace_dir / "sessions"
    assert artifacts.mission_spec_md.exists()
    assert artifacts.mission_spec_lock.exists()
    assert artifacts.session_manifest_path.exists()
    assert artifacts.event_log_path.exists()
    assert artifacts.latest_path.exists()
    assert artifacts.report_path.exists()
    assert_mission_spec_locked(artifacts)

    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    assert manifest["session_id"] == artifacts.session_id
    assert manifest["task"] == "Build feature X"
    assert manifest["markdown_sha256"]

    prompt_files = sorted(artifacts.prompts_dir.iterdir())
    claude_output_files = sorted(artifacts.logs_dir.iterdir())
    codex_output_files = sorted(artifacts.reviews_dir.iterdir())
    assert len(prompt_files) == 8
    assert len(claude_output_files) == 4
    assert len(codex_output_files) == 4
    assert any("_mission_spec_claude_round_01.txt" in path.name for path in prompt_files)
    assert any("_implementation_claude_round_02.md" in path.name for path in claude_output_files)
    assert any("_implementation_codex_round_02.json" in path.name for path in codex_output_files)

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["session_id"] == artifacts.session_id
    assert report["session_dir"] == str(artifacts.session_dir)
    assert report["workspace_dir"] == str(artifacts.workspace_dir)
    assert report["task"] == "Build feature X"
    assert report["implementation_rounds"] == 2
    assert report["event_log_path"] == str(artifacts.event_log_path)
    assert report["session_manifest_path"] == str(artifacts.session_manifest_path)

    session_manifest = json.loads(artifacts.session_manifest_path.read_text(encoding="utf-8"))
    assert session_manifest["status"] == "succeeded"
    assert session_manifest["task"] == "Build feature X"
    assert session_manifest["artifacts"]["prompts_dir"] == str(artifacts.prompts_dir)

    latest = json.loads(artifacts.latest_path.read_text(encoding="utf-8"))
    assert latest["session_id"] == artifacts.session_id
    assert latest["status"] == "succeeded"

    events = [
        json.loads(line)
        for line in artifacts.event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[0]["type"] == "session_started"
    assert events[-1]["type"] == "session_finished"
    assert any(event["type"] == "mission_locked" for event in events)
    assert any(event["type"] == "prompt_written" for event in events)
    assert any(event["type"] == "output_written" and event["actor"] == "codex" for event in events)

    assert "Locked mission spec" in claude.calls[2][1]
    assert "Regression test missing" in claude.calls[3][1]

    rendered_output = output.getvalue()
    assert "Audax collaborative mission loop" in rendered_output
    assert "[Mission] locked at" in rendered_output
    assert "[Implementation] mission complete in 2 round(s)" in rendered_output


def test_user_approval_feedback_restarts_spec_loop(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)

    approval_calls: list[str] = []
    decisions = iter(
        [
            ApprovalDecision(approved=False, feedback="Add a rollback requirement."),
            ApprovalDecision(approved=True),
        ]
    )

    def approval_gate(mission_spec: str, path: Path) -> ApprovalDecision:
        approval_calls.append(mission_spec)
        return next(decisions)

    claude = FakeClaude(
        [
            "# Mission\nInitial\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "# Mission\nRevised\n\n## Mission Success Criteria\n- A\n- Rollback exists\n\n"
            "## Required Behaviors\n- B\n\n## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "## Accomplished\n- Done\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- None\n",
        ]
    )
    codex = FakeCodex(
        [
            {"approved": True, "summary": "Spec is acceptable.", "issues": []},
            {"approved": True, "summary": "Revised spec is acceptable.", "issues": []},
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root, require_approval=True),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=approval_gate,
        output_stream=io.StringIO(),
    )

    result = orchestrator.run("Ship feature Y")

    assert result.success is True
    assert result.mission_spec_rounds == 2
    assert len(approval_calls) == 2
    assert "Add a rollback requirement." in claude.calls[1][1]
    assert "Revised" in artifacts.mission_spec_md.read_text(encoding="utf-8")


def test_lock_manifest_detects_mutation(tmp_path: Path) -> None:
    artifacts = MissionArtifacts.from_workspace(tmp_path / DEFAULT_WORKSPACE_DIR)
    artifacts.ensure_directories()
    lock_mission_spec(
        "# Mission\nLocked\n\n## Mission Success Criteria\n- Deterministic\n",
        artifacts,
        "Lock task",
    )

    assert_mission_spec_locked(artifacts)
    artifacts.mission_spec_md.write_text("mutated\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Mission spec lock mismatch"):
        assert_mission_spec_locked(artifacts)


def test_parse_args_defaults_use_shorter_rounds_and_require_approval() -> None:
    args = parse_args([])

    assert args.spec_rounds == 3
    assert args.implementation_rounds == 5
    assert args.require_approval is True


def test_parse_args_can_disable_approval() -> None:
    args = parse_args(["--no-require-approval"])

    assert args.require_approval is False


def test_exhausted_spec_rounds_ship_latest_draft_for_approval(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)
    output = io.StringIO()

    claude = FakeClaude(
        [
            "# Mission\nOne\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "# Mission\nTwo\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "## Accomplished\n- done\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- none\n",
        ]
    )
    codex = FakeCodex(
        [
            {
                "approved": False,
                "summary": "Too weak.",
                "issues": [
                    {
                        "severity": "high",
                        "title": "Missing detail",
                        "details": "Tighten the criteria.",
                        "suggested_fix": "Be specific.",
                    }
                ],
            },
            {
                "approved": False,
                "summary": "Still too weak.",
                "issues": [
                    {
                        "severity": "high",
                        "title": "Missing detail",
                        "details": "Tighten the criteria.",
                        "suggested_fix": "Be specific.",
                    }
                ],
            },
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=LoopConfig(
            repo_root=repo_root,
            workspace_dir=repo_root / DEFAULT_WORKSPACE_DIR,
            max_spec_rounds=2,
            max_implementation_rounds=4,
            require_mission_approval=True,
            heartbeat_seconds=0.01,
        ),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=output,
    )

    result = orchestrator.run("Build feature Z")

    assert result.success is True
    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    assert report["mission_spec_rounds"] == 2
    assert report["implementation_rounds"] == 1
    assert report["error"] == ""
    assert report["latest_mission_spec_review_approved"] is False
    assert "Still too weak." in report["latest_mission_spec_review_feedback"]
    assert artifacts.mission_spec_lock.exists()
    rendered = output.getvalue()
    assert "shipping the latest draft for final approval" in rendered
    assert "latest Codex reject message" in rendered
    assert "Still too weak." in rendered


def test_exhausted_spec_rounds_lock_latest_draft_when_approval_disabled(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)
    output = io.StringIO()

    claude = FakeClaude(
        [
            "# Mission\nOne\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "## Accomplished\n- done\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- none\n",
        ]
    )
    codex = FakeCodex(
        [
            {
                "approved": False,
                "summary": "Needs follow-up.",
                "issues": [
                    {
                        "severity": "medium",
                        "title": "Missing detail",
                        "details": "Tighten the criteria.",
                        "suggested_fix": "Be specific.",
                    }
                ],
            },
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=LoopConfig(
            repo_root=repo_root,
            workspace_dir=repo_root / DEFAULT_WORKSPACE_DIR,
            max_spec_rounds=1,
            max_implementation_rounds=2,
            require_mission_approval=False,
            heartbeat_seconds=0.01,
        ),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: (_ for _ in ()).throw(AssertionError("approval gate should not run")),
        output_stream=output,
    )

    result = orchestrator.run("Build feature auto-lock")

    assert result.success is True
    rendered = output.getvalue()
    assert "locking the latest draft with unresolved review feedback" in rendered
    assert "Needs follow-up." in rendered


def test_failed_implementation_run_report_keeps_completed_round_count(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)

    claude = FakeClaude(
        [
            "# Mission\nGood\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "## Accomplished\n- Partial work\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Still open\n",
            "## Accomplished\n- More partial work\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Still open\n",
            "## Accomplished\n- Even more partial work\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Still open\n",
            "## Accomplished\n- Yet more partial work\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Still open\n",
        ]
    )
    codex = FakeCodex(
        [
            {"approved": True, "summary": "Spec is acceptable.", "issues": []},
            {
                "mission_accomplished": False,
                "has_issues": True,
                "summary": "Round one incomplete.",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "bug",
                        "title": "Still broken",
                        "details": "The repo is not ready.",
                        "suggested_fix": "Finish the change.",
                    }
                ],
            },
            {
                "mission_accomplished": False,
                "has_issues": True,
                "summary": "Round two incomplete.",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "bug",
                        "title": "Still broken",
                        "details": "The repo is not ready.",
                        "suggested_fix": "Finish the change.",
                    }
                ],
            },
            {
                "mission_accomplished": False,
                "has_issues": True,
                "summary": "Round three incomplete.",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "bug",
                        "title": "Still broken",
                        "details": "The repo is not ready.",
                        "suggested_fix": "Finish the change.",
                    }
                ],
            },
            {
                "mission_accomplished": False,
                "has_issues": True,
                "summary": "Round four incomplete.",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "bug",
                        "title": "Still broken",
                        "details": "The repo is not ready.",
                        "suggested_fix": "Finish the change.",
                    }
                ],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )

    with pytest.raises(RuntimeError, match="Implementation failed to converge within 4 round\\(s\\)"):
        orchestrator.run("Build feature Z")

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    assert report["mission_spec_rounds"] == 1
    assert report["implementation_rounds"] == 4
    assert report["error"] == "Implementation failed to converge within 4 round(s)"


def _seed_interrupted_session(repo_root: Path) -> MissionArtifacts:
    """Run a full mission that fails implementation and return the artifacts."""
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)
    claude = FakeClaude(
        [
            "# Mission\nShip feature R\n\n## Mission Success Criteria\n- Observable\n\n"
            "## Required Behaviors\n- Does thing\n\n## Test Plan\n- pytest\n\n"
            "## Constraints And Non-Goals\n- None\n",
            "## Accomplished\n- Partial\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Open\n",
            "## Accomplished\n- Still partial\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Open\n",
            "## Accomplished\n- Still partial\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Open\n",
            "## Accomplished\n- Still partial\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- Open\n",
        ]
    )
    incomplete_review = {
        "mission_accomplished": False,
        "has_issues": True,
        "summary": "Not done.",
        "issues": [
            {
                "severity": "medium",
                "category": "bug",
                "title": "Open",
                "details": "Finish it.",
                "suggested_fix": "Finish.",
            }
        ],
    }
    codex = FakeCodex(
        [
            {"approved": True, "summary": "Spec is acceptable.", "issues": []},
            incomplete_review,
            incomplete_review,
            incomplete_review,
            incomplete_review,
        ]
    )
    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )
    with pytest.raises(RuntimeError):
        orchestrator.run("Ship feature R")
    return artifacts


def test_resume_continues_failed_session_against_locked_spec(tmp_path: Path) -> None:
    repo_root = tmp_path
    seeded = _seed_interrupted_session(repo_root)
    workspace_dir = repo_root / DEFAULT_WORKSPACE_DIR

    resumable = find_resumable_sessions(workspace_dir)
    assert [entry[0] for entry in resumable] == [seeded.session_id]

    manifest = load_session_manifest(workspace_dir, seeded.session_id)
    assert manifest["status"] == "failed"
    assert manifest["task"] == "Ship feature R"

    artifacts = MissionArtifacts.from_workspace(
        workspace_dir,
        session_id=seeded.session_id,
        started_at=manifest["started_at"],
    )
    assert artifacts.session_dir == seeded.session_dir

    import audax_core.app as app_module

    locked_spec = app_module._load_locked_spec(artifacts)

    resume_claude = FakeClaude(
        [
            "## Accomplished\n- Finished feature R\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- None\n",
        ]
    )
    resume_codex = FakeCodex(
        [
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete after resume.",
                "issues": [],
            }
        ]
    )
    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=resume_claude,
        codex=resume_codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )

    result = orchestrator.resume("Ship feature R", locked_spec)

    assert result.success is True
    assert result.session_id == seeded.session_id
    # Drafting is skipped on resume, so Claude was only called for implementation.
    assert len(resume_claude.calls) == 1
    assert "Locked mission spec" in resume_claude.calls[0][1]

    manifest_after = load_session_manifest(workspace_dir, seeded.session_id)
    assert manifest_after["status"] == "succeeded"

    events = [
        json.loads(line)
        for line in artifacts.event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["type"] == "session_resumed" for event in events)

    # Succeeded sessions are no longer offered as resumable candidates.
    assert find_resumable_sessions(workspace_dir) == []


def test_resume_rehydrates_last_codex_feedback_into_first_round(tmp_path: Path) -> None:
    repo_root = tmp_path
    seeded = _seed_interrupted_session(repo_root)
    workspace_dir = repo_root / DEFAULT_WORKSPACE_DIR

    artifacts = MissionArtifacts.from_workspace(
        workspace_dir,
        session_id=seeded.session_id,
        started_at=seeded.started_at,
    )

    import audax_core.app as app_module

    locked_spec = app_module._load_locked_spec(artifacts)

    resume_claude = FakeClaude(
        [
            "## Accomplished\n- Fixed the open issue\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- None\n",
        ]
    )
    resume_codex = FakeCodex(
        [
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Done.",
                "issues": [],
            }
        ]
    )
    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=resume_claude,
        codex=resume_codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )

    result = orchestrator.resume("Ship feature R", locked_spec)
    assert result.success is True

    first_prompt = resume_claude.calls[0][1]
    # The seeded codex review kept raising the "Open" / "Finish it." issue,
    # so the first resumed implementation prompt must carry that feedback
    # instead of the "No outstanding reviewer feedback." placeholder.
    assert "No outstanding reviewer feedback." not in first_prompt
    assert "Open" in first_prompt
    assert "Finish it." in first_prompt

    events = [
        json.loads(line)
        for line in artifacts.event_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["type"] == "resume_feedback_rehydrated" for event in events)


def test_resume_skips_rehydration_when_no_prior_review(tmp_path: Path) -> None:
    """A freshly-locked spec with no prior implementation round has nothing to rehydrate."""
    repo_root = tmp_path
    workspace_dir = repo_root / DEFAULT_WORKSPACE_DIR
    artifacts = MissionArtifacts.from_workspace(workspace_dir)
    artifacts.ensure_directories()

    from audax_core.artifacts import lock_mission_spec

    lock_mission_spec(
        "# Mission\nShip\n\n## Mission Success Criteria\n- A\n",
        artifacts,
        "Ship",
    )
    import audax_core.app as app_module

    locked_spec = app_module._load_locked_spec(artifacts)

    claude = FakeClaude(
        [
            "## Accomplished\n- Implemented\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- None\n",
        ]
    )
    codex = FakeCodex(
        [
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Done.",
                "issues": [],
            }
        ]
    )
    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )

    result = orchestrator.resume("Ship", locked_spec)
    assert result.success is True
    assert "No outstanding reviewer feedback." in claude.calls[0][1]


def test_resume_rejects_session_with_mutated_mission_spec(tmp_path: Path) -> None:
    repo_root = tmp_path
    seeded = _seed_interrupted_session(repo_root)
    seeded.mission_spec_md.write_text("tampered contents\n", encoding="utf-8")

    artifacts = MissionArtifacts.from_workspace(
        repo_root / DEFAULT_WORKSPACE_DIR,
        session_id=seeded.session_id,
        started_at=seeded.started_at,
    )

    import audax_core.app as app_module

    with pytest.raises(RuntimeError, match="Mission spec lock mismatch"):
        app_module._load_locked_spec(artifacts)


def test_parse_continue_args_defaults() -> None:
    args = parse_continue_args([])
    assert args.session_id is None
    assert args.implementation_rounds == 5
    assert args.workspace_dir == DEFAULT_WORKSPACE_DIR


def test_parse_continue_args_accepts_session_id() -> None:
    args = parse_continue_args(["20260413T181500Z_pid42"])
    assert args.session_id == "20260413T181500Z_pid42"


def test_continue_main_errors_when_no_resumable_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = continue_main([])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "No resumable sessions found" in captured.err


def test_heartbeat_progress_reports_without_streaming_payload() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    stream = io.StringIO()
    progress = HeartbeatProgress(
        "Codex mission review",
        interval_seconds=0.5,
        stream=stream,
        clock=clock,
    )

    progress.start()
    clock.now = 0.2
    progress.maybe_emit()
    clock.now = 0.7
    progress.maybe_emit()
    clock.now = 1.0
    progress.finish(success=True)

    rendered = stream.getvalue()
    assert "[Codex mission review] working..." in rendered
    assert "still working" in rendered
    assert "[Codex mission review] done (1s)" in rendered


def test_heartbeat_progress_uses_current_stdout_by_default() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    stream = io.StringIO()
    with redirect_stdout(stream):
        progress = HeartbeatProgress("stdout capture", interval_seconds=0, clock=clock)
        progress.start()
        clock.now = 1.0
        progress.finish(success=True)

    rendered = stream.getvalue()
    assert "[stdout capture] working..." in rendered
    assert "[stdout capture] done (1s)" in rendered


def test_heartbeat_progress_uses_inline_spinner_for_tty() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    clock = FakeClock()
    stream = FakeTTY()
    progress = HeartbeatProgress(
        "tty capture",
        interval_seconds=5.0,
        stream=stream,
        clock=clock,
    )

    progress.start()
    clock.now = 0.1
    progress.maybe_emit()
    clock.now = 1.0
    progress.finish(success=True)

    rendered = stream.getvalue()
    assert "\r[tty capture] | working (0s)" in rendered
    assert "\r[tty capture] / working (0s)" in rendered
    assert "\r[tty capture] done (1s)" in rendered
    assert rendered.endswith("\n")
    assert "still working" not in rendered


def test_quiet_process_runner_times_out_long_running_process(tmp_path: Path) -> None:
    runner = QuietProcessRunner(
        heartbeat_seconds=0.01,
        progress_stream=io.StringIO(),
        subprocess_timeout_seconds=0.2,
    )

    started_at = time.monotonic()
    with pytest.raises(RuntimeError, match="sleep test timed out after 0.2s"):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            "sleep test",
            cwd=tmp_path,
        )

    assert time.monotonic() - started_at < 2


def test_quiet_process_runner_uses_current_stdout_by_default(tmp_path: Path) -> None:
    stream = io.StringIO()
    with redirect_stdout(stream):
        runner = QuietProcessRunner(heartbeat_seconds=0.01)
        output = runner.run(
            [sys.executable, "-c", "print('runner ok')"],
            "stdout runner test",
            cwd=tmp_path,
        )

    rendered = stream.getvalue()
    assert "runner ok" in output
    assert "[stdout runner test] working..." in rendered
    assert "[stdout runner test] done" in rendered


def test_quiet_process_runner_uses_inline_spinner_for_tty(tmp_path: Path) -> None:
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = FakeTTY()
    runner = QuietProcessRunner(
        heartbeat_seconds=5.0,
        progress_stream=stream,
    )
    output = runner.run(
        [sys.executable, "-c", "import time; time.sleep(0.25); print('runner ok')"],
        "tty runner test",
        cwd=tmp_path,
    )

    rendered = stream.getvalue()
    assert "runner ok" in output
    assert "\r[tty runner test]" in rendered
    assert "still working" not in rendered
    assert rendered.endswith("\n")


def test_quiet_process_runner_interrupt_kills_process_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "pids.json"
    runner = QuietProcessRunner(
        heartbeat_seconds=0.01,
        progress_stream=io.StringIO(),
        subprocess_timeout_seconds=10,
    )
    original_sleep = time.sleep
    interrupted = False

    def interrupting_sleep(seconds: float) -> None:
        nonlocal interrupted
        if interrupted:
            original_sleep(seconds)
            return
        if pid_file.exists() and pid_file.stat().st_size > 0:
            interrupted = True
            raise KeyboardInterrupt
        original_sleep(min(seconds, 0.01))

    monkeypatch.setattr("audax_core.progress.time.sleep", interrupting_sleep)

    script = (
        "import json, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "pid_path = Path(sys.argv[1])\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pid_path.write_text(json.dumps({'parent': __import__(\"os\").getpid(), 'child': child.pid}))\n"
        "time.sleep(30)\n"
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run(
            [sys.executable, "-c", script, str(pid_file)],
            "interrupt cleanup test",
            cwd=tmp_path,
        )

    pids = json.loads(pid_file.read_text(encoding="utf-8"))

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        all_gone = True
        for pid in (pids["parent"], pids["child"]):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            else:
                all_gone = False
                break
        if all_gone:
            break
        time.sleep(0.01)

    for pid in (pids["parent"], pids["child"]):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_quiet_process_runner_uses_taskkill_for_windows_process_trees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = QuietProcessRunner(progress_stream=io.StringIO())
    taskkill_calls: list[list[str]] = []

    class FakeProc:
        pid = 4321

        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False

        def poll(self) -> int | None:
            return 0 if self.terminated else None

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="taskkill", timeout=timeout)
            self.terminated = True
            return 0

        def terminate(self) -> None:
            raise AssertionError("taskkill should handle non-force termination on Windows")

        def kill(self) -> None:
            raise AssertionError("taskkill should handle forced termination on Windows")

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        taskkill_calls.append(cmd)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr("audax_core.progress.os.name", "nt")
    monkeypatch.setattr("audax_core.progress.subprocess.run", fake_run)

    runner._terminate_process(FakeProc())

    assert taskkill_calls == [
        ["taskkill", "/PID", "4321", "/T"],
        ["taskkill", "/PID", "4321", "/T", "/F"],
    ]


def test_interactive_mission_approval_accepts_request_changes_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(["request changes", "Add rollback instructions.", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    result = interactive_mission_approval("spec", Path("mission_spec.md"))

    assert result == ApprovalDecision(approved=False, feedback="Add rollback instructions.")


def test_interactive_mission_approval_treats_no_as_request_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(["n", "Add integration coverage.", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    result = interactive_mission_approval("spec", Path("mission_spec.md"))

    assert result == ApprovalDecision(approved=False, feedback="Add integration coverage.")


def test_parse_claude_stream_output_returns_only_text_deltas() -> None:
    output = "\n".join(
        [
            '{"type":"system","subtype":"init"}',
            '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"CLAUDE_"}}}',
            '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"STREAM_"}}}',
            '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"OK"}}}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"extra wrapper"}]}}',
            '{"type":"result","result":"FINAL_RESULT"}',
        ]
    )

    assert parse_claude_stream_output(output) == "FINAL_RESULT"


def test_claude_cli_sends_prompt_via_stdin_instead_of_argv(tmp_path: Path) -> None:
    class FakeProcessRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def run(
            self,
            cmd: list[str],
            label: str,
            *,
            cwd: Path,
            stdin_text: str | None = None,
        ) -> str:
            self.calls.append(
                {
                    "cmd": cmd,
                    "label": label,
                    "cwd": cwd,
                    "stdin_text": stdin_text,
                }
            )
            return '{"type":"result","result":"CLAUDE_OK"}\n'

    runner = FakeProcessRunner()
    cli = ClaudeCLI("claude", runner, tmp_path)
    prompt = "Large prompt block " * 20_000

    result = cli.run(prompt, label="Claude stdin transport test")

    assert result == "CLAUDE_OK"
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["stdin_text"] == prompt
    assert call["cmd"] == [
        "claude",
        "-p",
        "--input-format",
        "text",
        "--model",
        "opus",
        "--effort",
        "max",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]


def test_orchestrator_uses_current_stdout_by_default(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)
    claude = FakeClaude(
        [
            "# Mission\nShip feature\n\n## Mission Success Criteria\n- Works\n\n"
            "## Required Behaviors\n- Do it\n\n## Test Plan\n- Run pytest\n\n"
            "## Constraints And Non-Goals\n- None\n",
            "## Accomplished\n- Done\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- None\n",
        ]
    )
    codex = FakeCodex(
        [
            {"approved": True, "summary": "Spec is acceptable.", "issues": []},
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    stream = io.StringIO()
    with redirect_stdout(stream):
        orchestrator = ReviewLoopOrchestrator(
            config=make_config(repo_root),
            artifacts=artifacts,
            claude=claude,
            codex=codex,
            approval_gate=lambda *_: ApprovalDecision(approved=True),
        )
        result = orchestrator.run("Build feature Q")

    assert result.success is True
    rendered = stream.getvalue()
    assert "Audax collaborative mission loop" in rendered
    assert "[Mission] locked at" in rendered


def test_render_session_header_card_uses_box_layout() -> None:
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    config = LoopConfig(
        repo_root=Path("/tmp/repo"),
        workspace_dir=Path("/tmp/repo/audax_artifacts"),
        max_spec_rounds=10,
        max_implementation_rounds=50,
        require_mission_approval=True,
    )
    rendered = render_session_header_card(
        "Build a dashboard for 潜能恒信 with prices, news, sentiment, vol, and returns",
        config,
        FakeTTY(),
    )

    assert "AUDAX COLLABORATIVE MISSION LOOP" in rendered
    assert "Task:" in rendered
    assert "Repo:" in rendered
    assert "Workspace:" in rendered
    assert "Mission approval: required" in rendered
    assert "╭" in rendered and "╰" in rendered


def test_render_startup_card_uses_box_layout() -> None:
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    args = argparse.Namespace(
        spec_rounds=6,
        implementation_rounds=20,
        workspace_dir="custom_artifacts",
        require_approval=True,
        heartbeat_seconds=2.5,
        subprocess_timeout_seconds=0,
        claude_cmd="claude-custom",
        codex_cmd="codex-custom",
    )
    rendered = render_startup_card(
        FakeTTY(),
        build_startup_card_info_lines(args, repo_root=Path("/tmp/repo")),
    )

    assert "AUDAX CONSOLE" in rendered
    assert "Enter the mission prompt for Audax." in rendered
    assert "Audax will make changes in: /tmp/repo" in rendered
    assert "--spec-rounds: 6" in rendered
    assert "--subprocess-timeout-seconds: disabled" in rendered
    assert "--require-approval/--no-require-approval: enabled" in rendered
    assert "Claude runtime selected by Audax:" in rendered
    assert "Codex runtime selected by Audax:" in rendered
    assert "model: opus" in rendered
    assert "reasoning effort: max" in rendered
    assert "model: gpt-5.4" in rendered
    assert "reasoning effort: xhigh" in rendered
    assert "╭" in rendered and "╰" in rendered


def test_build_repo_context_handles_symlinked_repo_root(tmp_path: Path) -> None:
    real_repo_root = tmp_path / "real_repo"
    real_repo_root.mkdir()
    (real_repo_root / "README.md").write_text("symlink-safe context\n", encoding="utf-8")

    symlink_repo_root = tmp_path / "linked_repo"
    try:
        symlink_repo_root.symlink_to(real_repo_root, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available in this environment")

    context = build_repo_context(symlink_repo_root, symlink_repo_root / DEFAULT_WORKSPACE_DIR)

    assert "FILE: README.md" in context
    assert "symlink-safe context" in context


def test_build_repo_context_excludes_generated_dirs(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("ROOT POLICY\n", encoding="utf-8")

    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "README.md").write_text("REAL NESTED POLICY\n", encoding="utf-8")

    pytest_cache_dir = tmp_path / ".pytest_cache"
    pytest_cache_dir.mkdir()
    (pytest_cache_dir / "README.md").write_text("PYTEST CACHE\n", encoding="utf-8")

    docs_build_dir = tmp_path / "docs" / "_build"
    docs_build_dir.mkdir(parents=True)
    (docs_build_dir / "README.md").write_text("DOC BUILD OUTPUT\n", encoding="utf-8")

    node_modules_dir = tmp_path / "node_modules" / "dependency"
    node_modules_dir.mkdir(parents=True)
    (node_modules_dir / "README.md").write_text("THIRD PARTY README\n", encoding="utf-8")

    pycache_dir = tmp_path / "pkg" / "__pycache__"
    pycache_dir.mkdir(parents=True)
    (pycache_dir / "README.md").write_text("BYTECODE CACHE\n", encoding="utf-8")

    rules = discover_rule_files(tmp_path, tmp_path / DEFAULT_WORKSPACE_DIR)
    rel_paths = {path.relative_to(tmp_path).as_posix() for path in rules}

    assert rel_paths == {"README.md", "policies/README.md"}

    context = build_repo_context(tmp_path, tmp_path / DEFAULT_WORKSPACE_DIR)
    assert "ROOT POLICY" in context
    assert "REAL NESTED POLICY" in context
    assert "PYTEST CACHE" not in context
    assert "DOC BUILD OUTPUT" not in context
    assert "THIRD PARTY README" not in context
    assert "BYTECODE CACHE" not in context


def test_main_returns_130_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("audax_core.app.ensure_cli_available", lambda cmd: None)

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, task: str) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr("audax_core.app.ReviewLoopOrchestrator", FakeOrchestrator)

    exit_code = main(["Build feature X", "--claude-cmd", "claude", "--codex-cmd", "codex"])
    captured = capsys.readouterr()

    assert exit_code == 130
    assert "Interrupted." in captured.err


def test_read_task_prompts_for_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("build the thing"))
    task = read_task(argparse.Namespace(task=[]))
    captured = capsys.readouterr()

    assert task == "build the thing"
    assert "Enter the mission prompt for Audax." in captured.out
    assert "Press Ctrl-D when you are done." in captured.out


def test_read_task_renders_tty_startup_card(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.chdir(Path("/tmp"))
    stdout = FakeTTY()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stdin", io.StringIO("build the thing"))

    task = read_task(
        argparse.Namespace(
            task=[],
            spec_rounds=7,
            implementation_rounds=12,
            workspace_dir="session_artifacts",
            require_approval=True,
            heartbeat_seconds=1.5,
            subprocess_timeout_seconds=45,
            claude_cmd="claude-enterprise",
            codex_cmd="codex-enterprise",
        )
    )

    rendered = stdout.getvalue()
    assert task == "build the thing"
    assert "AUDAX CONSOLE" in rendered
    assert "Enter the mission prompt for Audax." in rendered
    assert "Press Ctrl-D when you are done." in rendered
    assert "Audax will make changes in:" in rendered
    assert "--implementation-rounds: 12" in rendered
    assert "--claude-cmd: claude-enterprise" in rendered
    assert "--require-approval/--no-require-approval: enabled" in rendered
    assert "model: opus" in rendered
    assert "reasoning effort: max" in rendered
    assert "approvals/sandbox: dangerously-bypass-approvals-and-sandbox" in rendered
    assert "╭" in rendered and "╰" in rendered


def test_user_approval_feedback_survives_subsequent_codex_rejection(tmp_path: Path) -> None:
    """User-requested changes must persist across Codex rejection rounds.

    Regression: previously, if Codex approved round N (and the user rejected with
    feedback), a Codex rejection in round N+1 would overwrite the pending feedback
    and drop the user's explicit requirement from subsequent Claude prompts.
    """
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / DEFAULT_WORKSPACE_DIR)

    approvals = iter(
        [
            ApprovalDecision(approved=False, feedback="PERSIST_ROLLBACK_REQUIREMENT"),
            ApprovalDecision(approved=True),
        ]
    )

    claude = FakeClaude(
        [
            "# Mission\nA\n\n## Mission Success Criteria\n- ok\n\n## Required Behaviors\n- do\n\n"
            "## Test Plan\n- run pytest\n\n## Constraints And Non-Goals\n- none\n",
            "# Mission\nB\n\n## Mission Success Criteria\n- ok\n\n## Required Behaviors\n- do\n\n"
            "## Test Plan\n- run pytest\n\n## Constraints And Non-Goals\n- none\n",
            "# Mission\nC\n\n## Mission Success Criteria\n- ok\n\n## Required Behaviors\n- do\n\n"
            "## Test Plan\n- run pytest\n\n## Constraints And Non-Goals\n- none\n",
            "## Accomplished\n- done\n\n## Tests Run\n- pytest -q\n\n## Remaining Risks\n- none\n",
        ]
    )
    codex = FakeCodex(
        [
            {"approved": True, "summary": "Spec approved.", "issues": []},
            {
                "approved": False,
                "summary": "Revised spec needs more rigor.",
                "issues": [
                    {
                        "severity": "high",
                        "title": "CODEX_ROUND_TWO_GAP",
                        "details": "More detail needed.",
                        "suggested_fix": "Expand the behavior list.",
                    }
                ],
            },
            {"approved": True, "summary": "Spec approved.", "issues": []},
            {
                "mission_accomplished": True,
                "has_issues": False,
                "summary": "Mission complete.",
                "issues": [],
            },
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=make_config(repo_root, require_approval=True),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda spec, path: next(approvals),
        output_stream=io.StringIO(),
    )

    result = orchestrator.run("Ship feature with rollback")

    assert result.success is True
    assert result.mission_spec_rounds == 3
    round_two_prompt = claude.calls[1][1]
    round_three_prompt = claude.calls[2][1]
    assert "PERSIST_ROLLBACK_REQUIREMENT" in round_two_prompt
    assert "PERSIST_ROLLBACK_REQUIREMENT" in round_three_prompt, (
        "User approval feedback must persist after Codex rejects a later revision"
    )
    assert "CODEX_ROUND_TWO_GAP" in round_three_prompt, (
        "Codex feedback from the rejecting round must also be carried into round 3"
    )


def test_discover_rule_files_dedupes_same_inode(tmp_path: Path) -> None:
    """The same underlying file must only be discovered once.

    Regression: on case-insensitive filesystems (macOS/Windows) a single
    ``CLAUDE.md`` was reachable via both ``CLAUDE.md`` and ``Claude.md`` entries
    in ``RULE_FILENAMES`` because ``Path.resolve()`` preserves the casing of the
    requested name, so set-based dedup failed. Here we use a hardlink so the
    same regression reproduces on case-sensitive filesystems too.
    """
    canonical = tmp_path / "CLAUDE.md"
    canonical.write_text("THE RULE\n", encoding="utf-8")

    alt = tmp_path / "Claude.md"
    if not (alt.exists() and alt.samefile(canonical)):
        try:
            os.link(canonical, alt)
        except (NotImplementedError, OSError):
            pytest.skip("hardlinks are not available in this environment")
        if not alt.exists() or not alt.samefile(canonical):
            pytest.skip("filesystem did not produce a shared-inode alias")

    rules = discover_rule_files(tmp_path, tmp_path / DEFAULT_WORKSPACE_DIR)

    identities = {(p.stat().st_dev, p.stat().st_ino) for p in rules}
    assert len(rules) == len(identities), (
        f"Same file discovered multiple times: {[str(p) for p in rules]}"
    )
    assert len(rules) == 1

    context = build_repo_context(tmp_path, tmp_path / DEFAULT_WORKSPACE_DIR)
    assert context.count("THE RULE") == 1
