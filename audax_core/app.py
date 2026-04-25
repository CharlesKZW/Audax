"""CLI entrypoints for launching the Audax review loop."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import shutil
import sys

from .artifacts import load_locked_mission_spec
from .auto_commit import AutoCommitter
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
    LockedMissionSpec,
    LoopConfig,
    MissionArtifacts,
    find_continuable_sessions,
    find_resumable_sessions,
    load_session_manifest,
)
from .orchestrator import ReviewLoopOrchestrator
from .progress import QuietProcessRunner
from .ui import (
    read_task_interactive,
    render_startup_card,
    style_disabled,
    style_enabled,
    style_section_header,
    style_warning,
    supports_rich_terminal,
)


def ensure_cli_available(cmd: str) -> None:
    """Raise an error when a required external CLI is not available."""
    if shutil.which(cmd):
        return
    raise RuntimeError(f"Required command not found in PATH: {cmd}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for a fresh Audax mission."""
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
        default=None,
        help=(
            "Kill agent CLI subprocesses after this many seconds. "
            "Unset by default (no timeout). Use 0 to explicitly disable."
        ),
    )
    parser.add_argument("--claude-cmd", default=CLAUDE_CMD)
    parser.add_argument("--codex-cmd", default=CODEX_CMD)
    parser.add_argument(
        "--auto-commit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Commit repository changes after each implementation round. "
            "Enabled by default; skipped silently when the repo is not a "
            "git repository."
        ),
    )
    parser.add_argument(
        "--session-branch",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Check out a dedicated ``audax/<session_id>`` branch at session "
            "start and commit rounds onto it. Off by default; auto-commit "
            "lands on the current branch."
        ),
    )
    return parser.parse_args(argv)


