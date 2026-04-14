"""CLI entrypoints for launching the Audax review loop."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import sys

from .backends import (
    CLAUDE_INCLUDE_PARTIAL_MESSAGES,
    CLAUDE_INPUT_FORMAT,
    CLAUDE_MODEL,
    CLAUDE_OUTPUT_FORMAT,
    CLAUDE_REASONING_EFFORT,
    CLAUDE_SKIP_PERMISSIONS,
    CLAUDE_VERBOSE,
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CODEX_MODEL,
    CODEX_REASONING_EFFORT,
    ClaudeCLI,
    CodexCLI,
)
from .models import (
    CLAUDE_CMD,
    CODEX_CMD,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IMPLEMENTATION_ROUNDS,
    DEFAULT_SPEC_ROUNDS,
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    DEFAULT_WORKSPACE_DIR,
    LoopConfig,
    MissionArtifacts,
)
from .orchestrator import ReviewLoopOrchestrator
from .progress import QuietProcessRunner
from .ui import render_startup_card, supports_rich_terminal


def ensure_cli_available(cmd: str) -> None:
    """Raise an error when a required external CLI is not available."""
    if shutil.which(cmd):
        return
    raise RuntimeError(f"Required command not found in PATH: {cmd}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the top-level ``audax.py`` launcher."""
    parser = argparse.ArgumentParser(description="Audax collaborative review loop")
    parser.add_argument("task", nargs="*", help="Mission request. If omitted, stdin is used.")
    parser.add_argument("--spec-rounds", type=int, default=DEFAULT_SPEC_ROUNDS)
    parser.add_argument("--implementation-rounds", type=int, default=DEFAULT_IMPLEMENTATION_ROUNDS)
    parser.add_argument("--workspace-dir", default=DEFAULT_WORKSPACE_DIR)
    parser.add_argument(
        "--require-approval",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require an interactive approval decision before the mission spec is locked.",
    )
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument(
        "--subprocess-timeout-seconds",
        type=float,
        default=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        help="Kill agent CLI subprocesses after this many seconds. Use 0 to disable.",
    )
    parser.add_argument("--claude-cmd", default=CLAUDE_CMD)
    parser.add_argument("--codex-cmd", default=CODEX_CMD)
    return parser.parse_args(argv)


def resolve_workspace_dir(repo_root: Path, workspace_dir_arg: str) -> Path:
    """Resolve the configured workspace path relative to the repository root."""
    workspace_dir = Path(workspace_dir_arg)
    if not workspace_dir.is_absolute():
        workspace_dir = repo_root / workspace_dir
    return workspace_dir


def _format_seconds(seconds: float | None) -> str:
    """Render a duration value for the startup summary."""
    if seconds is None:
        return "disabled"
    return f"{seconds:g}s"


def _describe_optional_setting(value: str | None) -> str:
    """Render unset backend settings without implying a hidden default."""
    if value is None:
        return "CLI default (Audax does not override it)"
    return value


