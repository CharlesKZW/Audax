"""Terminal UI helpers for Audax launch, session headers, and round reports."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import sys
import textwrap
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO
import unicodedata

from .models import (
    ImplementationReview,
    LoopConfig,
    MISSION_MODE_DIRECT,
    MissionReview,
)

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
CARD_MIN_WIDTH = 84
CARD_MAX_WIDTH = 116

SEVERITY_ANSI = {
    "critical": "1;38;5;196",  # bright red, bold
    "high": "1;38;5;202",       # bright orange, bold
    "medium": "1;38;5;214",     # amber
    "low": "38;5;117",          # cyan
    "info": "38;5;244",         # gray
}
HEADING_ANSI = "1;38;5;117"
LABEL_ANSI = "1;38;5;252"
GOOD_ANSI = "1;38;5;82"
BAD_ANSI = "1;38;5;203"
MUTED_ANSI = "38;5;244"
BAR_FILLED_ANSI = "38;5;82"
BAR_EMPTY_ANSI = "38;5;238"
PROGRESS_BAR_WIDTH = 30
ISSUE_DETAIL_MAX_LINES = 3
SLASH_MENU_MAX_ROWS = 12
LIVE_LOG_RULE_ANSI = "38;5;60"
LIVE_LOG_TITLE_ANSI = "1;38;5;208"
LIVE_LOG_MUTED_ANSI = "38;5;244"
LIVE_LOG_ASSISTANT_LABEL_ANSI = "1;38;5;208"
LIVE_LOG_ASSISTANT_TEXT_ANSI = "38;5;252"
LIVE_LOG_INLINE_CODE_ANSI = "38;5;230;48;5;238"
LIVE_LOG_TOOL_LABEL_ANSI = "1;38;5;16;48;5;117"
LIVE_LOG_TOOL_DETAIL_ANSI = "38;5;250"

_SECTION_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_LEADING_NUMBER_PATTERN = re.compile(r"^\s*\d+[.)]?\s+")

_INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
_INLINE_BOLD_ASTERISK_PATTERN = re.compile(r"\*\*([^*\n]+?)\*\*")
_INLINE_BOLD_UNDERSCORE_PATTERN = re.compile(r"__([^_\n]+?)__")
_INLINE_MARKDOWN_PATTERN = re.compile(
    r"`([^`\n]+)`|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__"
)
INLINE_CODE_ANSI = "38;5;213"
INLINE_BOLD_ANSI = "1"
INPUT_BOX_BG = "#232a31"
INPUT_BOX_FG = "#f4f4f5"
INPUT_BOX_PROMPT_FG = "#e5e7eb"
INPUT_BOX_BORDER_FG = "#5c6773"
_FORCE_RICH_ENV_VARS = ("AUDAX_FORCE_TTY", "AUDAX_FORCE_RICH_TERMINAL")


def _strip_leading_number(item: str) -> str:
    """Remove a leading ``N.`` / ``N)`` prefix so we can renumber cleanly."""
    return _LEADING_NUMBER_PATTERN.sub("", item, count=1).strip()


def _render_inline_markdown(text: str, *, color: bool) -> str:
    """Apply ANSI styles to simple markdown inline spans.

    Supports backtick code spans and ``**bold**`` / ``__bold__``. Unmatched
    markers pass through unchanged. When ``color`` is False the markers are
    stripped so raw ``**`` / ``` ` ``` do not leak into plain output.
    """
    if not color:
        return _strip_inline_markdown(text)
    text = _INLINE_CODE_PATTERN.sub(
        lambda m: f"\x1b[{INLINE_CODE_ANSI}m{m.group(1)}\x1b[39m",
        text,
    )
    text = _INLINE_BOLD_ASTERISK_PATTERN.sub(
        lambda m: f"\x1b[{INLINE_BOLD_ANSI}m{m.group(1)}\x1b[22m",
        text,
    )
    text = _INLINE_BOLD_UNDERSCORE_PATTERN.sub(
        lambda m: f"\x1b[{INLINE_BOLD_ANSI}m{m.group(1)}\x1b[22m",
        text,
    )
    return text


def _strip_inline_markdown(text: str) -> str:
    text = _INLINE_CODE_PATTERN.sub(r"\1", text)
    text = _INLINE_BOLD_ASTERISK_PATTERN.sub(r"\1", text)
    text = _INLINE_BOLD_UNDERSCORE_PATTERN.sub(r"\1", text)
    return text


def supports_rich_terminal(stream: TextIO) -> bool:
    """Return whether a stream supports the richer card-style terminal UI."""
    if _force_rich_terminal():
        return True
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        if not isatty():
            return False
    except OSError:
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def _force_rich_terminal() -> bool:
    """Return whether rich terminal rendering was explicitly requested."""
    return any(_truthy_env(name) for name in _FORCE_RICH_ENV_VARS)


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def render_startup_card(stream: TextIO, info_lines: list[str] | None = None) -> str:
    """Render the interactive startup card shown before stdin mission entry."""
    return render_info_card(
        stream,
        title="AUDAX CONSOLE",
        info_lines=info_lines,
    )


def render_info_card(
    stream: TextIO,
    *,
    title: str,
    info_lines: list[str] | None = None,
) -> str:
    """Render a generic rich terminal information card."""
    del stream  # kept for signature compatibility with other card helpers
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    body_lines = info_lines or [
        "Enter the **mission prompt** for Audax.",
        "Press **Ctrl-D** when you are done.",
        "Audax will make changes in the current working directory.",
    ]
    return _compose_card(
        title=title,
        body_lines=body_lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def style_section_header(name: str, *, color: bool) -> str:
    """Return a bolded, decorated section header for the startup card."""
    rule = "─" * 6
    text = f"── {name} {rule}"
    return _style(text, HEADING_ANSI, color=color)


def style_enabled(label: str = "enabled", *, color: bool) -> str:
    """Return a green-styled on-state label for flag rows."""
    return _style(label, GOOD_ANSI, color=color)


def style_disabled(label: str = "disabled", *, color: bool) -> str:
    """Return a muted off-state label for flag rows."""
    return _style(label, MUTED_ANSI, color=color)


def style_warning(label: str, *, color: bool) -> str:
    """Return an amber-styled label used for danger-mode runtime values."""
    return _style(label, "1;38;5;208", color=color)


def style_approval_mode(required: bool, *, color: bool) -> str:
    """Return a styled mission-approval mode label."""
    if required:
        return _style("required", GOOD_ANSI, color=color)
    return _style("auto", "1;38;5;208", color=color)


def read_task_interactive(
    *,
    slash_commands: Mapping[str, str] | None = None,
    slash_command_completions: Mapping[str, str] | None = None,
    slash_command_options: Mapping[str, Sequence[str]] | None = None,
    slash_command_handler: Callable[[str], str | None] | None = None,
    stream: TextIO | None = None,
) -> str:
    """Read a mission prompt via a Codex-style framed input box.

    A rounded gray frame surrounds the input area; the inner background is a
    shaded gray that fills every visible line so the user clearly perceives an
    input box. The ``>`` prompt and input text use neutral foreground colors.
    Slash commands show a completion dropdown while the command token is being
    typed. Enter submits; Option+Enter inserts a newline for multi-line prompts.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.processors import BeforeInput
    from prompt_toolkit.lexers import SimpleLexer
    from prompt_toolkit.styles import Style

    bindings = KeyBindings()
    completion_map = slash_command_completions or slash_commands or {}
    normalized_completions = _normalize_slash_commands(completion_map)
    normalized_options = _normalize_slash_command_options(slash_command_options or {})
    slash_selected_index = [0]
    slash_scroll_offset = [0]
    slash_option_indexes: dict[str, int] = {}
    slash_last_token: list[str | None] = [None]

    def current_slash_suggestions() -> list[tuple[str, str]]:
        before_cursor = buffer.document.current_line_before_cursor
        stripped = before_cursor.lstrip()
        token = stripped.split(maxsplit=1)[0] if stripped.startswith("/") else None
        suggestions = slash_command_suggestions(before_cursor, normalized_completions)
        if token != slash_last_token[0]:
            slash_selected_index[0] = 0
            slash_scroll_offset[0] = 0
            slash_last_token[0] = token
        _clamp_slash_menu_state(
            suggestions,
            slash_selected_index,
            slash_scroll_offset,
        )
        return suggestions

    def complete_selected_slash_command(event, *, submit_option: bool = False) -> bool:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return False
        command = suggestions[slash_selected_index[0]][0]
        replacement = _slash_completion_text(
            command,
            option_indexes=slash_option_indexes,
            command_options=normalized_options,
        )
        if submit_option and command in normalized_options:
            event.app.exit(result=replacement)
            return True
        before_cursor = event.current_buffer.document.current_line_before_cursor
        token = before_cursor.lstrip().split(maxsplit=1)[0]
        event.current_buffer.delete_before_cursor(len(token))
        event.current_buffer.insert_text(replacement)
        slash_selected_index[0] = 0
        slash_scroll_offset[0] = 0
        slash_last_token[0] = replacement
        return True

    def current_selected_slash_options() -> tuple[str, ...]:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return ()
        command = suggestions[slash_selected_index[0]][0]
        return normalized_options.get(command, ())

    @bindings.add("enter")
    def _submit(event) -> None:
        if complete_selected_slash_command(event, submit_option=True):
            return
        event.app.exit(result=event.current_buffer.text)

    @bindings.add("tab")
    def _complete_slash_command(event) -> None:
        if not complete_selected_slash_command(event):
            event.current_buffer.insert_text("\t")

    @bindings.add("down", filter=Condition(lambda: bool(current_slash_suggestions())))
    def _select_next_slash_command(event) -> None:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return
        slash_selected_index[0] = min(
            len(suggestions) - 1,
            slash_selected_index[0] + 1,
        )
        _clamp_slash_menu_state(
            suggestions,
            slash_selected_index,
            slash_scroll_offset,
        )
        event.app.invalidate()

    @bindings.add("up", filter=Condition(lambda: bool(current_slash_suggestions())))
    def _select_previous_slash_command(event) -> None:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return
        slash_selected_index[0] = max(0, slash_selected_index[0] - 1)
        _clamp_slash_menu_state(
            suggestions,
            slash_selected_index,
            slash_scroll_offset,
        )
        event.app.invalidate()

    @bindings.add("right", filter=Condition(lambda: bool(current_selected_slash_options())))
    def _select_next_slash_option(event) -> None:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return
        command = suggestions[slash_selected_index[0]][0]
        _advance_slash_option(
            command,
            option_indexes=slash_option_indexes,
            command_options=normalized_options,
            delta=1,
        )
        event.app.invalidate()

    @bindings.add("left", filter=Condition(lambda: bool(current_selected_slash_options())))
    def _select_previous_slash_option(event) -> None:
        suggestions = current_slash_suggestions()
        if not suggestions:
            return
        command = suggestions[slash_selected_index[0]][0]
        _advance_slash_option(
            command,
            option_indexes=slash_option_indexes,
            command_options=normalized_options,
            delta=-1,
        )
        event.app.invalidate()

    @bindings.add("escape", "enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-d")
    def _eof(event) -> None:
        event.app.exit(result="")

    @bindings.add("c-c")
    def _interrupt(event) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    buffer = Buffer(multiline=True)

    input_window = Window(
        content=BufferControl(
            buffer=buffer,
            input_processors=[
                BeforeInput(input_box_prompt_prefix(), style="class:input-prompt")
            ],
            lexer=SimpleLexer(style="class:input-text"),
        ),
        wrap_lines=True,
        style="class:input-window",
        height=Dimension(min=1),
        dont_extend_height=True,
    )

    frame = HSplit(
        [
            _build_rounded_input_frame(input_window),
            ConditionalContainer(
                content=Window(
                    content=FormattedTextControl(
                        lambda: _render_slash_menu_fragments(
                            current_slash_suggestions(),
                            selected_index=slash_selected_index[0],
                            scroll_offset=slash_scroll_offset[0],
                            command_options=normalized_options,
                            option_indexes=slash_option_indexes,
                        )
                    ),
                    height=Dimension(min=1, max=SLASH_MENU_MAX_ROWS),
                    style="class:slash-menu",
                    dont_extend_height=True,
                ),
                filter=Condition(lambda: bool(current_slash_suggestions())),
            ),
        ]
    )

    style = Style.from_dict(build_input_box_style_map())

    app = Application(
        layout=Layout(frame, focused_element=input_window),
        key_bindings=bindings,
        style=style,
        full_screen=False,
        mouse_support=False,
        erase_when_done=False,
    )

    output_stream = stream or sys.stdout
    commands = {key.lower(): value for key, value in (slash_commands or {}).items()}

    while True:
        try:
            result = app.run()
        except EOFError:
            return ""

        text = result or ""
        command = text.strip().lower()
        if command.startswith("/") and (slash_command_handler is not None or commands):
            if slash_command_handler is not None:
                rendered = slash_command_handler(text.strip())
            else:
                rendered = commands.get(command)
                if rendered is None:
                    rendered = _render_unknown_slash_command(command, commands)
            if rendered is None:
                rendered = ""
            output_stream.write(rendered)
            if not rendered.endswith("\n"):
                output_stream.write("\n")
            output_stream.flush()
            buffer.reset()
            continue
        return text


def _render_unknown_slash_command(command: str, commands: Mapping[str, str]) -> str:
    """Render a compact message for an unrecognized interactive slash command."""
    available = ", ".join(sorted(name for name in commands if name != "/"))
    return f"Unknown Audax command: {command}\nAvailable commands: {available}\n"


def slash_command_suggestions(
    line_before_cursor: str,
    commands: Mapping[str, str],
) -> list[tuple[str, str]]:
    """Return slash command suggestions for the current input line."""
    normalized = _normalize_slash_commands(commands)
    stripped = line_before_cursor.lstrip()
    if not stripped.startswith("/"):
        return []

    token = stripped.split(maxsplit=1)[0]
    if stripped != token:
        return []

    lowered = token.lower()
    if lowered in normalized and lowered != "/":
        return []

    return [
        (command, normalized[command])
        for command in sorted(normalized)
        if command.startswith(lowered)
    ]


def build_slash_command_completer(commands: Mapping[str, str]):
    """Build a prompt-toolkit completer for startup slash commands."""
    from prompt_toolkit.completion import Completer, Completion

    normalized = _normalize_slash_commands(commands)

    class SlashCommandCompleter(Completer):
        def get_completions(self, document, complete_event):
            del complete_event
            before_cursor = document.current_line_before_cursor.lstrip()
            token = before_cursor.split(maxsplit=1)[0] if before_cursor else ""
            for command, description in slash_command_suggestions(
                document.current_line_before_cursor,
                normalized,
            ):
                yield Completion(
                    command,
                    start_position=-len(token),
                    display=command,
                    display_meta=description,
                )

    return SlashCommandCompleter()


def _normalize_slash_commands(commands: Mapping[str, str]) -> dict[str, str]:
    return {
        command.lower(): description
        for command, description in commands.items()
        if command.startswith("/")
    }


def _normalize_slash_command_options(
    command_options: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    return {
        command.lower(): tuple(str(option) for option in options if str(option).strip())
        for command, options in command_options.items()
        if command.startswith("/") and options
    }


def _slash_completion_text(
    command: str,
    *,
    option_indexes: Mapping[str, int],
    command_options: Mapping[str, tuple[str, ...]],
) -> str:
    options = command_options.get(command, ())
    if not options:
        return command
    idx = max(0, min(option_indexes.get(command, 0), len(options) - 1))
    return f"{command} {options[idx]}"


def _advance_slash_option(
    command: str,
    *,
    option_indexes: dict[str, int],
    command_options: Mapping[str, tuple[str, ...]],
    delta: int,
) -> None:
    options = command_options.get(command, ())
    if not options:
        return
    current = option_indexes.get(command, 0)
    option_indexes[command] = (current + delta) % len(options)


def _clamp_slash_menu_state(
    suggestions: list[tuple[str, str]],
    selected_index: list[int],
    scroll_offset: list[int],
    *,
    max_rows: int = SLASH_MENU_MAX_ROWS,
) -> None:
    """Keep the highlighted slash-menu row visible."""
    if not suggestions:
        selected_index[0] = 0
        scroll_offset[0] = 0
        return
    selected_index[0] = max(0, min(selected_index[0], len(suggestions) - 1))
    if selected_index[0] < scroll_offset[0]:
        scroll_offset[0] = selected_index[0]
    visible_end = scroll_offset[0] + max_rows
    if selected_index[0] >= visible_end:
        scroll_offset[0] = selected_index[0] - max_rows + 1
    scroll_offset[0] = max(0, min(scroll_offset[0], max(0, len(suggestions) - max_rows)))


def _render_slash_menu_fragments(
    suggestions: list[tuple[str, str]],
    *,
    selected_index: int = 0,
    scroll_offset: int = 0,
    max_rows: int = SLASH_MENU_MAX_ROWS,
    command_options: Mapping[str, tuple[str, ...]] | None = None,
    option_indexes: Mapping[str, int] | None = None,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    command_options = command_options or {}
    option_indexes = option_indexes or {}
    visible = suggestions[scroll_offset : scroll_offset + max_rows]
    for visible_idx, (command, description) in enumerate(visible):
        idx = scroll_offset + visible_idx
        is_current = idx == selected_index
        command_style = (
            "class:slash-menu.command.current"
            if is_current
            else "class:slash-menu.command"
        )
        meta_style = (
            "class:slash-menu.meta.current"
            if is_current
            else "class:slash-menu.meta"
        )
        row_style = "class:slash-menu.current" if is_current else "class:slash-menu"
        marker = ">" if is_current else " "
        rows.extend(
            [
                (row_style, f" {marker} "),
                (command_style, f"{command:<26}"),
                (meta_style, description),
            ]
        )
        rows.extend(
            _render_slash_option_fragments(
                command,
                command_options=command_options,
                option_indexes=option_indexes,
                current=is_current,
            )
        )
        rows.append((row_style, "\n"))
    remaining = len(suggestions) - (scroll_offset + len(visible))
    if remaining > 0:
        rows.extend(
            [
                ("class:slash-menu", "  "),
                ("class:slash-menu.meta", f"+ {remaining} more"),
            ]
        )
    elif rows:
        rows.pop()
    return rows


def _render_slash_option_fragments(
    command: str,
    *,
    command_options: Mapping[str, tuple[str, ...]],
    option_indexes: Mapping[str, int],
    current: bool,
) -> list[tuple[str, str]]:
    options = command_options.get(command, ())
    if not options:
        return []
    selected_idx = max(0, min(option_indexes.get(command, 0), len(options) - 1))
    fragments: list[tuple[str, str]] = [("class:slash-menu.meta.current" if current else "class:slash-menu.meta", "  ")]
    for idx, option in enumerate(options):
        selected = idx == selected_idx
        style = (
            "class:slash-menu.option.selected.current"
            if current and selected
            else "class:slash-menu.option.selected"
            if selected
            else "class:slash-menu.option.current"
            if current
            else "class:slash-menu.option"
        )
        if idx > 0:
            fragments.append(("class:slash-menu.meta.current" if current else "class:slash-menu.meta", " "))
        left = "<" if current and selected else " "
        right = ">" if current and selected else " "
        fragments.append((style, f"{left}{option}{right}"))
    return fragments


def _build_rounded_input_frame(body):
    """Wrap ``body`` in a Codex-style rounded border using box-drawing chars.

    The default ``prompt_toolkit.widgets.Frame`` only ships with sharp corners;
    Codex CLI's input box uses rounded corners (``╭ ╮ ╰ ╯``) so we build the
    border manually out of single-character ``Window`` cells.
    """
    from functools import partial
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window

    fill = partial(Window, style="class:input-frame.border")
    return HSplit(
        [
            VSplit(
                [
                    fill(width=1, height=1, char="╭"),
                    fill(char="─"),
                    fill(width=1, height=1, char="╮"),
                ],
                height=1,
            ),
            VSplit(
                [
                    fill(width=1, char="│"),
                    body,
                    fill(width=1, char="│"),
                ],
            ),
            VSplit(
                [
                    fill(width=1, height=1, char="╰"),
                    fill(char="─"),
                    fill(width=1, height=1, char="╯"),
                ],
                height=1,
            ),
        ],
        style="class:input-frame",
    )


def build_input_box_style_map() -> dict[str, str]:
    """Return prompt-toolkit style map for the framed mission input box."""
    base = f"bg:{INPUT_BOX_BG} fg:{INPUT_BOX_FG}"
    border = f"fg:{INPUT_BOX_BORDER_FG}"
    return {
        "input-frame": border,
        "input-frame.border": border,
        "input-window": base,
        "input-text": base,
        "input-prompt": f"{base} fg:{INPUT_BOX_PROMPT_FG} bold",
        "slash-menu": "bg:#111827 fg:#d1d5db",
        "slash-menu.current": "bg:#374151 fg:#ffffff",
        "slash-menu.command": "bg:#111827 fg:#f4f4f5 bold",
        "slash-menu.command.current": "bg:#374151 fg:#ffffff bold",
        "slash-menu.meta": "bg:#111827 fg:#9ca3af",
        "slash-menu.meta.current": "bg:#374151 fg:#e5e7eb",
        "slash-menu.option": "bg:#111827 fg:#d1d5db",
        "slash-menu.option.current": "bg:#374151 fg:#e5e7eb",
        "slash-menu.option.selected": "bg:#1f2937 fg:#f97316 bold",
        "slash-menu.option.selected.current": "bg:#f97316 fg:#111827 bold",
    }


def input_box_prompt_prefix() -> str:
    """Return the styled prefix for the input cursor position."""
    return " > "


def render_session_header_card(task: str, config: LoopConfig, stream: TextIO) -> str:
    """Render the rich TTY header card for an Audax mission run."""
    del stream  # kept for signature symmetry with other render helpers
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    approval_mode = (
        style_disabled("n/a", color=color)
        if config.mission_mode == MISSION_MODE_DIRECT
        else style_approval_mode(config.require_mission_approval, color=color)
    )
    spec_rounds_value = (
        "skipped"
        if config.mission_mode == MISSION_MODE_DIRECT
        else str(config.max_spec_rounds)
    )
    info_lines: list[str] = [
        style_section_header("Mission Brief", color=color),
        *_wrap_detail_row("Task", task, content_width, color=color),
        *_wrap_detail_row("Repo", str(config.repo_root), content_width, color=color),
        *_wrap_detail_row("Workspace", str(config.workspace_dir), content_width, color=color),
        "",
        style_section_header("Execution Budget", color=color),
        *_wrap_detail_row(
            "Mode",
            config.mission_mode,
            content_width,
            color=color,
        ),
        *_wrap_detail_row(
            "Spec rounds max",
            spec_rounds_value,
            content_width,
            color=color,
        ),
        *_wrap_detail_row(
            "Implementation rounds max",
            str(config.max_implementation_rounds),
            content_width,
            color=color,
        ),
        *_wrap_detail_row(
            "Mission approval",
            approval_mode,
            content_width,
            color=color,
            plain_value=(
                "n/a"
                if config.mission_mode == MISSION_MODE_DIRECT
                else "required" if config.require_mission_approval else "auto"
            ),
        ),
    ]
    return _compose_card(
        title="AUDAX COLLABORATIVE MISSION LOOP",
        body_lines=info_lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def render_live_log_header(label: str, *, rich: bool) -> str:
    """Render the header for Claude's streamed work log."""
    if not rich:
        return (
            "\n"
            f"  ── Claude live work log: {label} ─────────────────────\n"
            "  Ctrl-C stops the current Audax run; use `continue` to resume from a locked mission.\n"
        )

    total_width = _card_width()
    title = f" Claude live work log: {label} "
    rule_width = max(8, total_width - _display_width(title) - 8)
    top = (
        "  "
        + _style("╭─", LIVE_LOG_RULE_ANSI, color=True)
        + _style(title, LIVE_LOG_TITLE_ANSI, color=True)
        + _style("─" * rule_width + "╮", LIVE_LOG_RULE_ANSI, color=True)
    )
    hint = (
        "  "
        + _style("│", LIVE_LOG_RULE_ANSI, color=True)
        + " "
        + _style(
            "Ctrl-C stops the current Audax run; use `continue` to resume from a locked mission.",
            LIVE_LOG_MUTED_ANSI,
            color=True,
        )
    )
    return f"\n{top}\n{hint}\n"


def render_live_log_footer(*, rich: bool) -> str:
    """Render the footer for Claude's streamed work log."""
    if not rich:
        return "\n  ── end live output ────────────────────────\n"
    total_width = _card_width()
    title = " end live output "
    rule_width = max(8, total_width - _display_width(title) - 8)
    return (
        "\n  "
        + _style("╰─", LIVE_LOG_RULE_ANSI, color=True)
        + _style(title, LIVE_LOG_MUTED_ANSI, color=True)
        + _style("─" * rule_width + "╯", LIVE_LOG_RULE_ANSI, color=True)
        + "\n"
    )


def live_log_assistant_prefix(*, rich: bool) -> str:
    """Return the left gutter for streamed assistant text."""
    if not rich:
        return "  Claude  "
    return (
        "  "
        + _style("Claude", LIVE_LOG_ASSISTANT_LABEL_ANSI, color=True)
        + "  "
    )


def style_live_log_assistant_text(text: str, *, rich: bool) -> str:
    """Style streamed assistant prose."""
    if not rich:
        return _strip_inline_markdown(text)
    return _render_live_log_inline_markdown(text)


def split_live_log_inline_markdown(text: str) -> tuple[str, str]:
    """Split ``text`` into a safe prefix and a trailing incomplete inline span."""
    idx = 0
    while idx < len(text):
        if text[idx] == "`":
            end = text.find("`", idx + 1)
            newline = text.find("\n", idx + 1)
            if end == -1 or (newline != -1 and newline < end):
                return text[:idx], text[idx:]
            idx = end + 1
            continue
        if text.startswith("**", idx):
            end = text.find("**", idx + 2)
            newline = text.find("\n", idx + 2)
            if end == -1 or (newline != -1 and newline < end):
                return text[:idx], text[idx:]
            idx = end + 2
            continue
        if text.startswith("__", idx):
            end = text.find("__", idx + 2)
            newline = text.find("\n", idx + 2)
            if end == -1 or (newline != -1 and newline < end):
                return text[:idx], text[idx:]
            idx = end + 2
            continue
        idx += 1
    return text, ""


