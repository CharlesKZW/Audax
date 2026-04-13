from __future__ import annotations

import io
import os
from pathlib import Path
import shutil

import pytest

from audax_core.backends import ClaudeCLI, CodexCLI
from audax_core.progress import QuietProcessRunner


if os.environ.get("AUDAX_RUN_LIVE_CLI_TESTS") != "1":
    pytest.skip("live CLI tests disabled", allow_module_level=True)


@pytest.fixture
def process_runner() -> QuietProcessRunner:
    return QuietProcessRunner(heartbeat_seconds=0.1, progress_stream=io.StringIO())


def test_live_claude_cli_smoke(tmp_path: Path, process_runner: QuietProcessRunner) -> None:
    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")

    cli = ClaudeCLI("claude", process_runner, tmp_path)
    result = cli.run(
        "Reply with exactly CLAUDE_SMOKE_TEST_OK and nothing else.",
        label="Claude live smoke test",
    )

    assert result == "CLAUDE_SMOKE_TEST_OK"


def test_live_codex_cli_smoke(tmp_path: Path, process_runner: QuietProcessRunner) -> None:
    if not shutil.which("codex"):
        pytest.skip("codex CLI not installed")

    cli = CodexCLI("codex", process_runner, tmp_path)
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    result = cli.run_json(
        'Return JSON with exactly {"text":"CODEX_SMOKE_TEST_OK"}.',
        label="Codex live smoke test",
        schema=schema,
    )

    assert result["text"] == "CODEX_SMOKE_TEST_OK"
