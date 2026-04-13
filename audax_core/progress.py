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

    def __init__(
        self,
        label: str,
        interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        stream: TextIO = sys.stdout,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.label = label
        self.interval_seconds = max(0.0, interval_seconds)
        self.stream = stream
        self.clock = clock
        self.started_at: float | None = None
        self.last_update: float | None = None

    def start(self) -> None:
        now = self.clock()
        self.started_at = now
        self.last_update = now
        self._write(f"[{self.label}] working...")

    def maybe_emit(self) -> None:
        if self.started_at is None or self.last_update is None:
            return
        now = self.clock()
        if self.interval_seconds and (now - self.last_update) >= self.interval_seconds:
            self.last_update = now
            self._write(f"[{self.label}] still working ({int(now - self.started_at)}s elapsed)")

    def finish(self, success: bool) -> None:
        if self.started_at is None:
            return
        status = "done" if success else "failed"
        elapsed = int(self.clock() - self.started_at)
        self._write(f"[{self.label}] {status} ({elapsed}s)")

    def _write(self, message: str) -> None:
        self.stream.write(f"{message}\n")
        self.stream.flush()


class QuietProcessRunner:
    """Run a subprocess quietly while printing heartbeat status lines."""

    def __init__(
        self,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        progress_stream: TextIO = sys.stdout,
        subprocess_timeout_seconds: float | None = None,
    ) -> None:
        self.heartbeat_seconds = heartbeat_seconds
        self.progress_stream = progress_stream
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

            sleep_seconds = 0.2 if self.heartbeat_seconds <= 0 else min(0.5, self.heartbeat_seconds / 2)
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
                proc.terminate()
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
                proc.kill()
            proc.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
