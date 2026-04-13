from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


def test_sphinx_html_build(tmp_path: Path) -> None:
    pytest.importorskip("sphinx")
    pytest.importorskip("pydata_sphinx_theme")
    pytest.importorskip("sphinx_design")
    pytest.importorskip("sphinx_copybutton")

    repo_root = Path(__file__).resolve().parents[1]
    outdir = tmp_path / "html"
    doctreedir = tmp_path / "doctrees"
    cmd = [
        sys.executable,
        "-m",
        "sphinx",
        "-W",
        "-b",
        "html",
        "-d",
        str(doctreedir),
        str(repo_root / "docs"),
        str(outdir),
    ]

    completed = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert (outdir / "index.html").exists()
