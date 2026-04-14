"""Terminal UI helpers for Audax launch and session headers."""

from __future__ import annotations

import os
import re
import shutil
import textwrap
from typing import TextIO
import unicodedata

from .models import LoopConfig

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
CARD_MIN_WIDTH = 84
CARD_MAX_WIDTH = 116


def supports_rich_terminal(stream: TextIO) -> bool:
    """Return whether a stream supports the richer card-style terminal UI."""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        if not isatty():
            return False
    except OSError:
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def render_startup_card(stream: TextIO, info_lines: list[str] | None = None) -> str:
    """Render the interactive startup card shown before stdin mission entry."""
    return _render_card(
        stream=stream,
        title="AUDAX CONSOLE",
        info_lines=info_lines or [
            "Enter the mission prompt for Audax.",
            "Press Ctrl-D when you are done.",
            "Audax will make changes in the current working directory.",
        ],
    )


def render_session_header_card(task: str, config: LoopConfig, stream: TextIO) -> str:
    """Render the rich TTY header card for an Audax mission run."""
    approval_mode = "required" if config.require_mission_approval else "auto"
    return _render_card(
        stream=stream,
        title="AUDAX COLLABORATIVE MISSION LOOP",
        info_lines=[
            f"Task: {task}",
            f"Repo: {config.repo_root}",
            f"Workspace: {config.workspace_dir}",
            f"Spec rounds max: {config.max_spec_rounds}",
            f"Implementation rounds max: {config.max_implementation_rounds}",
            f"Mission approval: {approval_mode}",
        ],
    )


def _render_card(
    *,
    stream: TextIO,
    title: str,
    info_lines: list[str],
) -> str:
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    wrapped_info = _wrap_lines(info_lines, width=content_width)

    rendered_lines = [
        f"╭{'─' * (total_width - 2)}╮",
        f"│ {_pad_ansi(_style(title, '1;38;5;117', color=color), content_width)} │",
        f"├{'─' * (total_width - 2)}┤",
    ]
    for info_line in wrapped_info:
        styled = _style(info_line, '38;5;252', color=color)
        rendered_lines.append(f"│ {_pad_ansi(styled, content_width)} │")
    rendered_lines.append(f"╰{'─' * (total_width - 2)}╯")
    return "\n".join(rendered_lines) + "\n"


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
