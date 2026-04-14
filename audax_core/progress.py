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


class HeartbeatProgress:
    """Print sparse progress updates without streaming raw agent output."""

    SPINNER_FRAMES = ("|", "/", "-", "\\")

    def __init__(
        self,
        label: str,
        interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.label = label
        self.interval_seconds = max(0.0, interval_seconds)
        self.stream = stream or sys.stdout
        self.clock = clock
        self.started_at: float | None = None
        self.last_update: float | None = None
        self._spinner_index = 0
        self._last_inline_width = 0
        self._inline_updates = self._supports_inline_updates(self.stream)

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
        self._write(f"[{self.label}] working...")

    def maybe_emit(self) -> None:
        if self.started_at is None or self.last_update is None:
            return
        now = self.clock()
        if self._inline_updates:
            self.last_update = now
            self._spinner_index = (self._spinner_index + 1) % len(self.SPINNER_FRAMES)
            self._write_inline(self._working_message())
            return
        if self.interval_seconds and (now - self.last_update) >= self.interval_seconds:
            self.last_update = now
            self._write(f"[{self.label}] still working ({int(now - self.started_at)}s elapsed)")

    def finish(self, success: bool) -> None:
        if self.started_at is None:
            return
        status = "done" if success else "failed"
        elapsed = int(self.clock() - self.started_at)
        if self._inline_updates:
            self._write_inline(f"[{self.label}] {status} ({elapsed}s)", final=True)
            return
        self._write(f"[{self.label}] {status} ({elapsed}s)")

    def _working_message(self) -> str:
        """Render the current in-progress status message."""
        assert self.started_at is not None
        elapsed = int(self.clock() - self.started_at)
        frame = self.SPINNER_FRAMES[self._spinner_index]
        return f"[{self.label}] {frame} working ({elapsed}s)"

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

    @staticmethod
    def _supports_inline_updates(stream: TextIO) -> bool:
        """Return whether a stream supports carriage-return style updates."""
        isatty = getattr(stream, "isatty", None)
        if not callable(isatty):
            return False
        try:
            return bool(isatty())
        except OSError:
            return False


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
    ) -> str:
        """Run a command, capture combined output, and enforce an optional timeout."""
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

        def drain_stdout() -> None:
            try:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except BaseException as exc:  # pragma: no cover
                read_errors.append(exc)
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()

        thread = threading.Thread(target=drain_stdout, daemon=True)
        thread.start()

        progress = HeartbeatProgress(
            label=label,
            interval_seconds=self.heartbeat_seconds,
            stream=self.progress_stream,
        )
        progress.start()
        started_at = time.monotonic()
        timed_out = False
        return_code: int | None = None
        output = ""

        try:
            if stdin_text is not None:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text)
                proc.stdin.close()

            sleep_seconds = 0.1 if progress.uses_inline_updates else (
                0.2 if self.heartbeat_seconds <= 0 else min(0.5, self.heartbeat_seconds / 2)
            )
            while proc.poll() is None:
                time.sleep(sleep_seconds)
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
            progress.finish(success=False)
            raise
        finally:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()

        assert return_code is not None
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
