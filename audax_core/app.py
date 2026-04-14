"""CLI entrypoints for launching the Audax review loop."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import signal
import shutil
import sys

from .backends import ClaudeCLI, CodexCLI
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
    parser.add_argument("--require-approval", action="store_true")
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


def read_task(args: argparse.Namespace) -> str:
    """Resolve the mission request from positional arguments or stdin."""
    if args.task:
        return " ".join(args.task).strip()
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
            workspace_dir = Path(args.workspace_dir)
            if not workspace_dir.is_absolute():
                workspace_dir = repo_root / workspace_dir

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
