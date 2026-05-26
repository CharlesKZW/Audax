"""CLI entrypoints for launching the Audax review loop."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import shlex
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
    DEFAULT_MISSION_MODE,
    MISSION_MODE_CHOICES,
    MISSION_MODE_DIRECT,
    MISSION_MODE_SPEC,
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
    render_info_card,
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
    parser.add_argument(
        "--mode",
        choices=MISSION_MODE_CHOICES,
        default=DEFAULT_MISSION_MODE,
        help=(
            "Execution mode. "
            "`direct-instruction` skips spec drafting and locks the original prompt directly; "
            "`mission-spec` drafts and locks a reviewed mission spec."
        ),
    )
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


class StartupExitRequested(Exception):
    """Raised when the user exits from the interactive startup prompt."""


def build_startup_card_info_lines(
    args: argparse.Namespace,
    *,
    repo_root: Path | None = None,
    interactive: bool = False,
) -> list[str]:
    """Build the compact startup-card summary shown before stdin task entry."""
    repo_root = repo_root or Path.cwd()
    submit_hint = (
        "Press **Enter** to submit · **Option+Enter** inserts a new line."
        if interactive
        else "Press **Ctrl-D** when you are done."
    )
    if interactive:
        submit_hint += " Type **/agents**, **/models**, or **/flags** for details."

    return [
        "Enter the **mission prompt** for Audax.",
        submit_hint,
        f"Target repository: `{repo_root}`",
    ]


def build_startup_flag_info_lines(
    args: argparse.Namespace,
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Build the session flag details shown by the interactive /flags command."""
    color = os.environ.get("NO_COLOR") is None
    repo_root = repo_root or Path.cwd()
    mission_mode = getattr(args, "mode", DEFAULT_MISSION_MODE)
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

    spec_rounds_value = (
        "skipped in direct-instruction mode"
        if mission_mode == MISSION_MODE_DIRECT
        else str(getattr(args, "spec_rounds", DEFAULT_SPEC_ROUNDS))
    )
    approval_value = (
        style_disabled("n/a in direct-instruction mode", color=color)
        if mission_mode == MISSION_MODE_DIRECT
        else toggle(getattr(args, "require_approval", True))
    )

    return [
        style_section_header("Session Flags", color=color),
        f"  **--mode**: {mission_mode}",
        f"  **--spec-rounds**: {spec_rounds_value}",
        (
            "  **--implementation-rounds**: "
            f"{getattr(args, 'implementation_rounds', DEFAULT_IMPLEMENTATION_ROUNDS)}"
        ),
        f"  **--workspace-dir**: `{workspace_dir}`",
        (
            "  **--require-approval/--no-require-approval**: "
            f"{approval_value}"
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
    ]


def build_startup_agent_info_lines(args: argparse.Namespace) -> list[str]:
    """Build Claude/Codex runtime details shown by /agents and /models."""
    color = os.environ.get("NO_COLOR") is None
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
        style_section_header("Claude Runtime", color=color),
        f"  **command**: `{getattr(args, 'claude_cmd', CLAUDE_CMD)}`",
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
        f"  **command**: `{getattr(args, 'codex_cmd', CODEX_CMD)}`",
        f"  **model**: {CODEX_MODEL}",
        f"  **reasoning effort**: {CODEX_REASONING_EFFORT}",
        f"  **approvals/sandbox**: {codex_sandbox}",
        "  **output**: JSON schema validated into a temporary output file",
    ]


def build_startup_slash_command_completions() -> dict[str, str]:
    """Return startup slash commands and short dropdown descriptions."""
    return {
        "/help": "show startup commands",
        "/agents": "show Claude and Codex runtime details",
        "/models": "alias for /agents",
        "/flags": "show current session flags and paths",
        "/mode": "set or toggle direct-instruction vs mission-spec",
        "/spec-rounds": "set mission-spec drafting rounds",
        "/spec": "alias for /spec-rounds",
        "/implementation-rounds": "set implementation/review rounds",
        "/impl-rounds": "alias for /implementation-rounds",
        "/input-rounds": "alias for /implementation-rounds",
        "/require-approval": "toggle or set mission approval",
        "/approval": "alias for /require-approval",
        "/auto-commit": "toggle or set auto-commit",
        "/commit": "alias for /auto-commit",
        "/session-branch": "toggle or set session branch creation",
        "/branch": "alias for /session-branch",
        "/exit": "close Audax without starting a mission",
        "/quit": "alias for /exit",
    }