def _render_live_log_inline_markdown(text: str) -> str:
    """Render assistant prose with live-log base, bold, and code styles."""
    rendered: list[str] = []
    last = 0
    for match in _INLINE_MARKDOWN_PATTERN.finditer(text):
        if match.start() > last:
            rendered.append(
                _style(text[last : match.start()], LIVE_LOG_ASSISTANT_TEXT_ANSI, color=True)
            )
        code = match.group(1)
        bold = match.group(2) or match.group(3)
        if code is not None:
            rendered.append(_style(code, LIVE_LOG_INLINE_CODE_ANSI, color=True))
        else:
            rendered.append(
                _style(bold, f"1;{LIVE_LOG_ASSISTANT_TEXT_ANSI}", color=True)
            )
        last = match.end()
    if last < len(text):
        rendered.append(_style(text[last:], LIVE_LOG_ASSISTANT_TEXT_ANSI, color=True))
    return "".join(rendered)


def render_live_log_tool_start(name: str, *, rich: bool) -> str:
    """Render the left gutter for a Claude tool-use line."""
    if not rich:
        return f"\n  Tool    {name}  "
    badge = _style(f" {name} ", LIVE_LOG_TOOL_LABEL_ANSI, color=True)
    return f"\n  {badge} "


def style_live_log_tool_detail(
    text: str,
    *,
    rich: bool,
    tool_name: str | None = None,
) -> str:
    """Style a one-line Claude tool-use summary."""
    if _is_shell_tool(tool_name) and text:
        text = f"$ {text}"
    if not rich:
        return text
    return _style(text, LIVE_LOG_TOOL_DETAIL_ANSI, color=True)