def build_startup_card_info_lines(
    args: argparse.Namespace,
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Build the rich startup-card summary shown before stdin task entry."""
    repo_root = repo_root or Path.cwd()
    workspace_dir = resolve_workspace_dir(
        repo_root,
        getattr(args, "workspace_dir", DEFAULT_WORKSPACE_DIR),
    )
    subprocess_timeout_seconds = getattr(
        args,
        "subprocess_timeout_seconds",
        DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return [
        "Enter the mission prompt for Audax.",
        "Press Ctrl-D when you are done.",
        f"Audax will make changes in: {repo_root}",
        "",
        "Adjustable flags for this session:",
        f"--spec-rounds: {getattr(args, 'spec_rounds', DEFAULT_SPEC_ROUNDS)}",
        (
            "--implementation-rounds: "
            f"{getattr(args, 'implementation_rounds', DEFAULT_IMPLEMENTATION_ROUNDS)}"
        ),
        f"--workspace-dir: {workspace_dir}",
        (
            "--require-approval/--no-require-approval: "
            f"{'enabled' if getattr(args, 'require_approval', True) else 'disabled'}"
        ),
        f"--heartbeat-seconds: {_format_seconds(getattr(args, 'heartbeat_seconds', DEFAULT_HEARTBEAT_SECONDS))}",
        (
            "--subprocess-timeout-seconds: "
            f"{_format_seconds(None if subprocess_timeout_seconds == 0 else subprocess_timeout_seconds)}"
        ),
        f"--claude-cmd: {getattr(args, 'claude_cmd', CLAUDE_CMD)}",
        f"--codex-cmd: {getattr(args, 'codex_cmd', CODEX_CMD)}",
        "",
        "Claude runtime selected by Audax:",
        f"model: {_describe_optional_setting(CLAUDE_MODEL)}",
        f"reasoning effort: {_describe_optional_setting(CLAUDE_REASONING_EFFORT)}",
        (
            "permissions: dangerously-skip-permissions"
            if CLAUDE_SKIP_PERMISSIONS
            else "permissions: CLI default"
        ),
        (
            "I/O: "
            f"{CLAUDE_INPUT_FORMAT} prompt -> {CLAUDE_OUTPUT_FORMAT} output"
            f"{' with verbose logging' if CLAUDE_VERBOSE else ''}"
            f"{' and partial messages' if CLAUDE_INCLUDE_PARTIAL_MESSAGES else ''}"
        ),
        "",
        "Codex runtime selected by Audax:",
        f"model: {CODEX_MODEL}",
        f"reasoning effort: {CODEX_REASONING_EFFORT}",
        (
            "approvals/sandbox: dangerously-bypass-approvals-and-sandbox"
            if CODEX_BYPASS_APPROVALS_AND_SANDBOX
            else "approvals/sandbox: CLI default"
        ),
        "output: JSON schema validated into a temporary output file",
    ]


def read_task(args: argparse.Namespace) -> str:
    """Resolve the mission request from positional arguments or stdin."""
    if args.task:
        return " ".join(args.task).strip()
    if supports_rich_terminal(sys.stdout):
        sys.stdout.write(render_startup_card(sys.stdout, build_startup_card_info_lines(args)))
        sys.stdout.flush()
    else:
        print("Enter the mission prompt for Audax.")
        print("Press Ctrl-D when you are done.\n")
    return sys.stdin.read().strip()


@contextmanager
def forward_termination_signals() -> None:
    """Translate process termination signals into ``KeyboardInterrupt``."""
    previous_handlers: dict[int, signal.Handlers] = {}

    def raise_keyboard_interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for signum_name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, signum_name, None)
        if signum is None:
            continue
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, raise_keyboard_interrupt)

    try:
        yield
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main(argv: list[str] | None = None) -> int:
    """Run the Audax CLI and return a process exit status."""
    args = parse_args(argv)
    try:
        with forward_termination_signals():
            task = read_task(args)
            if not task:
                print("No mission request provided.", file=sys.stderr)
                return 1
            if args.spec_rounds <= 0 or args.implementation_rounds <= 0:
                print("Round counts must be positive integers.", file=sys.stderr)
                return 1
            if args.subprocess_timeout_seconds < 0:
                print("Subprocess timeout must be zero or a positive number.", file=sys.stderr)
                return 1

            repo_root = Path.cwd()
            workspace_dir = resolve_workspace_dir(repo_root, args.workspace_dir)

            ensure_cli_available(args.claude_cmd)
            ensure_cli_available(args.codex_cmd)

            config = LoopConfig(
                repo_root=repo_root,
                workspace_dir=workspace_dir,
                max_spec_rounds=args.spec_rounds,
                max_implementation_rounds=args.implementation_rounds,
                require_mission_approval=args.require_approval,
                heartbeat_seconds=args.heartbeat_seconds,
                subprocess_timeout_seconds=(
                    None if args.subprocess_timeout_seconds == 0 else args.subprocess_timeout_seconds
                ),
                claude_cmd=args.claude_cmd,
                codex_cmd=args.codex_cmd,
            )
            artifacts = MissionArtifacts.from_workspace(workspace_dir)
            process_runner = QuietProcessRunner(
                heartbeat_seconds=args.heartbeat_seconds,
                subprocess_timeout_seconds=config.subprocess_timeout_seconds,
            )
            orchestrator = ReviewLoopOrchestrator(
                config=config,
                artifacts=artifacts,
                claude=ClaudeCLI(args.claude_cmd, process_runner, repo_root),
                codex=CodexCLI(args.codex_cmd, process_runner, repo_root),
            )
            result = orchestrator.run(task)
            print(
                f"\nMission complete. Session: {result.session_dir}\n"
                f"Locked spec: {result.mission_spec_pdf}\n"
                f"Run report: {result.report_path}"
            )
            return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