def build_startup_slash_command_handler(
    args: argparse.Namespace,
    *,
    repo_root: Path | None = None,
    stream: object | None = None,
):
    """Build the interactive slash-command handler used before mission entry."""
    output_stream = stream if hasattr(stream, "write") else sys.stdout
    repo_root = repo_root or Path.cwd()

    def render_flags(message: str = "") -> str:
        lines = build_startup_flag_info_lines(args, repo_root=repo_root)
        if message:
            lines = [message, "", *lines]
        return render_info_card(
            output_stream,
            title="AUDAX FLAGS",
            info_lines=lines,
        )

    def render_agents() -> str:
        return render_info_card(
            output_stream,
            title="AUDAX AGENTS",
            info_lines=build_startup_agent_info_lines(args),
        )

    def render_help() -> str:
        return render_info_card(
            output_stream,
            title="AUDAX COMMANDS",
            info_lines=[
                "Type a command and press **Enter**.",
                "  **/agents** or **/models**: show Claude and Codex runtime details.",
                "  **/flags**: show current session flags and paths.",
                "  **/mode [direct|mission-spec]**: set or toggle mission mode.",
                "  **/spec-rounds <n>**: set mission-spec drafting rounds.",
                "  **/implementation-rounds <n>**: set implementation/review rounds.",
                "  **/require-approval [on|off]**: set or toggle mission approval.",
                "  **/auto-commit [on|off]**: set or toggle auto-commit.",
                "  **/session-branch [on|off]**: set or toggle session branch creation.",
                "  **/exit** or **/quit**: close Audax without starting a mission.",
            ],
        )

    def handler(raw_command: str) -> str:
        try:
            tokens = shlex.split(raw_command)
        except ValueError as exc:
            return _render_startup_command_error(output_stream, f"Could not parse command: {exc}")
        if not tokens:
            return render_help()

        command = tokens[0].lower()
        values = tokens[1:]

        if command in {"/exit", "/quit"}:
            raise StartupExitRequested
        if command in {"/", "/help"}:
            return render_help()
        if command in {"/agents", "/models"}:
            return render_agents()
        if command == "/flags":
            return render_flags()
        if command == "/mode":
            return _handle_mode_command(args, values, render_flags, output_stream)
        if command in {"/spec-rounds", "/spec_rounds", "/spec"}:
            value = _extract_round_value(command, values, expected_word="rounds")
            return _handle_positive_int_command(
                args,
                attr="spec_rounds",
                flag_name="--spec-rounds",
                value=value,
                render_flags=render_flags,
                output_stream=output_stream,
            )
        if command in {
            "/implementation-rounds",
            "/implementation_rounds",
            "/impl-rounds",
            "/impl_rounds",
            "/implementation",
            "/input-rounds",
            "/input_rounds",
            "/input",
        }:
            value = _extract_round_value(command, values, expected_word="rounds")
            return _handle_positive_int_command(
                args,
                attr="implementation_rounds",
                flag_name="--implementation-rounds",
                value=value,
                render_flags=render_flags,
                output_stream=output_stream,
            )
        if command in {
            "/require-approval",
            "/require_approval",
            "/approval",
            "/require",
        }:
            bool_values = _extract_bool_values(command, values, expected_word="approval")
            return _handle_bool_command(
                args,
                attr="require_approval",
                flag_name="--require-approval",
                values=bool_values,
                render_flags=render_flags,
                output_stream=output_stream,
            )
        if command in {
            "/auto-commit",
            "/auto_commit",
            "/commit",
            "/auto",
        }:
            bool_values = _extract_bool_values(command, values, expected_word="commit")
            return _handle_bool_command(
                args,
                attr="auto_commit",
                flag_name="--auto-commit",
                values=bool_values,
                render_flags=render_flags,
                output_stream=output_stream,
            )
        if command in {
            "/session-branch",
            "/session_branch",
            "/branch",
            "/session",
        }:
            bool_values = _extract_bool_values(command, values, expected_word="branch")
            return _handle_bool_command(
                args,
                attr="session_branch",
                flag_name="--session-branch",
                values=bool_values,
                render_flags=render_flags,
                output_stream=output_stream,
            )

        return _render_startup_command_error(
            output_stream,
            f"Unknown Audax command: {command}",
            hint="Type /help to list available commands.",
        )

    return handler


def _extract_round_value(command: str, values: list[str], *, expected_word: str) -> str | None:
    """Support both /spec-rounds 4 and /spec rounds 4 spellings."""
    if command in {"/spec", "/implementation", "/input"} and values[:1] == [expected_word]:
        values = values[1:]
    return values[0] if values else None


def _extract_bool_values(command: str, values: list[str], *, expected_word: str) -> list[str]:
    """Support both /auto-commit off and /auto commit off spellings."""
    if command in {"/require", "/auto", "/session"} and values[:1] == [expected_word]:
        return values[1:]
    return values


