#!/usr/bin/env python3
"""Prepare and publish privacy-reviewed, read-only student-site artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import kb
import pdf_export
import process_uploads
from PIL import Image, ImageChops, ImageOps

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
DEFAULT_SITE = PROJECT_ROOT / "student-site"
TEACHER_VENDOR = PROJECT_ROOT / "teacher-console" / "static" / "vendor"
DRAFT_DIR = "publication-draft"
DRAFT_RECORD = "publication-draft.json"
REVIEW_RECORD = "publication-review.json"
PUBLIC_IMAGE_DIR = "publication-assets"
PUBLIC_IMAGE_RECORD = "publication-images.json"
PUBLIC_PDF_NAME = "带答案错题.pdf"
COMMON_FILES = ("index.html", "viewer.html", "assets/site.css", "assets/site.js")
IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)(\{width=\d+%\})?')
# Match any <script type="application/json"> tag — ID-agnostic, works with all templates
_JSON_SCRIPT_RE = re.compile(
    r'(<script\b[^>]*\btype=["\']application/json["\'][^>]*>)(.*?)(</script>)',
    re.IGNORECASE | re.DOTALL,
)
_ENTRY_ID_LINE_RE = re.compile(
    r"^\s*(?:题目编号|条目编号|Entry ID|entry_id)\s*[:：]\s*`?[^`\n]+`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
TEXT_EXTENSIONS = {".html", ".js", ".json", ".md", ".css", ".svg"}
PUBLIC_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
FORBIDDEN_TEXT = (
    "student-error-library",
    "teacher-solution",
    "教师版解析",
    "record.json",
    "pipeline.json",
    "delivery-manifest.json",
    "source-review",
    "answer-review",
    "visualization-review",
    "physics-model.json",
)
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_MAX_BYTES = 2 * 1024 * 1024
SVG_MAX_ELEMENTS = 20_000
SVG_MAX_DEPTH = 128
SVG_MAX_TEXT = 200_000
SVG_ALLOWED_ELEMENTS = {
    "circle",
    "clipPath",
    "defs",
    "desc",
    "ellipse",
    "g",
    "line",
    "linearGradient",
    "marker",
    "mask",
    "path",
    "pattern",
    "polygon",
    "polyline",
    "radialGradient",
    "rect",
    "stop",
    "style",
    "subscript",
    "svg",
    "text",
    "title",
    "tspan",
}
SVG_ALLOWED_ATTRIBUTES = {
    "aria-label",
    "aria-labelledby",
    "baseline-shift",
    "class",
    "clip-path",
    "cx",
    "cy",
    "d",
    "fill",
    "fill-opacity",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "id",
    "letter-spacing",
    "marker-end",
    "marker-mid",
    "marker-start",
    "markerHeight",
    "markerUnits",
    "markerWidth",
    "mask",
    "offset",
    "opacity",
    "orient",
    "patternContentUnits",
    "patternTransform",
    "patternUnits",
    "points",
    "preserveAspectRatio",
    "r",
    "refX",
    "refY",
    "role",
    "rx",
    "ry",
    "stop-color",
    "stop-opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "style",
    "text-anchor",
    "transform",
    "viewBox",
    "width",
    "x",
    "x1",
    "x2",
    "y",
    "y1",
    "y2",
}
SVG_LOCAL_URL_ATTRIBUTES = {"clip-path", "fill", "marker-end", "marker-mid", "marker-start", "mask", "stroke"}
SVG_FORBIDDEN_CSS = re.compile(
    r"(?:@import|@namespace|expression\s*\(|url\s*\(\s*(?![\"']?#[-A-Za-z0-9_.:]+[\"']?\s*\))"
    r"|javascript\s*:|data\s*:|https?\s*:|//|behavior\s*:|-moz-binding)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _public_date(value: Any) -> str:
    """Expose day-level ordering only; exact local upload time stays private."""
    text = str(value or "").strip()
    match = re.match(r"^\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else ""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_id(entry: Path) -> str:
    return f"question-{hashlib.sha256(entry.name.encode('utf-8')).hexdigest()[:12]}"


def _source_images(entry: Path) -> list[tuple[str, Path]]:
    record = kb.load_json(entry / "record.json", {})
    result = []
    for relative in record.get("source", {}).get("stored_files", []):
        path = entry / str(relative)
        if path.is_file() and path.suffix.lower() in kb.IMAGE_EXTENSIONS:
            result.append((str(relative), path))
    return result


def _image_id(relative: str) -> str:
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]


def _normalized_box(value: Any, *, minimum: float = 0.01) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("裁剪或遮挡区域格式错误")
    x, y, width, height = (float(item) for item in value)
    if x < 0 or y < 0 or width < minimum or height < minimum or x + width > 1.0001 or y + height > 1.0001:
        raise ValueError("裁剪或遮挡区域超出题图范围")
    return [round(max(0, min(1, item)), 6) for item in (x, y, width, height)]


def _suggest_crop(path: Path) -> list[float]:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((1200, 1200))
        background = Image.new("RGB", image.size, (255, 255, 255))
        difference = ImageChops.difference(image, background).convert("L").point(lambda value: 255 if value > 18 else 0)
        bounds = difference.getbbox()
        if not bounds:
            return [0.0, 0.0, 1.0, 1.0]
        left, top, right, bottom = bounds
        padding_x = max(8, int(image.width * 0.025))
        padding_y = max(8, int(image.height * 0.025))
        left, top = max(0, left - padding_x), max(0, top - padding_y)
        right, bottom = min(image.width, right + padding_x), min(image.height, bottom + padding_y)
        if (right - left) * (bottom - top) < image.width * image.height * 0.12:
            return [0.0, 0.0, 1.0, 1.0]
        return [
            round(left / image.width, 6),
            round(top / image.height, 6),
            round((right - left) / image.width, 6),
            round((bottom - top) / image.height, 6),
        ]


def _public_image_record_current(entry: Path, record: dict[str, Any]) -> bool:
    if record.get("status") != "passed":
        return False
    expected_sources = {relative for relative, _ in _source_images(entry)}
    recorded_sources = {str(page.get("source", "")) for page in record.get("pages", [])}
    if expected_sources != recorded_sources:
        return False
    for page in record.get("pages", []):
        source = entry / str(page.get("source", ""))
        if not source.is_file():
            return False
        if kb.sha256_file(source) != page.get("source_digest"):
            return False
        if page.get("include"):
            output = entry / PUBLIC_IMAGE_DIR / str(page.get("output", ""))
            if not output.is_file() or kb.sha256_file(output) != page.get("output_digest"):
                return False
    return True


def public_image_snapshot(entry: Path) -> dict[str, Any]:
    record = kb.load_json(entry / PUBLIC_IMAGE_RECORD, {})
    current = _public_image_record_current(entry, record)
    saved = {str(page.get("source")): page for page in record.get("pages", [])}
    sources = []
    for relative, path in _source_images(entry):
        with Image.open(path) as opened:
            width, height = ImageOps.exif_transpose(opened).size
        page = saved.get(relative, {}) if current else {}
        sources.append({
            "id": _image_id(relative),
            "relative": relative,
            "width": width,
            "height": height,
            "include": bool(page.get("include", True)),
            "crop": page.get("crop") or _suggest_crop(path),
            "redactions": page.get("redactions", []),
        })
    status = "passed" if current else ("stale" if record.get("status") == "passed" else "not-reviewed")
    return {
        "status": status,
        "sources": sources,
        "reviewer": record.get("reviewer"),
        "reviewed_at": record.get("reviewed_at"),
        "included_count": sum(1 for page in record.get("pages", []) if page.get("include")) if current else 0,
    }


def save_public_images(entry: Path, pages: Any, reviewer: str, note: str) -> dict[str, Any]:
    if not reviewer.strip():
        raise ValueError("公开题图必须记录复核人")
    if not isinstance(pages, list):
        raise ValueError("公开题图配置格式错误")
    allowed = {_image_id(relative): (relative, path) for relative, path in _source_images(entry)}
    submitted = {str(page.get("source_id")): page for page in pages if isinstance(page, dict)}
    output_dir = entry / PUBLIC_IMAGE_DIR
    work_dir = entry / f".{PUBLIC_IMAGE_DIR}.tmp"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True)
    records = []
    output_index = 0
    for source_id, (relative, source) in allowed.items():
        spec = submitted.get(source_id, {})
        include = bool(spec.get("include", False))
        crop = _normalized_box(spec.get("crop", [0, 0, 1, 1]), minimum=0.08)
        redactions = [_normalized_box(box, minimum=0.005) for box in spec.get("redactions", [])]
        page_record = {
            "source": relative,
            "include": include,
            "crop": crop,
            "redactions": redactions,
            "source_digest": kb.sha256_file(source),
        }
        if include:
            output_index += 1
            output_name = f"question-{output_index}.webp"
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                if image.width * image.height > 80_000_000:
                    raise ValueError("题图像素过大，无法安全生成公开副本")
                x, y, width, height = crop
                bounds = (
                    round(x * image.width),
                    round(y * image.height),
                    round((x + width) * image.width),
                    round((y + height) * image.height),
                )
                image = image.crop(bounds)
                image = ImageOps.autocontrast(image, cutoff=0.4)
                for rx, ry, rw, rh in redactions:
                    left, top = round(rx * image.width), round(ry * image.height)
                    right, bottom = round((rx + rw) * image.width), round((ry + rh) * image.height)
                    image.paste((246, 243, 235), (left, top, right, bottom))
                if image.width > 2200:
                    image.thumbnail((2200, 4000), Image.Resampling.LANCZOS)
                target = work_dir / output_name
                image.save(target, "WEBP", quality=90, method=6)
            page_record.update({"output": output_name, "output_digest": kb.sha256_file(work_dir / output_name)})
        records.append(page_record)
    report = {
        "schema_version": 1,
        "status": "passed",
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "reviewed_at": now_iso(),
        "pages": records,
    }
    shutil.rmtree(output_dir, ignore_errors=True)
    work_dir.rename(output_dir)
    write_json(entry / PUBLIC_IMAGE_RECORD, report)
    shutil.rmtree(entry / DRAFT_DIR, ignore_errors=True)
    (entry / DRAFT_RECORD).unlink(missing_ok=True)
    return {"status": "passed", "included_count": output_index, "reviewed_at": report["reviewed_at"]}


def approved_public_images(entry: Path) -> list[Path]:
    record = kb.load_json(entry / PUBLIC_IMAGE_RECORD, {})
    if not _public_image_record_current(entry, record):
        return []
    return [
        entry / PUBLIC_IMAGE_DIR / str(page["output"])
        for page in record.get("pages", [])
        if page.get("include") and page.get("output")
    ]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_common_site(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative in COMMON_FILES:
        source = DEFAULT_SITE / relative
        if not source.is_file():
            raise FileNotFoundError(f"student-site scaffold is missing: {relative}")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
    if not TEACHER_VENDOR.is_dir():
        raise FileNotFoundError("teacher-console Markdown vendor assets are missing")
    shutil.copytree(TEACHER_VENDOR, target / "vendor", dirs_exist_ok=True)


def _public_difficulty(record: dict[str, Any]) -> dict[str, Any] | None:
    """Project the reviewed difficulty into the deliberately narrow public shape."""
    assessment = record.get("difficulty_assessment", {})
    if not isinstance(assessment, dict):
        return None
    public_dimensions = []
    for dimension in assessment.get("dimensions", []) if isinstance(assessment.get("dimensions"), list) else []:
        if not isinstance(dimension, dict):
            continue
        public_dimensions.append({
            "id": str(dimension.get("id", ""))[:64],
            "label": str(dimension.get("label", ""))[:40],
            "score": max(0, min(5, float(dimension.get("score", 0)))),
            "core_judgment": str(dimension.get("core_judgment", ""))[:180],
        })
    score = assessment.get("score")
    if not public_dimensions or not isinstance(score, (int, float)):
        return None
    return {
        "score": int(score),
        "level": str(assessment.get("level", ""))[:20],
        "summary": str(assessment.get("summary", ""))[:300],
        "dimensions": public_dimensions,
    }


def initialize_site(site: Path = DEFAULT_SITE) -> dict[str, Any]:
    _copy_common_site(site)
    catalog = site / "catalog.json"
    if not catalog.exists():
        write_json(catalog, {"schema_version": 1, "generated_at": now_iso(), "questions": []})
    (site / "questions").mkdir(exist_ok=True)
    return {"status": "initialized", "site": str(site), "catalog": str(catalog)}


def _safe_svg(path: Path) -> None:
    """Fail closed unless *path* is a bounded, passive SVG document.

    This is deliberately a parser-backed allowlist.  String blacklists miss
    namespaces, character references and newly introduced active SVG elements.
    """
    if path.stat().st_size > SVG_MAX_BYTES:
        raise ValueError(f"SVG is too large to publish safely: {path.name}")
    raw = path.read_bytes()
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.IGNORECASE):
        raise ValueError(f"SVG document types and entities are not allowed: {path.name}")
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ValueError(f"invalid SVG cannot be published: {path.name}") from exc

    element_count = 0
    text_count = 0
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        element_count += 1
        text_count += len(element.text or "") + len(element.tail or "")
        if element_count > SVG_MAX_ELEMENTS or depth > SVG_MAX_DEPTH or text_count > SVG_MAX_TEXT:
            raise ValueError(f"SVG exceeds safe complexity limits: {path.name}")

        namespace, local_name = _svg_name(element.tag)
        if namespace not in {"", SVG_NAMESPACE} or local_name not in SVG_ALLOWED_ELEMENTS:
            raise ValueError(f"SVG element is not allowed: {local_name or element.tag}")
        for raw_name, value in element.attrib.items():
            attribute_namespace, attribute = _svg_name(raw_name)
            if attribute_namespace or attribute not in SVG_ALLOWED_ATTRIBUTES or attribute.lower().startswith("on"):
                raise ValueError(f"SVG attribute is not allowed: {attribute or raw_name}")
            _validate_svg_attribute(attribute, value, path)
        if local_name == "style":
            _validate_svg_css(element.text or "", path)
        stack.extend((child, depth + 1) for child in list(element))


def _svg_name(name: Any) -> tuple[str, str]:
    if not isinstance(name, str):
        return "unsupported", ""
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return "", name


def _validate_svg_css(value: str, path: Path) -> None:
    if len(value) > 100_000 or SVG_FORBIDDEN_CSS.search(value):
        raise ValueError(f"unsafe SVG CSS cannot be published: {path.name}")
    if any(character in value for character in ("\x00", "\r")):
        raise ValueError(f"invalid SVG CSS cannot be published: {path.name}")


def _validate_svg_attribute(name: str, value: str, path: Path) -> None:
    if len(value) > 20_000 or any(character in value for character in ("\x00", "\r", "\n", "<", ">")):
        raise ValueError(f"invalid SVG attribute cannot be published: {name}")
    lowered = value.casefold()
    if any(token in lowered for token in ("javascript:", "data:", "vbscript:", "file:", "https:", "http:", "//")):
        raise ValueError(f"external SVG value cannot be published: {path.name}")
    if name == "style":
        _validate_svg_css(value, path)
    if name in SVG_LOCAL_URL_ATTRIBUTES and "url(" in lowered:
        if not re.fullmatch(r"\s*url\(\s*[\"']?#[A-Za-z0-9_.:-]+[\"']?\s*\)\s*", value, re.IGNORECASE):
            raise ValueError(f"only local SVG fragment references are allowed: {path.name}")


def _copy_public_asset(source: Path, destination: Path, entry: Path, identifier: str) -> None:
    if source.suffix.lower() != ".svg":
        shutil.copy2(source, destination)
        return
    _safe_svg(source)
    text = source.read_text(encoding="utf-8", errors="strict")
    text = text.replace(entry.name, identifier)
    destination.write_text(text, encoding="utf-8")


def _public_markdown(entry: Path) -> tuple[str, list[tuple[Path, str]]]:
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8")
    answer_path = entry / "student-solution.md"
    if not answer_path.is_file():
        raise FileNotFoundError("student-solution.md is missing")
    answer = answer_path.read_text(encoding="utf-8")
    source_files = {str(Path(item)) for item in record.get("source", {}).get("stored_files", [])}
    copied: dict[Path, str] = {}

    def rewrite(match: re.Match[str]) -> str:
        alt, target, width = match.group(1), match.group(2), match.group(3) or ""
        clean_target = target.split("#", 1)[0]
        relative = Path(clean_target)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "assets":
            return ""
        normalized = str(relative)
        source = (entry / relative).resolve()
        try:
            source.relative_to((entry / "assets").resolve())
        except ValueError:
            return ""
        if normalized in source_files or source.name.lower().startswith("original.") or not source.is_file():
            return ""
        if source.suffix.lower() not in PUBLIC_IMAGE_EXTENSIONS:
            return ""
        if source.suffix.lower() == ".svg":
            _safe_svg(source)
        if source not in copied:
            copied[source] = f"asset-{len(copied) + 1}{source.suffix.lower()}"
        return f"![{alt}](assets/{copied[source]}){width}"

    problem = _ENTRY_ID_LINE_RE.sub("", problem)
    answer = _ENTRY_ID_LINE_RE.sub("", answer)
    combined = f"{problem.rstrip()}\n\n---\n\n{answer.lstrip()}"
    combined = IMAGE_RE.sub(rewrite, combined)
    title = str(record.get("title") or entry.name).strip()
    public_questions = approved_public_images(entry)
    question_images = "\n\n".join(
        f"![公开题图 第 {index} 页](assets/question-{index}.webp)" for index in range(1, len(public_questions) + 1)
    )
    if combined.lstrip().startswith("#"):
        first, separator, rest = combined.partition("\n")
        combined = f"{first}\n\n{question_images}\n\n{rest.lstrip()}" if question_images else combined
    else:
        combined = f"# {title}\n\n{question_images}\n\n{combined}" if question_images else f"# {title}\n\n{combined}"
    for index, source in enumerate(public_questions, 1):
        copied[source] = f"question-{index}.webp"
    return combined.rstrip() + "\n", [(source, name) for source, name in copied.items()]


def _generate_pdf(question_dir: Path) -> dict[str, Any]:
    return pdf_export.generate_markdown_pdf(
        question_dir / "content.md", question_dir / PUBLIC_PDF_NAME, success_status="generated"
    )


def _approved_simulator(entry: Path) -> Path | None:
    snapshot = process_uploads.visualization_snapshot(entry)
    review = snapshot.get("review", {})
    if (
        review.get("status") != "passed"
        or snapshot.get("review_stale")
        or not snapshot.get("build_current")
        or review.get("artifact_digest") != snapshot.get("artifact_digest")
    ):
        return None
    simulator = entry / process_uploads.VISUALIZATION_DIR / "physics-simulator.html"
    return simulator if simulator.is_file() else None


def _copy_public_simulator(source: Path, destination: Path, identifier: str) -> None:
    html = source.read_text(encoding="utf-8")
    # Find the first JSON script tag that contains a physics model (schema_version + model_type)
    match = None
    model = None
    for m in _JSON_SCRIPT_RE.finditer(html):
        try:
            candidate = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "schema_version" in candidate and "model_type" in candidate:
            match = m
            model = candidate
            break
    if not match or model is None:
        raise ValueError("approved simulator has no sanitizable physics model")
    model["entry_id"] = identifier
    model["source"] = {"publication": "student-site"}
    model.pop("teacher_audit", None)
    public_model = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    html = html[: match.start()] + match.group(1) + public_model + match.group(3) + html[match.end() :]
    html = html.replace("physics-model.json", "统一物理模型")
    destination.write_text(html, encoding="utf-8")


def _write_question(entry: Path, root: Path) -> dict[str, Any]:
    identifier = public_id(entry)
    question_dir = root / "questions" / identifier
    if question_dir.exists():
        shutil.rmtree(question_dir)
    assets_dir = question_dir / "assets"
    assets_dir.mkdir(parents=True)
    markdown, assets = _public_markdown(entry)
    (question_dir / "content.md").write_text(markdown, encoding="utf-8")
    for source, name in assets:
        _copy_public_asset(source, assets_dir / name, entry, identifier)
    if not assets:
        assets_dir.rmdir()
    pdf = _generate_pdf(question_dir)
    simulator = _approved_simulator(entry)
    if simulator:
        _copy_public_simulator(simulator, question_dir / "simulation.html", identifier)
    record = kb.load_json(entry / "record.json", {})
    item = {
        "id": identifier,
        "title": str(record.get("title") or "物理错题"),
        "subject": str(record.get("subject") or "高中物理"),
        "knowledge_points": list(record.get("knowledge_points", []))[:6],
        "content": f"questions/{identifier}/content.md",
        "pdf": f"questions/{identifier}/{PUBLIC_PDF_NAME}" if pdf.get("status") == "generated" else None,
        "simulation": f"questions/{identifier}/simulation.html" if simulator else None,
        "uploaded_at": _public_date(record.get("created_at") or record.get("updated_at")),
        "published_at": now_iso(),
        "difficulty": _public_difficulty(record),
    }
    return {"item": item, "pdf": pdf, "assets": [name for _, name in assets]}


def audit_public_tree(root: Path, entry: Path) -> list[str]:
    errors: list[str] = []
    identifier = public_id(entry)
    question_root = root / "questions" / identifier
    allowed_question_files = {"content.md", PUBLIC_PDF_NAME, "answer.pdf", "simulation.html"}
    for path in sorted(item for item in question_root.rglob("*") if item.is_file()):
        relative = path.relative_to(question_root)
        if relative.parts[0] != "assets" and str(relative) not in allowed_question_files:
            errors.append(f"unexpected public file: {relative}")
        if relative.parts[0] == "assets" and path.suffix.lower() not in PUBLIC_IMAGE_EXTENSIONS:
            errors.append(f"unsupported public asset: {relative}")
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        for token in (*FORBIDDEN_TEXT, entry.name, str(PROJECT_ROOT)):
            if token.lower() in lowered:
                errors.append(f"private reference {token!r} found in {relative}")
    return sorted(set(errors))


def prepare_publication(library: Path, entry_id: str, site: Path = DEFAULT_SITE) -> dict[str, Any]:
    entry = library / "entries" / entry_id
    if not entry.is_dir():
        raise FileNotFoundError(entry_id)
    pipeline = kb.load_json(entry / "pipeline.json", {})
    delivery = kb.load_json(entry / "delivery.json", {})
    if pipeline.get("state") != "delivered" or delivery.get("status") != "delivered":
        raise ValueError("只能为已交付的题目生成学生端公开预览")
    image_snapshot = public_image_snapshot(entry)
    if image_snapshot["sources"] and image_snapshot["status"] != "passed":
        raise ValueError("请先裁剪、脱敏并确认公开题图")
    initialize_site(site)
    draft = entry / DRAFT_DIR
    if draft.exists():
        shutil.rmtree(draft)
    _copy_common_site(draft)
    result = _write_question(entry, draft)
    write_json(draft / "catalog.json", {"schema_version": 1, "generated_at": now_iso(), "questions": [result["item"]]})
    errors = audit_public_tree(draft, entry)
    if errors:
        shutil.rmtree(draft)
        raise ValueError("；".join(errors))
    digest = tree_digest(draft)
    report = {
        "schema_version": 1,
        "entry_id": entry.name,
        "status": "prepared",
        "prepared_at": now_iso(),
        "public_id": result["item"]["id"],
        "artifact_digest": digest,
        "pdf": result["pdf"],
        "has_simulation": bool(result["item"]["simulation"]),
        "public_question_images": image_snapshot.get("included_count", 0),
        "privacy_defaults": {"original_source_included": False, "teacher_solution_included": False},
    }
    write_json(entry / DRAFT_RECORD, report)
    return report


def publish_prepared(
    library: Path, entry_id: str, reviewer: str, note: str, site: Path = DEFAULT_SITE
) -> dict[str, Any]:
    entry = library / "entries" / entry_id
    draft = entry / DRAFT_DIR
    prepared = kb.load_json(entry / DRAFT_RECORD, {})
    if prepared.get("status") != "prepared" or not draft.is_dir():
        raise ValueError("请先生成学生端公开预览")
    if not reviewer.strip():
        raise ValueError("公开发布必须记录复核人")
    current_digest = tree_digest(draft)
    if current_digest != prepared.get("artifact_digest"):
        raise ValueError("公开预览已发生变化，请重新生成并复核")
    errors = audit_public_tree(draft, entry)
    if errors:
        raise ValueError("；".join(errors))
    initialize_site(site)
    identifier = str(prepared["public_id"])
    target = site / "questions" / identifier
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(draft / "questions" / identifier, target)
    catalog_path = site / "catalog.json"
    catalog = kb.load_json(catalog_path, {"schema_version": 1, "questions": []})
    draft_catalog = kb.load_json(draft / "catalog.json", {"questions": []})
    item = dict(draft_catalog["questions"][0])
    item["published_at"] = now_iso()
    questions = [question for question in catalog.get("questions", []) if question.get("id") != identifier]
    questions.append(item)
    questions.sort(
        key=lambda question: (
            str(question.get("uploaded_at") or question.get("published_at") or ""),
            str(question.get("published_at") or ""),
        ),
        reverse=True,
    )
    write_json(catalog_path, {"schema_version": 1, "generated_at": now_iso(), "questions": questions})
    review = {
        "schema_version": 1,
        "entry_id": entry.name,
        "status": "published-local",
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "approved_at": now_iso(),
        "public_id": identifier,
        "artifact_digest": current_digest,
        "git_status": "not-pushed",
    }
    write_json(entry / REVIEW_RECORD, review)
    return review


def sync_published_difficulties(library: Path, site: Path = DEFAULT_SITE) -> dict[str, Any]:
    """Refresh only difficulty metadata for entries already approved and published locally."""
    catalog_path = site / "catalog.json"
    catalog = kb.load_json(catalog_path, {})
    questions = catalog.get("questions")
    if not isinstance(questions, list):
        raise ValueError("学生端题库清单格式错误")

    approved_entries: dict[str, Path] = {}
    entries_root = library / "entries"
    for entry in sorted(path for path in entries_root.iterdir() if path.is_dir()):
        review = kb.load_json(entry / REVIEW_RECORD, {})
        if review.get("status") != "published-local":
            continue
        identifier = str(review.get("public_id") or "")
        if not identifier or identifier != public_id(entry):
            raise ValueError(f"公开发布记录与条目映射不一致: {entry.name}")
        if not (site / "questions" / identifier / "content.md").is_file():
            raise ValueError(f"已批准条目缺少公开内容: {identifier}")
        approved_entries[identifier] = entry

    updated_ids: list[str] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "")
        matched_entry = approved_entries.get(identifier)
        if matched_entry is None:
            continue
        difficulty = _public_difficulty(kb.load_json(matched_entry / "record.json", {}))
        if item.get("difficulty") != difficulty:
            item["difficulty"] = difficulty
            updated_ids.append(identifier)

    if updated_ids:
        catalog["generated_at"] = now_iso()
        write_json(catalog_path, catalog)
    return {
        "status": "synced",
        "published_entries": len(approved_entries),
        "updated": len(updated_ids),
        "updated_public_ids": updated_ids,
    }


def publication_snapshot(entry: Path, site: Path = DEFAULT_SITE) -> dict[str, Any]:
    prepared = kb.load_json(entry / DRAFT_RECORD, {})
    review = kb.load_json(entry / REVIEW_RECORD, {})
    draft = entry / DRAFT_DIR
    image_snapshot = public_image_snapshot(entry)
    image_gate = not image_snapshot["sources"] or image_snapshot["status"] == "passed"
    preview_current = bool(
        prepared.get("status") == "prepared"
        and draft.is_dir()
        and tree_digest(draft) == prepared.get("artifact_digest")
        and image_gate
    )
    identifier = str(prepared.get("public_id") or review.get("public_id") or public_id(entry))
    published = (site / "questions" / identifier / "content.md").is_file()
    return {
        "status": review.get("status") if published else ("prepared" if preview_current else "not-prepared"),
        "public_id": identifier,
        "preview_ready": preview_current,
        "published_local": published,
        "prepared_at": prepared.get("prepared_at"),
        "approved_at": review.get("approved_at"),
        "reviewer": review.get("reviewer"),
        "pdf": prepared.get("pdf", {}),
        "has_simulation": bool(prepared.get("has_simulation")),
        "public_question_images": prepared.get("public_question_images", 0),
        "git_status": review.get("git_status", "not-pushed"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=PROJECT_ROOT / "student-error-library")
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("entry_id")
    publish = commands.add_parser("publish")
    publish.add_argument("entry_id")
    publish.add_argument("--reviewer", required=True)
    publish.add_argument("--note", default="")
    commands.add_parser("sync-difficulty")
    status = commands.add_parser("status")
    status.add_argument("entry_id")
    args = parser.parse_args()
    library = args.library.resolve()
    site = args.site.resolve()
    if args.command == "init":
        result = initialize_site(site)
    elif args.command == "prepare":
        result = prepare_publication(library, args.entry_id, site)
    elif args.command == "publish":
        result = publish_prepared(library, args.entry_id, args.reviewer, args.note, site)
    elif args.command == "sync-difficulty":
        result = sync_published_difficulties(library, site)
    else:
        result = publication_snapshot(library / "entries" / args.entry_id, site)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
