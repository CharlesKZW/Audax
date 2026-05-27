"""Progress reporting and safe subprocess execution helpers."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, TextIO

from .models import DEFAULT_HEARTBEAT_SECONDS

_FORCE_TTY_ENV_VARS = ("AUDAX_FORCE_TTY", "AUDAX_FORCE_RICH_TERMINAL")


class HeartbeatProgress:
    """Print sparse progress updates without streaming raw agent output."""

    SHIMMER_FRAMES = (
        "[=   ]",
        "[==  ]",
        "[=== ]",
        "[ ===]",
        "[  ==]",
        "[   =]",
    )

    def __init__(
        self,
        label: str,
        interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
        inline_updates: bool | None = None,
    ) -> None:
        self.label = label
        self.interval_seconds = max(0.0, interval_seconds)
        self.stream = stream or sys.stdout
        self.clock = clock
        self.started_at: float | None = None
        self.last_update: float | None = None
        self._shimmer_index = 0
        self._last_inline_width = 0
        self._inline_updates = (
            self._supports_inline_updates(self.stream)
            if inline_updates is None
            else inline_updates
        )
        self._color_updates = self._supports_color(self.stream)

    @property
    def uses_inline_updates(self) -> bool:
        """Return whether the current stream supports in-place progress updates."""
        return self._inline_updates

    def start(self) -> None:
        now = self.clock()
        self.started_at = now
        self.last_update = now
        if self._inline_updates:
            self._write_inline(self._working_message())
            return
        if self._color_updates:
            self._write(self._working_message())
            return
        self._write(f"[{self.label}] working...")

    def maybe_emit(self) -> None:
        if self.started_at is None or self.last_update is None:
            return
        now = self.clock()
        if self._inline_updates:
            self.last_update = now
            self._advance_shimmer()
            self._write_inline(self._working_message())
            return
        if self.interval_seconds and (now - self.last_update) >= self.interval_seconds:
            self.last_update = now
            self._advance_shimmer()
            if self._color_updates:
                self._write(
                    self._working_message(action="still working", elapsed_suffix=" elapsed")
                )
            else:
                self._write(
                    f"[{self.label}] still working ({int(now - self.started_at)}s elapsed)"
                )

    def finish(self, success: bool) -> None:
        if self.started_at is None:
            return
        status = "done" if success else "failed"
        elapsed = int(self.clock() - self.started_at)
        message = f"[{self.label}] {status} ({elapsed}s)"
        if self._color_updates:
            status_code = "1;38;5;82" if success else "1;38;5;203"
            message = (
                f"{self._style(f'[{self.label}]', '1;38;5;117')} "
                f"{self._style(status, status_code)} "
                f"{self._style(f'({elapsed}s)', '38;5;244')}"
            )
        if self._inline_updates:
            self._write_inline(message, final=True)
            return
        self._write(message)

    def _working_message(
        self,
        *,
        action: str = "working",
        elapsed_suffix: str = "",
    ) -> str:
        """Render the current in-progress status message."""
        assert self.started_at is not None
        elapsed = int(self.clock() - self.started_at)
        frame = self.SHIMMER_FRAMES[self._shimmer_index]
        if not self._color_updates:
            return f"[{self.label}] {frame} {action} ({elapsed}s{elapsed_suffix})"
        label = self._style(f"[{self.label}]", "38;5;117")
        shimmer = self._style(frame, "38;5;45")
        action_text = self._style(action, "38;5;252")
        elapsed_text = self._style(f"({elapsed}s{elapsed_suffix})", "38;5;244")
        return f"{label} {shimmer} {action_text} {elapsed_text}"

    def _advance_shimmer(self) -> None:
        self._shimmer_index = (self._shimmer_index + 1) % len(self.SHIMMER_FRAMES)

    def _write(self, message: str) -> None:
        self.stream.write(f"{message}\n")
        self.stream.flush()

    def _write_inline(self, message: str, *, final: bool = False) -> None:
        """Render an inline status line, padding to overwrite prior content."""
        padding = ""
        if len(message) < self._last_inline_width:
            padding = " " * (self._last_inline_width - len(message))
        self.stream.write(f"\r{message}{padding}")
        if final:
            self.stream.write("\n")
            self._last_inline_width = 0
        else:
            self._last_inline_width = len(message)
        self.stream.flush()

    def clear_inline(self) -> None:
        """Clear the current inline status before another writer emits output."""
        if not self._inline_updates or not self._last_inline_width:
            return
        self.stream.write("\r")
        self.stream.write(" " * self._last_inline_width)
        self.stream.write("\r")
        self.stream.flush()

    def redraw_inline(self) -> None:
        """Redraw the current inline status after another writer emits output."""
        if not self._inline_updates or self.started_at is None:
            return
        self._write_inline(self._working_message())

    @staticmethod
    def _supports_inline_updates(stream: TextIO) -> bool:
        """Return whether a stream supports carriage-return style updates."""
        if _force_tty_output():
            return True
        isatty = getattr(stream, "isatty", None)
        if not callable(isatty):
            return False
        try:
            return bool(isatty())
        except OSError:
            return False

    @staticmethod
    def _supports_color(stream: TextIO) -> bool:
        """Return whether a stream should receive ANSI-colored progress output."""
        if os.environ.get("NO_COLOR") is not None:
            return False
        if os.environ.get("TERM", "").lower() == "dumb" and not _force_tty_output():
            return False
        return HeartbeatProgress._supports_inline_updates(stream)

    @staticmethod
    def _style(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m"


def _force_tty_output() -> bool:
    """Return whether Audax should trust terminal control even if isatty is false."""
    return any(_truthy_env(name) for name in _FORCE_TTY_ENV_VARS)


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


class QuietProcessRunner:
    """Run a subprocess quietly while printing heartbeat status lines."""

    def __init__(
        self,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        progress_stream: TextIO | None = None,
        subprocess_timeout_seconds: float | None = None,
    ) -> None:
        self.heartbeat_seconds = heartbeat_seconds
        self.progress_stream = progress_stream or sys.stdout
        self.subprocess_timeout_seconds = (
            None
            if subprocess_timeout_seconds is None or subprocess_timeout_seconds <= 0
            else float(subprocess_timeout_seconds)
        )

    def run(
        self,
        cmd: list[str],
        label: str,
        *,
        cwd: Path,
        stdin_text: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        disable_heartbeat: bool = False,
    ) -> str:
        """Run a command, capture combined output, and enforce an optional timeout.

        ``on_chunk`` receives each raw stdout chunk as it arrives, in the
        background drain thread; callers use it to stream partial output to
        the user. When the progress stream is a TTY, inline heartbeats are
        cleared before streamed output and redrawn afterward.
        """
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE if stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=os.name != "nt",
        )

        chunks: list[str] = []
        read_errors: list[BaseException] = []
        output_lock = threading.RLock()
        progress: HeartbeatProgress | None = None

        if not disable_heartbeat:
            supports_inline = HeartbeatProgress._supports_inline_updates(
                self.progress_stream
            )
            interval_seconds = self.heartbeat_seconds
            if on_chunk is not None and not supports_inline:
                interval_seconds = 0
            progress = HeartbeatProgress(
                label=label,
                interval_seconds=interval_seconds,
                stream=self.progress_stream,
            )
            with output_lock:
                progress.start()

        def drain_stdout() -> None:
            try:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if on_chunk is not None:
                        try:
                            with output_lock:
                                if progress is not None and progress.uses_inline_updates:
                                    progress.clear_inline()
                                on_chunk(chunk)
                                if progress is not None and progress.uses_inline_updates:
                                    progress.redraw_inline()
                        except BaseException:
                            # Never let a callback failure break the drain.
                            pass
            except BaseException as exc:  # pragma: no cover
                read_errors.append(exc)
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()

        thread = threading.Thread(target=drain_stdout, daemon=True)
        thread.start()

        started_at = time.monotonic()
        timed_out = False
        return_code: int | None = None
        output = ""

        try:
            if stdin_text is not None:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text)
                proc.stdin.close()

            inline_sleep = (
                progress is not None and progress.uses_inline_updates
            )
            sleep_seconds = 0.1 if inline_sleep else (
                0.2 if self.heartbeat_seconds <= 0 else min(0.5, self.heartbeat_seconds / 2)
            )
            while proc.poll() is None:
                time.sleep(sleep_seconds)
                if progress is not None:
                    with output_lock:
                        progress.maybe_emit()
                if self.subprocess_timeout_seconds is not None:
                    elapsed = time.monotonic() - started_at
                    if elapsed >= self.subprocess_timeout_seconds:
                        timed_out = True
                        self._terminate_process(proc)
                        break

            return_code = proc.wait()
            thread.join()

            if read_errors:
                raise RuntimeError(f"{label} output capture failed: {read_errors[0]}")

            output = "".join(chunks)
        except BaseException:
            self._terminate_process(proc)
            thread.join(timeout=1)
            if progress is not None:
                with output_lock:
                    progress.finish(success=False)
            raise
        finally:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()

        assert return_code is not None
        if progress is not None:
            with output_lock:
                progress.finish(success=return_code == 0 and not timed_out)

        if timed_out:
            trimmed = output.strip()
            detail = f"\n{trimmed}" if trimmed else ""
            raise RuntimeError(
                f"{label} timed out after {self.subprocess_timeout_seconds:g}s{detail}"
            )

        if return_code != 0:
            trimmed = output.strip()
            detail = f"\n{trimmed}" if trimmed else ""
            raise RuntimeError(f"{label} exited with status {return_code}{detail}")

        return output

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        """Terminate a subprocess group as cleanly as possible."""
        if proc.poll() is not None:
            return

        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                self._terminate_windows_process_tree(proc, force=False)
            proc.wait(timeout=1)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

        if proc.poll() is not None:
            return

        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                self._terminate_windows_process_tree(proc, force=True)
            proc.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

    def _terminate_windows_process_tree(
        self,
        proc: subprocess.Popen[str],
        *,
        force: bool,
    ) -> None:
        """Terminate a Windows process tree via ``taskkill``."""
        cmd = ["taskkill", "/PID", str(proc.pid), "/T"]
        if force:
            cmd.append("/F")
        try:
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            if force:
                proc.kill()
            else:
                proc.terminate()