def _handle_mode_command(
    args: argparse.Namespace,
    values: list[str],
    render_flags,
    output_stream: object,
) -> str:
    if not values:
        current = getattr(args, "mode", DEFAULT_MISSION_MODE)
        next_mode = (
            MISSION_MODE_SPEC
            if current == MISSION_MODE_DIRECT
            else MISSION_MODE_DIRECT
        )
    else:
        requested = values[0].strip().lower()
        aliases = {
            "direct": MISSION_MODE_DIRECT,
            "direct-instruction": MISSION_MODE_DIRECT,
            "instruction": MISSION_MODE_DIRECT,
            "mission": MISSION_MODE_SPEC,
            "mission-spec": MISSION_MODE_SPEC,
            "spec": MISSION_MODE_SPEC,
        }
        next_mode = aliases.get(requested)
        if next_mode is None:
            return _render_startup_command_error(
                output_stream,
                f"Unsupported mode: {values[0]}",
                hint="Use /mode direct or /mode mission-spec.",
            )
    setattr(args, "mode", next_mode)
    return render_flags(f"Updated --mode to {next_mode}.")


def _handle_positive_int_command(
    args: argparse.Namespace,
    *,
    attr: str,
    flag_name: str,
    value: str | None,
    render_flags,
    output_stream: object,
) -> str:
    if value is None:
        return _render_startup_command_error(
            output_stream,
            f"Missing value for {flag_name}.",
            hint=f"Use /{flag_name.removeprefix('--')} 5.",
        )
    try:
        parsed = int(value)
    except ValueError:
        return _render_startup_command_error(
            output_stream,
            f"{flag_name} must be a positive integer.",
        )
    if parsed <= 0:
        return _render_startup_command_error(
            output_stream,
            f"{flag_name} must be a positive integer.",
        )
    setattr(args, attr, parsed)
    return render_flags(f"Updated {flag_name} to {parsed}.")


def _handle_bool_command(
    args: argparse.Namespace,
    *,
    attr: str,
    flag_name: str,
    values: list[str],
    render_flags,
    output_stream: object,
) -> str:
    if not values:
        next_value = not bool(getattr(args, attr, False))
    else:
        parsed = _parse_boolish(values[0])
        if parsed is None:
            return _render_startup_command_error(
                output_stream,
                f"Unsupported value for {flag_name}: {values[0]}",
                hint=f"Use /{flag_name.removeprefix('--')} on or /{flag_name.removeprefix('--')} off.",
            )
        next_value = parsed
    setattr(args, attr, next_value)
    label = "enabled" if next_value else "disabled"
    return render_flags(f"Updated {flag_name} to {label}.")


def _parse_boolish(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    return None


def _render_startup_command_error(
    stream: object,
    message: str,
    *,
    hint: str = "",
) -> str:
    lines = [message]
    if hint:
        lines.append(hint)
    return render_info_card(
        stream if hasattr(stream, "write") else sys.stdout,
        title="AUDAX COMMAND",
        info_lines=lines,
    )


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
        return read_task_interactive(
            slash_command_completions=build_startup_slash_command_completions(),
            slash_command_handler=build_startup_slash_command_handler(
                args,
                stream=sys.stdout,
            ),
            stream=sys.stdout,
        ).strip()
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
            if args.implementation_rounds <= 0:
                print("Round counts must be positive integers.", file=sys.stderr)
                return 1
            if args.mode == MISSION_MODE_SPEC and args.spec_rounds <= 0:
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
                mission_mode=args.mode,
                max_spec_rounds=0 if args.mode == MISSION_MODE_DIRECT else args.spec_rounds,
                max_implementation_rounds=args.implementation_rounds,
                require_mission_approval=(
                    False if args.mode == MISSION_MODE_DIRECT else args.require_approval
                ),
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
                f"{result.locked_contract_label}: {result.locked_contract_path}\n"
                f"Run report: {result.report_path}"
            )
            return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except StartupExitRequested:
        print("Audax exited.")
        return 0
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
            config_snapshot = manifest.get("config", {})
            if not isinstance(config_snapshot, dict):
                config_snapshot = {}
            mission_mode = _normalize_mission_mode(
                config_snapshot.get("mission_mode", MISSION_MODE_SPEC)
            )

            artifacts = MissionArtifacts.from_workspace(
                workspace_dir,
                session_id=session_id,
                started_at=str(manifest.get("started_at", "")) or None,
            )

            config = LoopConfig(
                repo_root=repo_root,
                workspace_dir=workspace_dir,
                mission_mode=mission_mode,
                max_spec_rounds=0 if mission_mode == MISSION_MODE_DIRECT else 1,
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
                f"{result.locked_contract_label}: {result.locked_contract_path}\n"
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


def _normalize_mission_mode(raw_mode: object) -> str:
    """Return a supported mission mode, defaulting invalid values safely."""
    mode = str(raw_mode or "").strip()
    if mode in MISSION_MODE_CHOICES:
        return mode
    return MISSION_MODE_SPEC


def _load_locked_spec(artifacts: MissionArtifacts) -> LockedMissionSpec:
    return load_locked_mission_spec(artifacts)
