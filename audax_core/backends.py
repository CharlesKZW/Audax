"""Adapters for the external Claude and Codex command-line interfaces."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from .progress import QuietProcessRunner

CLAUDE_INPUT_FORMAT = "text"
CLAUDE_OUTPUT_FORMAT = "stream-json"
CLAUDE_INCLUDE_PARTIAL_MESSAGES = True
CLAUDE_VERBOSE = True
CLAUDE_SKIP_PERMISSIONS = True
CLAUDE_MODEL = "opus"
CLAUDE_REASONING_EFFORT = "max"

CODEX_MODEL = "gpt-5.4"
CODEX_REASONING_EFFORT = "xhigh"
CODEX_BYPASS_APPROVALS_AND_SANDBOX = True


def parse_claude_stream_output(output: str) -> str:
    """Extract the assistant text from Claude's ``stream-json`` output."""
    chunks: list[str] = []
    final_result = ""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "stream_event":
            inner = event.get("event", {})
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    delta_text = delta.get("text", "")
                    if delta_text:
                        chunks.append(delta_text)
        elif event.get("type") == "result":
            final_result = event.get("result", "") or ""

    if final_result.strip():
        return final_result.strip()
    assembled = "".join(chunks).strip()
    if assembled:
        return assembled
    return output.strip()


class ClaudeCLI:
    """Thin wrapper around the Claude CLI prompt interface."""

    def __init__(self, cmd: str, process_runner: QuietProcessRunner, repo_root: Path) -> None:
        self.cmd = cmd
        self.process_runner = process_runner
        self.repo_root = repo_root

    def run(self, prompt: str, label: str) -> str:
        """Execute Claude with a plain-text prompt and return its rendered text."""
        cmd = [
            self.cmd,
            "-p",
            "--input-format",
            CLAUDE_INPUT_FORMAT,
        ]
        if CLAUDE_MODEL:
            cmd.extend(["--model", CLAUDE_MODEL])
        if CLAUDE_REASONING_EFFORT:
            cmd.extend(["--effort", CLAUDE_REASONING_EFFORT])
        if CLAUDE_SKIP_PERMISSIONS:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(
            [
                "--output-format",
                CLAUDE_OUTPUT_FORMAT,
            ]
        )
        if CLAUDE_VERBOSE:
            cmd.append("--verbose")
        if CLAUDE_INCLUDE_PARTIAL_MESSAGES:
            cmd.append("--include-partial-messages")
        output = self.process_runner.run(cmd, label, cwd=self.repo_root, stdin_text=prompt)
        return parse_claude_stream_output(output)


class CodexCLI:
    """Thin wrapper around the Codex structured-output CLI interface."""

    def __init__(self, cmd: str, process_runner: QuietProcessRunner, repo_root: Path) -> None:
        self.cmd = cmd
        self.process_runner = process_runner
        self.repo_root = repo_root

    def run_json(self, prompt: str, label: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Execute Codex with a JSON schema and return the parsed object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            schema_path = tmp_path / "codex_schema.json"
            output_path = tmp_path / "codex_output.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            cmd = [
                self.cmd,
                "exec",
                "--model",
                CODEX_MODEL,
                "-c",
                f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
            ]
            if CODEX_BYPASS_APPROVALS_AND_SANDBOX:
                cmd.append("--dangerously-bypass-approvals-and-sandbox")
            cmd.extend(
                [
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "-",
                ]
            )
            self.process_runner.run(cmd, label, cwd=self.repo_root, stdin_text=prompt)
            if not output_path.exists():
                raise RuntimeError(f"{label} finished without creating {output_path}")
            return json.loads(output_path.read_text(encoding="utf-8"))
