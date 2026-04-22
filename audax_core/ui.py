"""Terminal UI helpers for Audax launch, session headers, and round reports."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import textwrap
from typing import TextIO
import unicodedata

from .models import ImplementationReview, LoopConfig, MissionReview

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

_SECTION_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_LEADING_NUMBER_PATTERN = re.compile(r"^\s*\d+[.)]?\s+")

_INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
_INLINE_BOLD_ASTERISK_PATTERN = re.compile(r"\*\*([^*\n]+?)\*\*")
_INLINE_BOLD_UNDERSCORE_PATTERN = re.compile(r"__([^_\n]+?)__")
INLINE_CODE_ANSI = "38;5;213"
INLINE_BOLD_ANSI = "1"


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


def read_task_interactive() -> str:
    """Read a mission prompt via a Codex-style input box.

    Renders a single ``>`` prompt with a shaded background. Long input wraps
    visually onto the next line; Enter submits; Alt+Enter inserts a newline so
    the user can compose multi-line prompts.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.lexers import SimpleLexer
    from prompt_toolkit.styles import Style

    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    style = Style.from_dict({
        "prompt": "bg:#2b2b2b fg:#5fafff bold",
        "input": "bg:#2b2b2b",
    })

    session = PromptSession(
        message=FormattedText([("class:prompt", "> ")]),
        multiline=True,
        wrap_lines=True,
        style=style,
        lexer=SimpleLexer(style="class:input"),
        key_bindings=bindings,
    )

    try:
        return session.prompt()
    except EOFError:
        return ""


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
    target = stream if stream is not None else None
    color = os.environ.get("NO_COLOR") is None
    total_width = _card_width()
    content_width = total_width - 4
    del target  # stream kept for signature compatibility; not used directly.

    implementer_section = _implementer_box(
        round_num=round_num,
        backend=implementer_backend,
        summary_markdown=implementer_summary,
        total_width=total_width,
        content_width=content_width,
        color=color,
    )
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
        part.rstrip("\n")
        for part in (implementer_section, reviewer_section, progress_section)
    ) + "\n"


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
    sections = parse_markdown_sections(mission_spec)
    criteria = [
        _strip_leading_number(item)
        for item in _find_section(sections, "Mission Success Criteria")
    ]
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
