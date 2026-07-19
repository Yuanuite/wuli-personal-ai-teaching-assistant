#!/usr/bin/env python3
"""Robust Markdown-to-PDF export helpers for local student artifacts."""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\{width=\d+%\})?")
_PANDOC_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\([^)\s]+\))(?!\{width=)")


def generate_markdown_pdf(
    markdown_path: Path,
    pdf_path: Path,
    *,
    success_status: str = "generated",
    prefer_pandoc: bool = True,
) -> dict[str, Any]:
    """Generate a PDF from Markdown, falling back to reportlab when LaTeX is unavailable.

    The fallback intentionally supports the subset used by student exports: headings,
    paragraphs, separators, TeX-like formulas as readable text, and local images.
    """
    markdown_path = markdown_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pandoc_error = ""
    if prefer_pandoc:
        pandoc_result = _try_pandoc(markdown_path, pdf_path, success_status)
        if pandoc_result.get("status") == success_status:
            return pandoc_result
        pandoc_error = str(pandoc_result.get("reason") or pandoc_result.get("stderr") or "")
    fallback = _try_reportlab(markdown_path, pdf_path, success_status)
    if fallback.get("status") == success_status:
        if pandoc_error:
            fallback["fallback_from"] = pandoc_error[:500]
        return fallback
    if pandoc_error:
        fallback["pandoc_error"] = pandoc_error[:500]
    return fallback


_PANDOC_SAFE_EXTENSIONS = {".webp", ".svg", ".gif", ".bmp", ".tiff", ".tif"}

def _prerender_svgs(markdown: str, base: Path) -> tuple[str, list[Path]]:
    """Pre-render images that pandoc/xelatex cannot reliably handle to 1200px-wide PNGs."""
    rsvg = shutil.which("rsvg-convert")
    rendered: list[Path] = []
    try:
        from PIL import Image as PILImage
    except Exception:
        PILImage = None

    def _replace(m: re.Match) -> str:
        target = m.group(2).split("#", 1)[0]
        source = base / target
        if not source.is_file():
            return m.group(0)
        suffix = source.suffix.lower()
        png = source.with_suffix(".pdfgen.png")
        ok = False
        if suffix == ".svg" and rsvg:
            res = subprocess.run(
                [rsvg, "-f", "png", "-w", "1200", "-o", str(png), str(source)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            ok = res.returncode == 0 and png.is_file()
        elif suffix in _PANDOC_SAFE_EXTENSIONS and PILImage:
            try:
                with PILImage.open(source) as img:
                    img.load()
                    w, h = img.size
                    if w > 1600:
                        img = img.resize((1200, int(h * 1200 / w)), PILImage.LANCZOS)
                    img.convert("RGB").save(png)
                ok = png.is_file()
            except Exception:
                pass
        if not ok:
            return m.group(0)
        old = m.group(0)
        rendered.append(png)
        return old.replace(target, str(png.relative_to(base)))

    return IMAGE_RE.sub(_replace, markdown), rendered


def _try_pandoc(markdown_path: Path, pdf_path: Path, success_status: str) -> dict[str, Any]:
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        return {"status": "skipped", "reason": "pandoc or xelatex not found"}
    raw = markdown_path.read_text(encoding="utf-8")
    # Pre-render SVGs to PNG to avoid pandoc producing huge intermediate bitmaps
    raw, svg_rendered = _prerender_svgs(raw, markdown_path.parent)
    # Add width limit to images to prevent xelatex "Dimension too large"
    raw = _PANDOC_IMAGE_RE.sub(r"\1{width=90%}", raw)
    tmp_md = markdown_path.with_suffix(".pandoc.md")
    tmp_md.write_text(raw, encoding="utf-8")
    last_error = ""
    try:
        for font in ("STSong", "Heiti SC", "PingFang SC", "Arial Unicode MS"):
            result = subprocess.run(
                [
                    pandoc,
                    tmp_md.name,
                    "-o",
                    pdf_path.name,
                    "--pdf-engine=xelatex",
                    "-V",
                    f"mainfont={font}",
                    "-V",
                    f"CJKmainfont={font}",
                    "-V",
                    "geometry:margin=2cm",
                    "-V",
                    "fontsize=11pt",
                    "-V",
                    "linestretch=1.25",
                    "-V",
                    "colorlinks=true",
                    "--from",
                    "markdown+tex_math_dollars+raw_tex",
                    "--standalone",
                ],
                cwd=markdown_path.parent,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0 and pdf_path.is_file():
                return {
                    "status": success_status,
                    "file": pdf_path.name,
                    "path": str(pdf_path),
                    "size": pdf_path.stat().st_size,
                    "engine": "pandoc-xelatex",
                    "font": font,
                }
            last_error = (result.stderr or result.stdout or "PDF generation failed").strip()[-800:]
            pdf_path.unlink(missing_ok=True)
        return {"status": "skipped", "reason": last_error or "PDF generation failed", "engine": "pandoc-xelatex"}
    finally:
        tmp_md.unlink(missing_ok=True)
        for png in svg_rendered:
            png.unlink(missing_ok=True)


def _try_reportlab(markdown_path: Path, pdf_path: Path, success_status: str) -> dict[str, Any]:
    try:
        from PIL import Image as PILImage
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer
    except Exception:
        _add_optional_python_package_paths()
        try:
            from PIL import Image as PILImage
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer
        except Exception as exc:  # pragma: no cover - dependency absence is environment-specific
            return {"status": "skipped", "reason": f"reportlab/Pillow unavailable: {exc}", "engine": "reportlab"}

    font_name = _register_cjk_font(pdfmetrics, TTFont)
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "WuliNormal",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10.5,
        leading=17,
        spaceAfter=7,
    )
    title = ParagraphStyle(
        "WuliTitle",
        parent=normal,
        fontSize=18,
        leading=24,
        spaceAfter=14,
        alignment=TA_CENTER,
    )
    heading = ParagraphStyle("WuliHeading", parent=normal, fontSize=14, leading=20, spaceBefore=10, spaceAfter=8)
    subheading = ParagraphStyle("WuliSubheading", parent=normal, fontSize=12, leading=18, spaceBefore=8, spaceAfter=6)
    formula = ParagraphStyle(
        "WuliFormula",
        parent=normal,
        fontName=font_name,
        backColor=colors.HexColor("#f6f7fb"),
        borderColor=colors.HexColor("#d9dfec"),
        borderWidth=0.5,
        borderPadding=6,
        leftIndent=8,
        rightIndent=8,
    )
    caption = ParagraphStyle("WuliCaption", parent=normal, fontSize=9, textColor=colors.HexColor("#606a78"), alignment=TA_CENTER)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title=markdown_path.stem,
    )
    max_width = A4[0] - doc.leftMargin - doc.rightMargin
    story: list[Any] = []
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory(prefix="wuli-pdf-assets-") as tmp:
        tmp_dir = Path(tmp)
        for block in _markdown_blocks(text):
            image = IMAGE_RE.fullmatch(block.strip())
            if image:
                alt, target = image.group(1), image.group(2)
                image_path = _resolve_asset(markdown_path.parent, target)
                rendered = _render_image_for_reportlab(image_path, tmp_dir, PILImage) if image_path else None
                if rendered:
                    width, height = _scaled_image_size(rendered, max_width, 9 * cm, PILImage)
                    story.append(Image(str(rendered), width=width, height=height))
                    if alt:
                        story.append(Paragraph(_escape_inline(alt), caption))
                    story.append(Spacer(1, 8))
                else:
                    story.append(Paragraph(f"图像：{_escape_inline(alt or target)}", caption))
                continue
            stripped = block.strip()
            if stripped in {"---", "***", "___"}:
                story.append(HRFlowable(width="100%", color=colors.HexColor("#d8deea"), thickness=0.8, spaceBefore=8, spaceAfter=12))
                continue
            if stripped.startswith("# "):
                story.append(Paragraph(_escape_inline(stripped[2:].strip()), title))
            elif stripped.startswith("## "):
                story.append(Paragraph(_escape_inline(stripped[3:].strip()), heading))
            elif stripped.startswith("### "):
                story.append(Paragraph(_escape_inline(stripped[4:].strip()), subheading))
            elif stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
                story.append(Paragraph(_escape_inline(stripped), formula))
            else:
                story.append(Paragraph(_escape_inline(_clean_markdown_inline(stripped)), normal))
        try:
            doc.build(story)
        except Exception as exc:
            pdf_path.unlink(missing_ok=True)
            return {"status": "skipped", "reason": f"reportlab failed: {exc}", "engine": "reportlab"}
    if not pdf_path.is_file():
        return {"status": "skipped", "reason": "reportlab did not create PDF", "engine": "reportlab"}
    return {
        "status": success_status,
        "file": pdf_path.name,
        "path": str(pdf_path),
        "size": pdf_path.stat().st_size,
        "engine": "reportlab",
        "font": font_name,
    }


def _register_cjk_font(pdfmetrics: Any, TTFont: Any) -> str:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            name = f"WuliCJK{abs(hash(str(path))) % 100000}"
            pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
        except Exception:
            continue
    return "Helvetica"


def _add_optional_python_package_paths() -> None:
    candidates: list[Path] = []
    env_path = os.environ.get("WULI_PYTHON_PACKAGES")
    if env_path:
        candidates.append(Path(env_path))
    for part in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(part)
        marker = Path("codex-primary-runtime") / "dependencies" / "bin"
        if marker.as_posix() in path.as_posix():
            dependency_root = path
            while dependency_root.name != "dependencies" and dependency_root.parent != dependency_root:
                dependency_root = dependency_root.parent
            candidates.append(dependency_root / "python")
    candidates.append(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python")
    expanded: list[Path] = []
    for path in candidates:
        expanded.append(path)
        expanded.extend(sorted((path / "lib").glob("python*/site-packages")) if (path / "lib").is_dir() else [])
    for path in expanded:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []
    in_formula = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("$$"):
            if paragraph:
                blocks.append(" ".join(paragraph))
                paragraph = []
            paragraph.append(line.strip())
            in_formula = not (line.strip().endswith("$$") and line.strip() != "$$")
            if not in_formula:
                blocks.append("\n".join(paragraph))
                paragraph = []
            continue
        if in_formula:
            paragraph.append(line.strip())
            if line.strip().endswith("$$"):
                blocks.append("\n".join(paragraph))
                paragraph = []
                in_formula = False
            continue
        if not line.strip():
            if paragraph:
                blocks.append(" ".join(paragraph))
                paragraph = []
            continue
        if IMAGE_RE.fullmatch(line.strip()) or line.strip() in {"---", "***", "___"} or line.startswith("#"):
            if paragraph:
                blocks.append(" ".join(paragraph))
                paragraph = []
            blocks.append(line.strip())
        else:
            paragraph.append(line.strip())
    if paragraph:
        blocks.append(" ".join(paragraph))
    return blocks


def _resolve_asset(base: Path, target: str) -> Path | None:
    clean = target.split("#", 1)[0]
    path = (base / clean).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _render_image_for_reportlab(path: Path, tmp_dir: Path, PILImage: Any) -> Path | None:
    if path.suffix.lower() == ".svg":
        converter = shutil.which("rsvg-convert")
        if not converter:
            return None
        output = tmp_dir / f"{path.stem}.png"
        result = subprocess.run([converter, "-f", "png", "-o", str(output), str(path)], capture_output=True, text=True, timeout=30, check=False)
        return output if result.returncode == 0 and output.is_file() else None
    try:
        with PILImage.open(path) as image:
            image.load()
            output = tmp_dir / f"{path.stem}.png"
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.save(output)
            return output
    except Exception:
        return None


def _scaled_image_size(path: Path, max_width: float, max_height: float, PILImage: Any) -> tuple[float, float]:
    with PILImage.open(path) as image:
        width, height = image.size
    scale = min(max_width / max(width, 1), max_height / max(height, 1), 1.0)
    return width * scale, height * scale


def _clean_markdown_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    return text


def _escape_inline(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")
