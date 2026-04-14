"""Mission artifact creation, locking, and lightweight PDF rendering."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import textwrap

from .models import LockedMissionSpec, MissionArtifacts, utc_timestamp

try:  # pragma: no cover - optional dependency
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - optional dependency
    Image = ImageDraw = ImageFont = None

try:  # pragma: no cover - optional dependency
    from fontTools.ttLib import TTFont as FontToolsTTFont
except ImportError:  # pragma: no cover - optional dependency
    FontToolsTTFont = None

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover - optional dependency
    PdfReader = PdfWriter = None


PDF_PAGE_WIDTH_POINTS = 612
PDF_PAGE_HEIGHT_POINTS = 792
PDF_LEFT_MARGIN_POINTS = 72
PDF_TOP_MARGIN_POINTS = 52
PDF_FONT_SIZE_POINTS = 12
PDF_LINE_HEIGHT_POINTS = 14
PDF_LINES_PER_PAGE = 48
PDF_RASTER_DPI = 144
PDF_SOURCE_TEXT_METADATA_KEY = "/AudaxSourceText"

UNICODE_TEXT_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arialuni.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
)
UNICODE_EMOJI_FONT_CANDIDATES = (
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/System/Library/Fonts/LastResort.otf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
)

_FONT_CODEPOINT_CACHE: dict[Path, set[int]] = {}
_FONT_OBJECT_CACHE: dict[tuple[Path, int], object] = {}


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
        "session_id": artifacts.session_id,
        "locked_at": utc_timestamp(),
        "task": task,
        "markdown_sha256": sha256_file(artifacts.mission_spec_md),
        "pdf_sha256": sha256_file(artifacts.mission_spec_pdf),
        "session_dir": str(artifacts.session_dir),
        "mission_spec_md": str(artifacts.mission_spec_md),
        "mission_spec_pdf": str(artifacts.mission_spec_pdf),
    }
    artifacts.write_json(artifacts.mission_spec_lock, manifest)
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
    return safe.encode("latin-1").decode("latin-1")


def write_simple_pdf(path: Path, *, title: str, text: str) -> None:
    """Render a compact PDF, falling back to a Unicode-safe raster path when needed."""
    if _can_encode_latin1(title) and _can_encode_latin1(text):
        _write_latin1_pdf(path, title=title, text=text)
    elif not _write_unicode_raster_pdf(path, title=title, text=text):
        _write_latin1_pdf(
            path,
            title=_latin1_escape_text(title),
            text=_latin1_escape_text(text),
        )

    _embed_pdf_source_text(path, title=title, text=text)


def _can_encode_latin1(text: str) -> bool:
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _latin1_escape_text(text: str) -> str:
    """Return a Latin-1-safe representation that preserves Unicode code points."""
    escaped: list[str] = []
    for character in text:
        if _can_encode_latin1(character):
            escaped.append(character)
            continue

        codepoint = ord(character)
        if codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04X}")
        else:
            escaped.append(f"\\U{codepoint:08X}")
    return "".join(escaped)


def _write_latin1_pdf(path: Path, *, title: str, text: str) -> None:
    """Render a compact single-font Latin-1 PDF without external dependencies."""
    lines = [title, ""] + wrap_text_lines(text)
    pages = [lines[idx : idx + PDF_LINES_PER_PAGE] for idx in range(0, len(lines), PDF_LINES_PER_PAGE)]
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

        commands = [
            "BT",
            f"/F1 {PDF_FONT_SIZE_POINTS} Tf",
            f"{PDF_LEFT_MARGIN_POINTS} {PDF_PAGE_HEIGHT_POINTS - PDF_TOP_MARGIN_POINTS} Td",
            f"{PDF_LINE_HEIGHT_POINTS} TL",
        ]
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


def _write_unicode_raster_pdf(path: Path, *, title: str, text: str) -> bool:
    """Render a Unicode-safe PDF by rasterizing the text with font fallbacks."""
    if Image is None or ImageDraw is None or ImageFont is None or FontToolsTTFont is None:
        return False

    scale = PDF_RASTER_DPI / 72.0
    page_width = int(PDF_PAGE_WIDTH_POINTS * scale)
    page_height = int(PDF_PAGE_HEIGHT_POINTS * scale)
    left_margin = int(PDF_LEFT_MARGIN_POINTS * scale)
    top_margin = int(PDF_TOP_MARGIN_POINTS * scale)
    font_size = int(PDF_FONT_SIZE_POINTS * scale)
    line_height = int(PDF_LINE_HEIGHT_POINTS * scale)
    font_paths = _discover_unicode_font_paths(font_size)
    if not font_paths:
        return False

    lines = [title, ""] + wrap_text_lines(text)
    page_line_groups = [
        lines[idx : idx + PDF_LINES_PER_PAGE]
        for idx in range(0, len(lines), PDF_LINES_PER_PAGE)
    ]
    images = []

    try:
        for page_lines in page_line_groups:
            image = Image.new("RGB", (page_width, page_height), "white")
            draw = ImageDraw.Draw(image)
            y = top_margin
            for line in page_lines:
                _draw_unicode_line(
                    draw,
                    line.expandtabs(4),
                    x=left_margin,
                    y=y,
                    font_paths=font_paths,
                    font_size=font_size,
                )
                y += line_height
            images.append(image)

        images[0].save(
            path,
            "PDF",
            resolution=PDF_RASTER_DPI,
            save_all=True,
            append_images=images[1:],
        )
        return True
    finally:
        for image in images:
            image.close()


def _discover_unicode_font_paths(font_size: int) -> list[Path]:
    """Return the available text and emoji fonts used for raster PDF rendering."""
    discovered: list[Path] = []
    for raw_path in UNICODE_TEXT_FONT_CANDIDATES + UNICODE_EMOJI_FONT_CANDIDATES:
        path = Path(raw_path)
        if path.exists() and path not in discovered and _font_is_loadable_with_pillow(path, font_size):
            discovered.append(path)
    return discovered


def _font_is_loadable_with_pillow(font_path: Path, font_size: int) -> bool:
    """Return whether Pillow can load the font at the requested size."""
    try:
        ImageFont.truetype(str(font_path), font_size)
    except OSError:
        return False
    return True


def _draw_unicode_line(
    draw: object,
    line: str,
    *,
    x: int,
    y: int,
    font_paths: list[Path],
    font_size: int,
) -> None:
    """Draw a single line using per-run font fallbacks."""
    if not line:
        return

    cursor_x = x
    current_path: Path | None = None
    current_run: list[str] = []

    for character in line:
        font_path = _font_path_for_character(character, font_paths, preferred=current_path)
        if current_path is not None and font_path != current_path and current_run:
            cursor_x = _draw_text_run(
                draw,
                "".join(current_run),
                font_path=current_path,
                font_size=font_size,
                x=cursor_x,
                y=y,
            )
            current_run = []
        current_path = font_path
        current_run.append(character)

    if current_path is not None and current_run:
        _draw_text_run(
            draw,
            "".join(current_run),
            font_path=current_path,
            font_size=font_size,
            x=cursor_x,
            y=y,
        )


def _draw_text_run(
    draw: object,
    text: str,
    *,
    font_path: Path,
    font_size: int,
    x: int,
    y: int,
) -> int:
    """Draw a run of text and return the next x-coordinate."""
    font = _load_image_font(font_path, font_size)
    try:
        draw.text((x, y), text, font=font, fill="black", embedded_color=True)
    except TypeError:  # pragma: no cover - older Pillow
        draw.text((x, y), text, font=font, fill="black")
    return x + int(round(_measure_text_width(font, text)))


def _measure_text_width(font: object, text: str) -> float:
    """Measure text width for cursor advance during raster rendering."""
    getlength = getattr(font, "getlength", None)
    if callable(getlength):
        return float(getlength(text))
    bbox = font.getbbox(text)
    return float(bbox[2] - bbox[0])


def _load_image_font(font_path: Path, font_size: int) -> object:
    """Load and cache a Pillow font object."""
    cache_key = (font_path, font_size)
    font = _FONT_OBJECT_CACHE.get(cache_key)
    if font is None:
        font = ImageFont.truetype(str(font_path), font_size)
        _FONT_OBJECT_CACHE[cache_key] = font
    return font


def _font_path_for_character(
    character: str,
    font_paths: list[Path],
    *,
    preferred: Path | None,
) -> Path:
    """Choose the first font that supports the given character."""
    if character.isspace():
        return preferred or font_paths[0]

    candidates: list[Path] = []
    if preferred is not None:
        candidates.append(preferred)
    candidates.extend(path for path in font_paths if path not in candidates)

    for path in candidates:
        if _font_supports_character(path, character):
            return path

    codepoint = ord(character)
    raise RuntimeError(
        f"Mission spec PDF rendering does not support character U+{codepoint:04X} ({character!r})."
    )


def _font_supports_character(font_path: Path, character: str) -> bool:
    """Return whether the font advertises a glyph for the given character."""
    supported_codepoints = _FONT_CODEPOINT_CACHE.get(font_path)
    if supported_codepoints is None:
        font = FontToolsTTFont(str(font_path), fontNumber=0, lazy=True)
        try:
            supported_codepoints = set((font.getBestCmap() or {}).keys())
        finally:
            font.close()
        _FONT_CODEPOINT_CACHE[font_path] = supported_codepoints
    return ord(character) in supported_codepoints


def _embed_pdf_source_text(path: Path, *, title: str, text: str) -> None:
    """Attach the exact source text to the PDF metadata when pypdf is available."""
    if PdfReader is None or PdfWriter is None:
        return

    reader = PdfReader(str(path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    writer.add_metadata(
        {
            "/Title": title,
            PDF_SOURCE_TEXT_METADATA_KEY: text,
        }
    )

    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("wb") as handle:
        writer.write(handle)
    temp_path.replace(path)