def _is_shell_tool(name: str | None) -> bool:
    """Return whether a Claude tool name represents shell command execution."""
    if name is None:
        return False
    return name.strip().lower() in {"bash", "shell"}


def _card_width() -> int:
    """Return the bounded width used for rich terminal cards."""
    try:
        columns = shutil.get_terminal_size().columns
    except OSError:
        columns = 96
    if columns < CARD_MIN_WIDTH:
        columns = CARD_MIN_WIDTH
    return min(columns, CARD_MAX_WIDTH)


def _wrap_lines(lines: list[str], *, width: int) -> list[str]:
    """Wrap content lines while preserving blank lines."""
    wrapped: list[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=width) or [""])
    return wrapped


def _style(text: str, code: str, *, color: bool) -> str:
    """Wrap text with an ANSI style when color output is enabled."""
    if not color:
        return text
    return f"\033[{code}m{text}\033[0m"


def _pad_ansi(text: str, width: int) -> str:
    """Pad a string containing ANSI escapes to a target display width."""
    padding = max(0, width - _display_width(text))
    return text + (" " * padding)


def _display_width(text: str) -> int:
    """Measure the visible width of a string, including East Asian wide chars."""
    clean = ANSI_PATTERN.sub("", text)
    width = 0
    for character in clean:
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Return ``{section_name_lower: [bullet, ...]}`` for markdown headings.

    Only bullet items under each heading are collected. Non-bullet lines are
    ignored. Section names preserve their original casing.
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.rstrip()
        heading_match = _SECTION_PATTERN.match(stripped)
        if heading_match is not None:
            name = heading_match.group(1).strip()
            current = sections.setdefault(name, [])
            continue
        if current is None:
            continue
        bullet_match = _BULLET_PATTERN.match(stripped)
        if bullet_match is not None:
            current.append(bullet_match.group(1).strip())
    return sections


