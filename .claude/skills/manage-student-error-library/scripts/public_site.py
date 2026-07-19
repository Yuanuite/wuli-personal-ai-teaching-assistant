#!/usr/bin/env python3
"""Prepare and publish privacy-reviewed, read-only student-site artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import kb
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
COMMON_FILES = ("index.html", "viewer.html", "assets/site.css", "assets/site.js")
IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)(\{width=\d+%\})?')
# Match any <script type="application/json"> tag — ID-agnostic, works with all templates
_JSON_SCRIPT_RE = re.compile(
    r'(<script\b[^>]*\btype=["\']application/json["\'][^>]*>)(.*?)(</script>)',
    re.IGNORECASE | re.DOTALL,
)
_ENTRY_ID_LINE_RE = re.compile(r'^题目编号：`[a-f0-9]{32,}[-][a-f0-9]+`\s*$', re.MULTILINE)
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


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
        return [round(left / image.width, 6), round(top / image.height, 6), round((right - left) / image.width, 6), round((bottom - top) / image.height, 6)]


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
        page_record = {"source": relative, "include": include, "crop": crop, "redactions": redactions, "source_digest": kb.sha256_file(source)}
        if include:
            output_index += 1
            output_name = f"question-{output_index}.webp"
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                if image.width * image.height > 80_000_000:
                    raise ValueError("题图像素过大，无法安全生成公开副本")
                x, y, width, height = crop
                bounds = (round(x * image.width), round(y * image.height), round((x + width) * image.width), round((y + height) * image.height))
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
    return [entry / PUBLIC_IMAGE_DIR / str(page["output"]) for page in record.get("pages", []) if page.get("include") and page.get("output")]


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


def initialize_site(site: Path = DEFAULT_SITE) -> dict[str, Any]:
    _copy_common_site(site)
    catalog = site / "catalog.json"
    if not catalog.exists():
        write_json(catalog, {"schema_version": 1, "generated_at": now_iso(), "questions": []})
    (site / "questions").mkdir(exist_ok=True)
    return {"status": "initialized", "site": str(site), "catalog": str(catalog)}


def _safe_svg(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    forbidden = ("<script", "javascript:", "<foreignobject")
    if any(token in text for token in forbidden):
        raise ValueError(f"unsafe SVG cannot be published: {path.name}")
    if re.search(r"\bon[a-z]+\s*=", text):
        raise ValueError(f"unsafe SVG event handler cannot be published: {path.name}")
    if re.search(r"\b(?:href|src)\s*=\s*['\"]\s*(?:https?:|//|data:)", text):
        raise ValueError(f"external SVG resource cannot be published: {path.name}")


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
    question_images = "\n\n".join(f"![公开题图 第 {index} 页](assets/question-{index}.webp)" for index in range(1, len(public_questions) + 1))
    if combined.lstrip().startswith("#"):
        first, separator, rest = combined.partition("\n")
        combined = f"{first}\n\n{question_images}\n\n{rest.lstrip()}" if question_images else combined
    else:
        combined = f"# {title}\n\n{question_images}\n\n{combined}" if question_images else f"# {title}\n\n{combined}"
    for index, source in enumerate(public_questions, 1):
        copied[source] = f"question-{index}.webp"
    return combined.rstrip() + "\n", [(source, name) for source, name in copied.items()]


def _generate_pdf(question_dir: Path) -> dict[str, Any]:
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        return {"status": "skipped", "reason": "pandoc or xelatex not found"}
    output = question_dir / "answer.pdf"
    last_error = ""
    for font in ("STSong", "Heiti SC", "PingFang SC"):
        result = subprocess.run(
            [
                pandoc,
                "content.md",
                "-o",
                "answer.pdf",
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
            cwd=question_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0 and output.is_file():
            return {"status": "generated", "file": "answer.pdf", "size": output.stat().st_size}
        last_error = (result.stderr or result.stdout or "PDF generation failed").strip()[-800:]
    output.unlink(missing_ok=True)
    return {"status": "skipped", "reason": last_error or "PDF generation failed"}


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
    if not match:
        raise ValueError("approved simulator has no sanitizable physics model")
    model["entry_id"] = identifier
    model["source"] = {"publication": "student-site"}
    model.pop("teacher_audit", None)
    public_model = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    html = html[:match.start()] + match.group(1) + public_model + match.group(3) + html[match.end():]
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
        shutil.copy2(source, assets_dir / name)
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
        "pdf": f"questions/{identifier}/answer.pdf" if pdf.get("status") == "generated" else None,
        "simulation": f"questions/{identifier}/simulation.html" if simulator else None,
        "published_at": now_iso(),
    }
    return {"item": item, "pdf": pdf, "assets": [name for _, name in assets]}


def audit_public_tree(root: Path, entry: Path) -> list[str]:
    errors: list[str] = []
    identifier = public_id(entry)
    question_root = root / "questions" / identifier
    allowed_question_files = {"content.md", "answer.pdf", "simulation.html"}
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


def publish_prepared(library: Path, entry_id: str, reviewer: str, note: str, site: Path = DEFAULT_SITE) -> dict[str, Any]:
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
    questions.sort(key=lambda question: str(question.get("published_at", "")), reverse=True)
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
    else:
        result = publication_snapshot(library / "entries" / args.entry_id, site)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
