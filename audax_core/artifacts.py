"""Mission artifact creation, locking, and lightweight PDF rendering."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import textwrap

from .models import LockedMissionSpec, MissionArtifacts


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_mission_spec(markdown_text: str, artifacts: MissionArtifacts, task: str) -> LockedMissionSpec:
    """Write and lock the immutable mission-spec artifacts for a run."""
    artifacts.mission_spec_md.write_text(markdown_text.strip() + "\n", encoding="utf-8")
    write_simple_pdf(
        artifacts.mission_spec_pdf,
        title="Audax Mission Spec",
        text=markdown_text.strip(),
    )

    manifest = {
        "task": task,
        "markdown_sha256": sha256_file(artifacts.mission_spec_md),
        "pdf_sha256": sha256_file(artifacts.mission_spec_pdf),
        "mission_spec_md": str(artifacts.mission_spec_md),
        "mission_spec_pdf": str(artifacts.mission_spec_pdf),
    }
    artifacts.mission_spec_lock.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return LockedMissionSpec(
        markdown_text=artifacts.mission_spec_md.read_text(encoding="utf-8"),
        markdown_sha256=manifest["markdown_sha256"],
        pdf_sha256=manifest["pdf_sha256"],
    )


def assert_mission_spec_locked(artifacts: MissionArtifacts) -> None:
    """Verify that the locked markdown and PDF artifacts are unchanged."""
    if not artifacts.mission_spec_lock.exists():
        raise RuntimeError("Mission spec lock file is missing")
    manifest = json.loads(artifacts.mission_spec_lock.read_text(encoding="utf-8"))
    expected_md_hash = str(manifest.get("markdown_sha256", ""))
    expected_pdf_hash = str(manifest.get("pdf_sha256", ""))
    current_md_hash = sha256_file(artifacts.mission_spec_md)
    current_pdf_hash = sha256_file(artifacts.mission_spec_pdf)
    if current_md_hash != expected_md_hash or current_pdf_hash != expected_pdf_hash:
        raise RuntimeError("Mission spec lock mismatch: immutable mission artifacts were modified")


def wrap_text_lines(text: str, *, width: int = 88) -> list[str]:
    """Wrap plain text into simple PDF-friendly line chunks."""
    wrapped: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            wrapped.append("")
            continue
        if line.startswith("    "):
            wrapped.append(line[:width])
            remainder = line[width:]
            while remainder:
                wrapped.append(remainder[:width])
                remainder = remainder[width:]
            continue
        pieces = textwrap.wrap(
            line,
            width=width,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        wrapped.extend(pieces or [""])
    return wrapped or [""]


def pdf_escape(text: str) -> str:
    """Escape a text fragment for placement in a minimal PDF content stream."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("latin-1", "replace").decode("latin-1")


def write_simple_pdf(path: Path, *, title: str, text: str) -> None:
    """Render a compact single-font PDF without external dependencies."""
    lines = [title, ""] + wrap_text_lines(text)
    lines_per_page = 48
    pages = [lines[idx : idx + lines_per_page] for idx in range(0, len(lines), lines_per_page)]
    objects: dict[int, bytes] = {}

    catalog_id = 1
    pages_id = 2
    font_id = 3
    next_id = 4
    page_ids: list[int] = []

    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for page_lines in pages:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)

        commands = ["BT", "/F1 12 Tf", "72 740 Td", "14 TL"]
        for line in page_lines:
            commands.append(f"({pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        content_stream = "\n".join(commands).encode("latin-1")

        objects[content_id] = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
            + content_stream
            + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("latin-1")

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * next_id

    for object_id in range(1, next_id):
        offsets[object_id] = buffer.tell()
        buffer.write(f"{object_id} 0 obj\n".encode("latin-1"))
        buffer.write(objects[object_id])
        buffer.write(b"\nendobj\n")

    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {next_id}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for object_id in range(1, next_id):
        buffer.write(f"{offsets[object_id]:010} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer\n<< /Size {next_id} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )

    path.write_bytes(buffer.getvalue())
