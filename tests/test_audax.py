from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
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
from audax_core.backends import parse_claude_stream_output
from audax_core.app import main, read_task
from audax_core.progress import QuietProcessRunner


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
        workspace_dir=repo_root / ".audax",
        max_spec_rounds=4,
        max_implementation_rounds=4,
        require_mission_approval=require_approval,
        heartbeat_seconds=0.01,
    )


def test_full_run_retries_until_success_and_locks_spec(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "CLAUDE.md").write_text("Always keep docs and tests aligned.\n", encoding="utf-8")
    artifacts = MissionArtifacts.from_workspace(repo_root / ".audax")
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
    assert result.mission_spec_rounds == 2
    assert result.implementation_rounds == 2
    assert artifacts.mission_spec_md.exists()
    assert artifacts.mission_spec_pdf.exists()
    assert artifacts.mission_spec_lock.exists()
    assert artifacts.report_path.exists()
    assert_mission_spec_locked(artifacts)

    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    assert manifest["task"] == "Build feature X"
    assert manifest["markdown_sha256"]
    assert manifest["pdf_sha256"]
    assert artifacts.mission_spec_pdf.read_bytes().startswith(b"%PDF-1.4")

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["implementation_rounds"] == 2
    assert "Locked mission spec" in claude.calls[2][1]
    assert "Regression test missing" in claude.calls[3][1]

    rendered_output = output.getvalue()
    assert "Audax collaborative mission loop" in rendered_output
    assert "[Mission] locked at" in rendered_output
    assert "[Implementation] mission complete in 2 round(s)" in rendered_output


def test_user_approval_feedback_restarts_spec_loop(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / ".audax")

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
    artifacts = MissionArtifacts.from_workspace(tmp_path / ".audax")
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


def test_failed_spec_run_report_keeps_completed_round_count(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / ".audax")

    claude = FakeClaude(
        [
            "# Mission\nOne\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
            "# Mission\nTwo\n\n## Mission Success Criteria\n- A\n\n## Required Behaviors\n- B\n\n"
            "## Test Plan\n- C\n\n## Constraints And Non-Goals\n- D\n",
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
        ]
    )

    orchestrator = ReviewLoopOrchestrator(
        config=LoopConfig(
            repo_root=repo_root,
            workspace_dir=repo_root / ".audax",
            max_spec_rounds=2,
            max_implementation_rounds=4,
            heartbeat_seconds=0.01,
        ),
        artifacts=artifacts,
        claude=claude,
        codex=codex,
        approval_gate=lambda *_: ApprovalDecision(approved=True),
        output_stream=io.StringIO(),
    )

    with pytest.raises(RuntimeError, match="Mission spec failed to converge within 2 round\\(s\\)"):
        orchestrator.run("Build feature Z")

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    assert report["mission_spec_rounds"] == 2
    assert report["implementation_rounds"] == 0
    assert report["error"] == "Mission spec failed to converge within 2 round(s)"


def test_failed_implementation_run_report_keeps_completed_round_count(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts = MissionArtifacts.from_workspace(repo_root / ".audax")

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
        if pid_file.exists():
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
            '{"type":"result","result":"CLAUDE_STREAM_OK"}',
        ]
    )

    assert parse_claude_stream_output(output) == "CLAUDE_STREAM_OK"


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