def parse_continue_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the ``audax continue`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="audax continue",
        description=(
            "Continue an interrupted Audax session from its locked mission "
            "spec or current mission_spec.md draft. With no session id, "
            "continues the most recent incomplete session in the workspace "
            "that still has a usable mission spec."
        ),
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        default=None,
        help=(
            "Session directory name under ``audax_artifacts/sessions/`` "
            "(e.g. 20260413T181500Z_pid42). Defaults to the most recent "
            "incomplete session."
        ),
    )
    parser.add_argument("--implementation-rounds", type=int, default=DEFAULT_IMPLEMENTATION_ROUNDS)
    parser.add_argument("--workspace-dir", default=DEFAULT_WORKSPACE_DIR)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument(
        "--subprocess-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Kill agent CLI subprocesses after this many seconds. "
            "Unset by default (no timeout). Use 0 to explicitly disable."
        ),
    )
    parser.add_argument("--claude-cmd", default=CLAUDE_CMD)
    parser.add_argument("--codex-cmd", default=CODEX_CMD)
    parser.add_argument(
        "--auto-commit",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--session-branch",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
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
    interactive: bool = False,
) -> list[str]:
    """Build the rich startup-card summary shown before stdin task entry."""
    color = os.environ.get("NO_COLOR") is None
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

    def toggle(flag: bool) -> str:
        return style_enabled(color=color) if flag else style_disabled(color=color)

    submit_hint = (
        "Press **Enter** to submit · **Alt+Enter** inserts a new line."
        if interactive
        else "Press **Ctrl-D** when you are done."
    )

    claude_permissions = (
        style_warning("dangerously-skip-permissions", color=color)
        if CLAUDE_SKIP_PERMISSIONS
        else "CLI default"
    )
    codex_sandbox = (
        style_warning("dangerously-bypass-approvals-and-sandbox", color=color)
        if CODEX_BYPASS_APPROVALS_AND_SANDBOX
        else "CLI default"
    )

    return [
        "Enter the **mission prompt** for Audax.",
        submit_hint,
        f"Target repository: `{repo_root}`",
        "",
        style_section_header("Session Flags", color=color),
        f"  **--spec-rounds**: {getattr(args, 'spec_rounds', DEFAULT_SPEC_ROUNDS)}",
        (
            "  **--implementation-rounds**: "
            f"{getattr(args, 'implementation_rounds', DEFAULT_IMPLEMENTATION_ROUNDS)}"
        ),
        f"  **--workspace-dir**: `{workspace_dir}`",
        (
            "  **--require-approval/--no-require-approval**: "
            f"{toggle(getattr(args, 'require_approval', True))}"
        ),
        f"  **--heartbeat-seconds**: {_format_seconds(getattr(args, 'heartbeat_seconds', DEFAULT_HEARTBEAT_SECONDS))}",
        (
            "  **--subprocess-timeout-seconds**: "
            f"{_format_seconds(None if subprocess_timeout_seconds == 0 else subprocess_timeout_seconds)}"
        ),
        f"  **--claude-cmd**: `{getattr(args, 'claude_cmd', CLAUDE_CMD)}`",
        f"  **--codex-cmd**: `{getattr(args, 'codex_cmd', CODEX_CMD)}`",
        (
            "  **--auto-commit/--no-auto-commit**: "
            f"{toggle(getattr(args, 'auto_commit', True))}"
        ),
        (
            "  **--session-branch/--no-session-branch**: "
            f"{toggle(getattr(args, 'session_branch', False))}"
        ),
        "",
        style_section_header("Claude Runtime", color=color),
        f"  **model**: {_describe_optional_setting(CLAUDE_MODEL)}",
        f"  **reasoning effort**: {_describe_optional_setting(CLAUDE_REASONING_EFFORT)}",
        f"  **permissions**: {claude_permissions}",
        (
            "  **I/O**: "
            f"{CLAUDE_INPUT_FORMAT} prompt → {CLAUDE_OUTPUT_FORMAT} output"
            f"{' with verbose logging' if CLAUDE_VERBOSE else ''}"
            f"{' and partial messages' if CLAUDE_INCLUDE_PARTIAL_MESSAGES else ''}"
        ),
        "",
        style_section_header("Codex Runtime", color=color),
        f"  **model**: {CODEX_MODEL}",
        f"  **reasoning effort**: {CODEX_REASONING_EFFORT}",
        f"  **approvals/sandbox**: {codex_sandbox}",
        "  **output**: JSON schema validated into a temporary output file",
    ]


def read_task(args: argparse.Namespace) -> str:
    """Resolve the mission request from positional arguments or stdin."""
    if args.task:
        return " ".join(args.task).strip()
    rich = supports_rich_terminal(sys.stdout)
    interactive = rich and _stdin_is_tty()
    if rich:
        sys.stdout.write(
            render_startup_card(
                sys.stdout,
                build_startup_card_info_lines(args, interactive=interactive),
            )
        )
        sys.stdout.flush()
    else:
        print("Enter the mission prompt for Audax.")
        print("Press Ctrl-D when you are done.\n")
    if interactive:
        return read_task_interactive().strip()
    return sys.stdin.read().strip()


def _stdin_is_tty() -> bool:
    """Return whether stdin is attached to a TTY."""
    isatty = getattr(sys.stdin, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


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
    """Run the Audax CLI and dispatch subcommands."""
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "continue":
        return continue_main(raw[1:])
    return run_main(raw)


def run_main(argv: list[str]) -> int:
    """Launch a fresh Audax mission."""
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
            if args.subprocess_timeout_seconds is not None and args.subprocess_timeout_seconds < 0:
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
            auto_committer = AutoCommitter(
                repo_root=repo_root,
                enabled=args.auto_commit,
                use_session_branch=args.session_branch,
            )
            orchestrator = _build_orchestrator(
                config=config,
                artifacts=artifacts,
                repo_root=repo_root,
                auto_committer=auto_committer,
            )
            result = orchestrator.run(task)
            print(
                f"\nMission complete. Session: {result.session_dir}\n"
                f"Locked spec: {result.mission_spec_md}\n"
                f"Run report: {result.report_path}"
            )
            return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def continue_main(argv: list[str]) -> int:
    """Continue an existing Audax session from its current mission spec."""
    args = parse_continue_args(argv)
    try:
        with forward_termination_signals():
            if args.implementation_rounds <= 0:
                print("Round counts must be positive integers.", file=sys.stderr)
                return 1
            if args.subprocess_timeout_seconds is not None and args.subprocess_timeout_seconds < 0:
                print("Subprocess timeout must be zero or a positive number.", file=sys.stderr)
                return 1

            repo_root = Path.cwd()
            workspace_dir = resolve_workspace_dir(repo_root, args.workspace_dir)

            session_id = args.session_id or _pick_latest_continuable_session_id(workspace_dir)
            manifest = load_session_manifest(workspace_dir, session_id)
            task = str(manifest.get("task", "")).strip()
            if not task:
                print(
                    f"Session {session_id} has no task recorded in session_manifest.json.",
                    file=sys.stderr,
                )
                return 1
            if manifest.get("status") == "succeeded":
                print(
                    f"Session {session_id} already succeeded; nothing to resume.",
                    file=sys.stderr,
                )
                return 1

            ensure_cli_available(args.claude_cmd)
            ensure_cli_available(args.codex_cmd)

            artifacts = MissionArtifacts.from_workspace(
                workspace_dir,
                session_id=session_id,
                started_at=str(manifest.get("started_at", "")) or None,
            )

            config = LoopConfig(
                repo_root=repo_root,
                workspace_dir=workspace_dir,
                max_spec_rounds=1,
                max_implementation_rounds=args.implementation_rounds,
                require_mission_approval=False,
                heartbeat_seconds=args.heartbeat_seconds,
                subprocess_timeout_seconds=(
                    None if args.subprocess_timeout_seconds == 0 else args.subprocess_timeout_seconds
                ),
                claude_cmd=args.claude_cmd,
                codex_cmd=args.codex_cmd,
            )
            auto_committer = AutoCommitter(
                repo_root=repo_root,
                enabled=args.auto_commit,
                use_session_branch=args.session_branch,
            )
            orchestrator = _build_orchestrator(
                config=config,
                artifacts=artifacts,
                repo_root=repo_root,
                auto_committer=auto_committer,
            )
            print(f"Continuing session {session_id} with task: {task}")
            result = orchestrator.continue_session(task)
            print(
                f"\nResume complete. Session: {result.session_dir}\n"
                f"Locked spec: {result.mission_spec_md}\n"
                f"Run report: {result.report_path}"
            )
            return 0 if result.success else 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _build_orchestrator(
    *,
    config: LoopConfig,
    artifacts: MissionArtifacts,
    repo_root: Path,
    auto_committer: AutoCommitter | None = None,
) -> ReviewLoopOrchestrator:
    process_runner = QuietProcessRunner(
        heartbeat_seconds=config.heartbeat_seconds,
        subprocess_timeout_seconds=config.subprocess_timeout_seconds,
    )
    return ReviewLoopOrchestrator(
        config=config,
        artifacts=artifacts,
        claude=ClaudeCLI(config.claude_cmd, process_runner, repo_root),
        codex=CodexCLI(config.codex_cmd, process_runner, repo_root),
        auto_committer=auto_committer,
    )


def _pick_latest_resumable_session_id(workspace_dir: Path) -> str:
    candidates = find_resumable_sessions(workspace_dir)
    if not candidates:
        raise RuntimeError(
            f"No resumable sessions found under {workspace_dir / 'sessions'}"
        )
    return candidates[0][0]


def _pick_latest_continuable_session_id(workspace_dir: Path) -> str:
    candidates = find_continuable_sessions(workspace_dir)
    if not candidates:
        raise RuntimeError(
            f"No resumable sessions found under {workspace_dir / 'sessions'}"
        )
    return candidates[0][0]


def _load_locked_spec(artifacts: MissionArtifacts) -> LockedMissionSpec:
    return load_locked_mission_spec(artifacts)