def _extract_mission_spec_bullets(mission_spec: str) -> list[str]:
    """Return user-facing mission success bullets from the current spec format."""
    sections = parse_markdown_sections(mission_spec)
    legacy_criteria = [
        _strip_leading_number(item)
        for item in _find_section(sections, "Mission Success Criteria")
    ]
    if legacy_criteria:
        return legacy_criteria

    bullets = [
        _strip_leading_number(match.group(1).strip())
        for line in mission_spec.splitlines()
        if (match := _BULLET_PATTERN.match(line.rstrip())) is not None
    ]
    return bullets


def _wrap_detail_row(
    label: str,
    value: str,
    width: int,
    *,
    color: bool,
    plain_value: str | None = None,
    indent: str = "  ",
) -> list[str]:
    """Wrap a label/value row with aligned continuation lines."""
    plain_prefix = f"{indent}{label}: "
    styled_prefix = f"{indent}{_style(f'{label}:', LABEL_ANSI, color=color)} "
    clean_value = plain_value if plain_value is not None else ANSI_PATTERN.sub("", value)
    wrapped_value = textwrap.wrap(clean_value, width=max(1, width - len(plain_prefix))) or [""]
    lines = [f"{styled_prefix}{value if len(wrapped_value) == 1 else wrapped_value[0]}"]
    continuation = " " * len(plain_prefix)
    for extra in wrapped_value[1:]:
        lines.append(f"{continuation}{extra}")
    return lines


def render_implementer_round_box(
    *,
    round_num: int,
    implementer_backend: str,
    implementer_summary: str,
    stream: TextIO | None = None,
) -> str:
    """Render only the implementer box for the given round."""
    del stream  # kept for signature symmetry with other render helpers
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    return _implementer_box(
        round_num=round_num,
        backend=implementer_backend,
        summary_markdown=implementer_summary,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def render_reviewer_and_progress_boxes(
    *,
    round_num: int,
    reviewer_backend: str,
    review: ImplementationReview,
    stream: TextIO | None = None,
) -> str:
    """Render the reviewer box followed by the progress box for the round."""
    del stream  # kept for signature symmetry with other render helpers
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    reviewer_section = _reviewer_box(
        round_num=round_num,
        backend=reviewer_backend,
        review=review,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )
    progress_section = _progress_box(
        round_num=round_num,
        review=review,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )
    return "\n".join(
        part.rstrip("\n") for part in (reviewer_section, progress_section)
    ) + "\n"


def render_implementation_round_report(
    *,
    round_num: int,
    implementer_backend: str,
    implementer_summary: str,
    reviewer_backend: str,
    review: ImplementationReview,
    stream: TextIO | None = None,
) -> str:
    """Render the three-box report shown after each implementation round."""
    implementer_section = render_implementer_round_box(
        round_num=round_num,
        implementer_backend=implementer_backend,
        implementer_summary=implementer_summary,
        stream=stream,
    )
    reviewer_and_progress = render_reviewer_and_progress_boxes(
        round_num=round_num,
        reviewer_backend=reviewer_backend,
        review=review,
        stream=stream,
    )
    return (
        implementer_section.rstrip("\n")
        + "\n"
        + reviewer_and_progress
    )


def render_mission_approval_card(
    *,
    mission_spec_path: Path,
    mission_spec: str,
    review: MissionReview | None = None,
    stream: TextIO | None = None,
) -> str:
    """Render the mission-approval summary card shown to the user."""
    target = stream if stream is not None else None
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    del target  # stream kept for signature symmetry with other render helpers.

    if review is None:
        review = MissionReview(
            approved=True,
            summary="Reviewer context was not provided.",
            issues=[],
            high_stakes_decisions=_fallback_high_stakes_decisions(mission_spec),
        )

    if review.approved:
        status = _style("APPROVED", GOOD_ANSI, color=color)
    else:
        status = _style("CHANGES REQUESTED", BAD_ANSI, color=color)

    lines: list[str] = [
        f"{_style('Mission spec:', LABEL_ANSI, color=color)} {mission_spec_path}",
        f"{_style('Reviewer status:', LABEL_ANSI, color=color)} {status}",
    ]

    if review.summary:
        lines.append("")
        lines.append(_style("Reviewer Summary", LABEL_ANSI, color=color))
        for wrapped in textwrap.wrap(review.summary, width=content_width) or [""]:
            lines.append(wrapped)

    lines.append("")
    lines.append(_style("Mission Success Behaviors", LABEL_ANSI, color=color))
    behaviors = _extract_mission_spec_bullets(mission_spec)
    if behaviors:
        for idx, behavior in enumerate(behaviors, start=1):
            numbered = f"{idx}. {behavior}"
            lines.extend(_wrap_bullet(numbered, content_width, indent="  ", cont="     "))
    else:
        lines.append(
            _style(
                "  No mission success behaviors were found in the draft.",
                BAD_ANSI,
                color=color,
            )
        )

    lines.append("")
    lines.append(_style("High-Stakes / Controversial Decisions", LABEL_ANSI, color=color))
    if review.high_stakes_decisions:
        for idx, decision in enumerate(review.high_stakes_decisions, start=1):
            numbered = f"{idx}. {_strip_leading_number(decision)}"
            lines.extend(_wrap_bullet(numbered, content_width, indent="  ", cont="     "))
    else:
        lines.append(
            _style(
                "  No high-stakes or controversial decisions were flagged.",
                MUTED_ANSI,
                color=color,
            )
        )

    lines.append("")
    lines.append(_style("Reviewer Sign-Off Blockers", LABEL_ANSI, color=color))
    if review.issues:
        for idx, issue in enumerate(review.issues, start=1):
            if idx > 1:
                lines.append("")
            severity_style = SEVERITY_ANSI.get(issue.severity.lower(), "38;5;252")
            severity_tag = _style(f"[{issue.severity.upper()}]", severity_style, color=color)
            title_line = f"{idx}. {severity_tag} {issue.title}"
            for wrapped in _wrap_with_indent(title_line, content_width, indent="   "):
                lines.append(wrapped)
            detail_lines = textwrap.wrap(issue.details, width=content_width - 6) if issue.details else []
            if detail_lines:
                for detail in detail_lines[:ISSUE_DETAIL_MAX_LINES]:
                    lines.append(f"      {detail}")
                if len(detail_lines) > ISSUE_DETAIL_MAX_LINES:
                    lines.append(_style("      ...", MUTED_ANSI, color=color))
    else:
        lines.append(
            _style(
                "  Reviewer has no unresolved sign-off blockers.",
                GOOD_ANSI,
                color=color,
            )
        )

    lines.append("")
    lines.append(_style("Actions", LABEL_ANSI, color=color))
    for action in (
        f"1. {_style('Approve', GOOD_ANSI, color=color)} to lock this mission spec.",
        f"2. {_style('Request changes', BAD_ANSI, color=color)} to send it back with feedback.",
        "3. Abort to stop the mission.",
    ):
        lines.extend(_wrap_bullet(action, content_width, indent="  ", cont="     "))

    return _compose_card(
        title="Mission Approval Request",
        body_lines=lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def _implementer_box(
    *,
    round_num: int,
    backend: str,
    summary_markdown: str,
    total_width: int,
    content_width: int,
    color: bool,
) -> str:
    sections = parse_markdown_sections(summary_markdown)
    display_order = ("Accomplished", "Tests Run", "Remaining Risks")
    lines: list[str] = []
    seen_any = False
    for name in display_order:
        bullets = _find_section(sections, name)
        if not bullets:
            continue
        if seen_any:
            lines.append("")
        seen_any = True
        lines.append(_style(name, LABEL_ANSI, color=color))
        for bullet in bullets:
            for wrapped in _wrap_bullet(bullet, content_width, indent="  • ", cont="    "):
                lines.append(wrapped)
    if not seen_any:
        lines.append(_style("(implementer produced no structured sections)", MUTED_ANSI, color=color))

    title = f"Round {round_num} — Implementer ({backend})"
    return _compose_card(
        title=title,
        body_lines=lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def _reviewer_box(
    *,
    round_num: int,
    backend: str,
    review: ImplementationReview,
    total_width: int,
    content_width: int,
    color: bool,
) -> str:
    accomplished_label = (
        _style("YES", GOOD_ANSI, color=color)
        if review.mission_accomplished
        else _style("NO", BAD_ANSI, color=color)
    )
    issues_label = (
        _style("NO", GOOD_ANSI, color=color)
        if not review.has_issues
        else _style("YES", BAD_ANSI, color=color)
    )
    lines: list[str] = [
        f"{_style('mission_accomplished:', LABEL_ANSI, color=color)} {accomplished_label}"
        f"   {_style('has_issues:', LABEL_ANSI, color=color)} {issues_label}",
    ]
    if review.summary:
        lines.append("")
        lines.append(_style("Summary", LABEL_ANSI, color=color))
        for wrapped in textwrap.wrap(review.summary, width=content_width) or [""]:
            lines.append(wrapped)

    if review.issues:
        lines.append("")
        header = f"Outstanding issues ({len(review.issues)})"
        lines.append(_style(header, LABEL_ANSI, color=color))
        for idx, issue in enumerate(review.issues, start=1):
            if idx > 1:
                lines.append("")
            severity_style = SEVERITY_ANSI.get(issue.severity.lower(), "38;5;252")
            severity_tag = _style(f"[{issue.severity.upper()}]", severity_style, color=color)
            category_tag = _style(f"[{issue.category}]", MUTED_ANSI, color=color)
            title_line = f"{idx}. {severity_tag} {category_tag} {issue.title}"
            for wrapped in _wrap_with_indent(title_line, content_width, indent="   "):
                lines.append(wrapped)
            detail_lines = textwrap.wrap(issue.details, width=content_width - 6) if issue.details else []
            if detail_lines:
                for detail in detail_lines[:ISSUE_DETAIL_MAX_LINES]:
                    lines.append(f"      {detail}")
                if len(detail_lines) > ISSUE_DETAIL_MAX_LINES:
                    lines.append(_style("      ...", MUTED_ANSI, color=color))
    else:
        lines.append("")
        lines.append(_style("No outstanding issues.", GOOD_ANSI, color=color))

    title = f"Round {round_num} — Reviewer ({backend})"
    return _compose_card(
        title=title,
        body_lines=lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def _progress_box(
    *,
    round_num: int,
    review: ImplementationReview,
    total_width: int,
    content_width: int,
    color: bool,
) -> str:
    pct = max(0, min(100, int(review.progress_pct)))
    bar = _render_progress_bar(pct, PROGRESS_BAR_WIDTH, color=color)
    lines: list[str] = [
        f"{_style('Mission progress:', LABEL_ANSI, color=color)} {bar}  {_style(f'{pct}%', LABEL_ANSI, color=color)}",
    ]

    completed = review.completed_criteria
    remaining = review.remaining_criteria
    if completed or remaining:
        lines.append("")
        header_left = _style(f"✓ Completed ({len(completed)})", GOOD_ANSI, color=color)
        header_right = _style(f"✗ Remaining ({len(remaining)})", BAD_ANSI, color=color)
        lines.extend(
            _render_two_column_lists(
                left_header=header_left,
                right_header=header_right,
                left_items=completed,
                right_items=remaining,
                total_width=content_width,
                color=color,
            )
        )
    else:
        lines.append("")
        lines.append(
            _style(
                "(reviewer did not split completed/remaining criteria)",
                MUTED_ANSI,
                color=color,
            )
        )

    title = f"Round {round_num} — Progress"
    return _compose_card(
        title=title,
        body_lines=lines,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )


def _compose_card(
    *,
    title: str,
    body_lines: list[str],
    total_width: int,
    content_width: int,
    color: bool,
) -> str:
    rendered = [
        f"╭{'─' * (total_width - 2)}╮",
        f"│ {_pad_ansi(_style(title, HEADING_ANSI, color=color), content_width)} │",
        f"├{'─' * (total_width - 2)}┤",
    ]
    for line in body_lines:
        for sub in _wrap_preserving_ansi(line, content_width):
            styled = _render_inline_markdown(sub, color=color)
            rendered.append(f"│ {_pad_ansi(styled, content_width)} │")
    rendered.append(f"╰{'─' * (total_width - 2)}╯")
    return "\n".join(rendered) + "\n"


def _wrap_preserving_ansi(line: str, width: int) -> list[str]:
    """Wrap a potentially ANSI-styled line while respecting display width.

    Falls back to naive re-wrapping on the plain text and re-applying the
    first ANSI prefix when the line is too long. If the line already fits,
    it's returned as-is.
    """
    if _display_width(line) <= width:
        return [line]
    clean = ANSI_PATTERN.sub("", line)
    wrapped = textwrap.wrap(clean, width=width) or [""]
    # Lose ANSI colors on wrapped overflow lines rather than try to split
    # escape sequences; the terminal still shows correct text.
    return wrapped


def _wrap_bullet(text: str, width: int, *, indent: str, cont: str) -> list[str]:
    """Wrap a bullet under a fixed indent."""
    prefix_width = len(indent)
    first = textwrap.wrap(text, width=max(1, width - prefix_width)) or [""]
    lines = [f"{indent}{first[0]}"]
    for extra in first[1:]:
        lines.append(f"{cont}{extra}")
    return lines


def _wrap_with_indent(first_line: str, width: int, *, indent: str) -> list[str]:
    """Wrap a potentially-ANSI first line, continuation indented."""
    if _display_width(first_line) <= width:
        return [first_line]
    clean = ANSI_PATTERN.sub("", first_line)
    wrapped = textwrap.wrap(clean, width=max(1, width - len(indent))) or [first_line]
    result = [wrapped[0]]
    for extra in wrapped[1:]:
        result.append(f"{indent}{extra}")
    return result


def _render_progress_bar(pct: int, width: int, *, color: bool) -> str:
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    filled_segment = _style("█" * filled, BAR_FILLED_ANSI, color=color)
    empty_segment = _style("░" * (width - filled), BAR_EMPTY_ANSI, color=color)
    return filled_segment + empty_segment


def _render_two_column_lists(
    *,
    left_header: str,
    right_header: str,
    left_items: list[str],
    right_items: list[str],
    total_width: int,
    color: bool,
) -> list[str]:
    """Render completed/remaining as side-by-side bullet columns."""
    gap = 2
    half = (total_width - gap) // 2
    right_start = len(left_items) + 1
    if half < 16:
        # Terminal too narrow for two columns; fall back to stacked lists.
        stacked: list[str] = [left_header]
        for idx, item in enumerate(left_items, start=1):
            numbered = f"{idx}. {_strip_leading_number(item)}"
            stacked.extend(_wrap_bullet(numbered, total_width, indent="  ✓ ", cont="    "))
        stacked.append("")
        stacked.append(right_header)
        for idx, item in enumerate(right_items, start=right_start):
            numbered = f"{idx}. {_strip_leading_number(item)}"
            stacked.extend(_wrap_bullet(numbered, total_width, indent="  ✗ ", cont="    "))
        return stacked

    left_lines: list[str] = [left_header]
    for idx, item in enumerate(left_items, start=1):
        numbered = f"{idx}. {_strip_leading_number(item)}"
        left_lines.extend(_wrap_bullet(numbered, half, indent="  ✓ ", cont="    "))
    right_lines: list[str] = [right_header]
    for idx, item in enumerate(right_items, start=right_start):
        numbered = f"{idx}. {_strip_leading_number(item)}"
        right_lines.extend(_wrap_bullet(numbered, half, indent="  ✗ ", cont="    "))

    rows = max(len(left_lines), len(right_lines))
    merged: list[str] = []
    for row in range(rows):
        left_cell = left_lines[row] if row < len(left_lines) else ""
        right_cell = right_lines[row] if row < len(right_lines) else ""
        merged.append(
            f"{_pad_ansi(left_cell, half)}{' ' * gap}{right_cell}"
        )
    return merged


def _find_section(sections: dict[str, list[str]], name: str) -> list[str]:
    """Case-insensitive section lookup that also falls back to partial match."""
    lowered = {key.lower(): value for key, value in sections.items()}
    if name.lower() in lowered:
        return lowered[name.lower()]
    for key, value in lowered.items():
        if name.lower() in key:
            return value
    return []


def _fallback_high_stakes_decisions(mission_spec: str) -> list[str]:
    """Best-effort extraction for approval summaries without reviewer context."""
    criteria = _extract_mission_spec_bullets(mission_spec)
    if not criteria:
        return []

    signal_words = (
        "api",
        "auth",
        "breaking",
        "cli",
        "contract",
        "data",
        "default",
        "delete",
        "drop",
        "migrate",
        "migration",
        "permission",
        "public",
        "remove",
        "rename",
        "replace",
        "rollback",
        "schema",
        "security",
    )
    focused = [
        item for item in criteria
        if any(word in item.lower() for word in signal_words)
    ]
    return (focused or criteria)[:5]
