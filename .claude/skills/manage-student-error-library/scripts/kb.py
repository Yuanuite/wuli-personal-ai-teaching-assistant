#!/usr/bin/env python3
"""Deterministic local storage, OCR, indexing, search, and review for a student's errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pdf_export


SCHEMA_VERSION = 1
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
REQUIRED_SOLUTION_HEADINGS = ("答案速览", "详细解答", "易错点")
PENDING_MARKERS = ("TODO", "待生成", "[待核对]", "（待核对）")
SCRIPT_DIR = Path(__file__).resolve().parent
VISION_SCRIPT = SCRIPT_DIR / "vision_ocr.swift"
VISION_OBJC_SOURCE = SCRIPT_DIR / "vision_ocr.m"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def answer_artifact_digest(entry: Path) -> str:
    digest = hashlib.sha256()
    for name in ("problem.md", "student-solution.md", "teacher-solution.md", "solution.md", "physics-model.json"):
        path = entry / name
        if path.exists():
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    # Explanation diagrams are part of the reviewed answer, even though their
    # bytes live outside Markdown.  Hash every local image referenced by an
    # answer layer so an SVG/PNG edit cannot silently retain an old approval.
    referenced: dict[str, Path] = {}
    for name in ("student-solution.md", "teacher-solution.md", "solution.md"):
        path = entry / name
        if not path.exists():
            continue
        for raw in markdown_image_refs(path.read_text(encoding="utf-8")):
            value = raw.strip()
            if value.startswith("<") and ">" in value:
                relative = value[1:value.index(">")]
            else:
                relative = value.split(maxsplit=1)[0]
            if re.match(r"^(https?:|data:)", relative):
                continue
            candidate = (entry / relative).resolve()
            try:
                key = str(candidate.relative_to(entry.resolve()))
            except ValueError:
                continue
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS | {".svg"}:
                referenced[key] = candidate
    for key in sorted(referenced):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(referenced[key].read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def safe_slug(value: str, fallback: str = "question") -> str:
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"[^0-9a-z\u3400-\u9fff-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return (value or fallback)[:40]


def library_path(raw: str | None) -> Path:
    return Path(raw).expanduser().resolve() if raw else (Path.cwd() / "student-error-library").resolve()


def init_library(root: Path) -> None:
    for relative in ("entries", "folders", "indexes", ".cache"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    if not config_path.exists():
        write_json(
            config_path,
            {
                "schema_version": SCHEMA_VERSION,
                "student": {"name": "", "grade": "", "subjects": []},
                "privacy": {"storage": "local", "allow_remote_ocr": False, "allow_remote_visual_review": False},
                "ocr": {"provider": "auto", "languages": ["zh-Hans", "en-US"]},
                "source_review": {"mode": "auto", "adapter_command": "", "adapter_locality": "local"},
            },
        )
    else:
        config = load_json(config_path, {})
        privacy = config.setdefault("privacy", {})
        privacy.setdefault("storage", "local")
        privacy.setdefault("allow_remote_ocr", False)
        privacy.setdefault("allow_remote_visual_review", False)
        config.setdefault("source_review", {"mode": "auto", "adapter_command": "", "adapter_locality": "local"})
        write_json(config_path, config)
    rebuild_index(root)


def entry_dirs(root: Path) -> Iterable[Path]:
    entries = root / "entries"
    if not entries.exists():
        return []
    return sorted(path for path in entries.iterdir() if path.is_dir() and (path / "record.json").exists())


def default_library_folder(record: dict[str, Any], entry_name: str = "") -> str:
    created = str(record.get("created_at", ""))
    if re.match(r"^\d{4}-\d{2}-\d{2}", created):
        return created[:10]
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", entry_name)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else str(date.today())


def validate_library_folder_name(value: str) -> str:
    name = re.sub(r"\s+", " ", value.strip())
    if not name or len(name) > 60:
        raise ValueError("folder name must contain 1-60 characters")
    if (
        name in {".", ".."}
        or name.startswith(".")
        or name.endswith((".", " "))
        or any(char in name for char in '<>:"/\\|?*\0')
    ):
        raise ValueError("folder name contains an unsafe character")
    if any(ord(char) < 32 for char in name):
        raise ValueError("folder name contains a control character")
    return name


def sync_library_folders(root: Path) -> list[dict[str, Any]]:
    folders_root = root / "folders"
    folders_root.mkdir(parents=True, exist_ok=True)
    local_assignments: dict[str, str] = {}
    for group_dir in sorted(path for path in folders_root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        try:
            group_name = validate_library_folder_name(group_dir.name)
        except ValueError:
            continue
        for child in group_dir.iterdir():
            if child.is_symlink() and (root / "entries" / child.name / "record.json").exists():
                try:
                    if child.resolve() != (root / "entries" / child.name).resolve():
                        continue
                except OSError:
                    continue
                local_assignments.setdefault(child.name, group_name)
            elif child.name.endswith(".entry.json"):
                pointer = load_json(child, {})
                entry_id = str(pointer.get("entry_id", ""))
                if (root / "entries" / entry_id / "record.json").exists():
                    local_assignments.setdefault(entry_id, group_name)
    grouped: dict[str, list[str]] = defaultdict(list)
    for entry in entry_dirs(root):
        record_path = entry / "record.json"
        record = load_json(record_path, {})
        raw_name = local_assignments.get(entry.name) or str(record.get("library_folder", ""))
        try:
            folder_name = validate_library_folder_name(raw_name) if raw_name else default_library_folder(record, entry.name)
        except ValueError:
            folder_name = default_library_folder(record, entry.name)
        if record.get("library_folder") != folder_name:
            record["library_folder"] = folder_name
            write_json(record_path, record)
        group_dir = folders_root / folder_name
        group_dir.mkdir(parents=True, exist_ok=True)
        link = group_dir / entry.name
        pointer = group_dir / f"{entry.name}.entry.json"
        if not link.exists() and not link.is_symlink() and not pointer.exists():
            try:
                link.symlink_to(Path(os.path.relpath(entry, group_dir)), target_is_directory=True)
            except OSError:
                write_json(pointer, {"entry_id": entry.name, "target": os.path.relpath(entry, group_dir)})
        grouped[folder_name].append(entry.name)
    return [
        {"name": name, "entries": sorted(entry_ids)}
        for name, entry_ids in sorted(grouped.items(), reverse=True)
    ]


def rename_library_folder(root: Path, old_name: str, new_name: str) -> dict[str, Any]:
    old = validate_library_folder_name(old_name)
    new = validate_library_folder_name(new_name)
    if old == new:
        return {"status": "unchanged", "old_name": old, "new_name": new, "entries": []}
    folders_root = root / "folders"
    old_dir, new_dir = folders_root / old, folders_root / new
    conflicting = next(
        (path for path in folders_root.iterdir() if path.name.casefold() == new.casefold() and path.name != old),
        None,
    ) if folders_root.exists() else None
    if new_dir.exists() or conflicting:
        raise FileExistsError(f"folder already exists: {new}")
    if not old_dir.exists():
        raise FileNotFoundError(f"folder not found: {old}")
    affected = [
        child.name if child.is_symlink() else str(load_json(child, {}).get("entry_id", ""))
        for child in old_dir.iterdir()
        if child.is_symlink() or child.name.endswith(".entry.json")
    ]
    affected = [entry_id for entry_id in affected if (root / "entries" / entry_id / "record.json").exists()]
    if not affected:
        raise ValueError(f"folder contains no indexed entries: {old}")
    old_dir.rename(new_dir)
    for entry in entry_dirs(root):
        record_path = entry / "record.json"
        record = load_json(record_path, {})
        folder_name = str(record.get("library_folder") or default_library_folder(record, entry.name))
        if entry.name not in affected and folder_name != old:
            continue
        record["library_folder"] = new
        write_json(record_path, record)
    sync_library_folders(root)
    rebuild_index(root)
    return {"status": "renamed", "old_name": old, "new_name": new, "entries": affected, "path": str(new_dir.resolve())}


def find_duplicate(root: Path, source_hash: str) -> str | None:
    for entry in entry_dirs(root):
        record = load_json(entry / "record.json", {})
        if record.get("source", {}).get("sha256") == source_hash:
            return str(record.get("id") or entry.name)
    return None


def discover_inputs(source: Path) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.lower() in SUPPORTED_EXTENSIONS else []
    if source.is_dir():
        return sorted(
            path for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    return []


def run_process(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, env=env)


def vision_ocr(image: Path, cache_dir: Path) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("Apple Vision OCR is available only on macOS")
    errors: list[str] = []
    clang = shutil.which("clang")
    executable = cache_dir / "vision_ocr"
    if clang and VISION_OBJC_SOURCE.exists():
        if not executable.exists() or executable.stat().st_mtime < VISION_OBJC_SOURCE.stat().st_mtime:
            clang_cache = cache_dir / "clang-module-cache"
            clang_cache.mkdir(parents=True, exist_ok=True)
            compile_env = os.environ.copy()
            compile_env["CLANG_MODULE_CACHE_PATH"] = str(clang_cache)
            compile_result = run_process([
                clang, "-fobjc-arc", "-fblocks",
                "-framework", "Foundation", "-framework", "Vision",
                "-framework", "ImageIO", "-framework", "CoreGraphics",
                str(VISION_OBJC_SOURCE), "-o", str(executable),
            ], env=compile_env)
            if compile_result.returncode != 0:
                errors.append(compile_result.stderr.strip() or "Objective-C helper compilation failed")
        if executable.exists():
            result = run_process([str(executable), str(image)])
            if result.returncode == 0:
                return json.loads(result.stdout)
            errors.append(result.stderr.strip() or "Objective-C Vision OCR failed")

    swift = shutil.which("swift")
    if swift and VISION_SCRIPT.exists():
        module_cache = cache_dir / "swift-module-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["SWIFT_MODULECACHE_PATH"] = str(module_cache)
        env["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
        result = run_process([swift, str(VISION_SCRIPT), str(image)], env=env)
        if result.returncode == 0:
            return json.loads(result.stdout)
        errors.append(result.stderr.strip() or "Swift Vision OCR failed")
    raise RuntimeError("; ".join(error for error in errors if error) or "Apple Vision OCR is unavailable")


def command_ocr(image: Path, command_text: str) -> dict[str, Any]:
    tokens = shlex.split(command_text)
    if not tokens:
        raise RuntimeError("OCR command is empty")
    replaced = False
    command: list[str] = []
    for token in tokens:
        if "{input}" in token:
            command.append(token.replace("{input}", str(image)))
            replaced = True
        else:
            command.append(token)
    if not replaced:
        command.append(str(image))
    result = run_process(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "External OCR command failed")
    raw = result.stdout.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "text" in parsed:
            parsed.setdefault("engine", "external-command")
            parsed.setdefault("average_confidence", 0.0)
            parsed.setdefault("lines", [])
            return parsed
    except json.JSONDecodeError:
        pass
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "external-command",
        "input": str(image),
        "languages": [],
        "average_confidence": 0.0,
        "text": raw,
        "lines": [],
    }


def ocr_image(image: Path, provider: str, command_text: str | None, cache_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    if provider in {"auto", "command"} and command_text:
        try:
            return command_ocr(image, command_text)
        except Exception as exc:  # noqa: BLE001 - preserve fallback details
            errors.append(f"external-command: {exc}")
            if provider == "command":
                raise
    if provider in {"auto", "vision"}:
        try:
            return vision_ocr(image, cache_dir)
        except Exception as exc:  # noqa: BLE001 - preserve fallback details
            errors.append(f"apple-vision: {exc}")
            if provider == "vision":
                raise
    if provider == "none":
        errors.append("OCR disabled")
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "unavailable",
        "input": str(image),
        "languages": [],
        "average_confidence": 0.0,
        "text": "",
        "lines": [],
        "errors": errors,
    }


def render_pdf(pdf: Path, output_dir: Path) -> list[Path]:
    executable = shutil.which("pdftoppm")
    if not executable:
        raise RuntimeError("pdftoppm is required for PDF input")
    prefix = output_dir / "page"
    result = run_process([executable, "-png", "-r", "220", str(pdf), str(prefix)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PDF rendering failed")
    return sorted(output_dir.glob("page-*.png"))


def combine_ocr(results: list[dict[str, Any]]) -> dict[str, Any]:
    confidences = [float(item.get("average_confidence", 0.0)) for item in results]
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "+".join(sorted({str(item.get("engine", "unknown")) for item in results})),
        "average_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "text": "\n\n".join(
            f"--- Page {index} ---\n{item.get('text', '')}" for index, item in enumerate(results, 1)
        ),
        "pages": results,
    }


def problem_draft(entry_id: str, image_refs: list[str], ocr_text: str) -> str:
    images = "\n".join(f"![原始题图 {index}]({ref})" for index, ref in enumerate(image_refs, 1))
    body = ocr_text.strip() or "[待核对] OCR 未得到文本，请人工转写题目。"
    return (
        f"# 题目（OCR 草稿）\n\n"
        f"题目编号：`{entry_id}`\n\n{images}\n\n"
        f"## OCR 原文\n\n{body}\n\n"
        "## 人工校对稿\n\n[待核对]\n"
    )


def solution_draft(image_refs: list[str]) -> str:
    images = "\n".join(f"![原始题图 {index}]({ref})" for index, ref in enumerate(image_refs, 1))
    return (
        "# 解析\n\n"
        f"{images}\n\n"
        "## 答案速览\n\n待生成\n\n"
        "## 详细解答\n\n待生成\n\n"
        "## 易错点\n\n待生成\n\n"
        "## 关联知识\n\n待生成\n"
    )


def _ingest_image(
    root: Path,
    stored_image: Path,
    entry: Path,
    entry_id: str,
    title: str,
    subject: str,
    provider: str,
    command_text: str | None,
    cache_dir: Path,
    *,
    original_name: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    """OCR a single stored image and write entry files (ocr/record/problem/solution).

    The entry directory and stored_image must already exist at their final
    location (e.g. entry/assets/page-1.png).
    """
    ocr = ocr_image(stored_image, provider, command_text, cache_dir)
    write_json(entry / "ocr.json", ocr)

    original_name = original_name or stored_image.name
    source_type = source_type or stored_image.suffix.lower().lstrip(".")
    image_ref = f"assets/{stored_image.name}"
    source_hash = sha256_file(stored_image)
    record = {
        "schema_version": SCHEMA_VERSION,
        "id": entry_id,
        "kind": "error",
        "status": "needs-review",
        "answer_status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "library_folder": str(date.today()),
        "title": title,
        "subject": subject,
        "grade": "",
        "knowledge_points": [],
        "error_types": ["待确认"],
        "student_error": "未提供；需结合学生原答确认。",
        "difficulty": "",
        "source": {
            "original_name": original_name,
            "sha256": source_hash,
            "stored_files": [f"assets/{stored_image.name}"],
            "source_type": source_type,
        },
        "ocr": {
            "engine": ocr.get("engine", "unknown"),
            "average_confidence": ocr.get("average_confidence", 0.0),
            "review_required": True,
        },
        "source_review": {"status": "needs-review", "method": "pending"},
        "answer_review": {"status": "not-ready"},
        "visualization_review": {"status": "not-ready"},
        "generated_from": [],
        "review": {"mastery": 0, "next_review": str(date.today()), "history": []},
    }
    write_json(entry / "record.json", record)
    write_text(entry / "problem.md", problem_draft(entry_id, [image_ref], str(ocr.get("text", ""))))
    write_text(entry / "solution.md", solution_draft([image_ref]))
    return {"input": str(stored_image), "status": "ingested", "entry_id": entry_id, "entry": str(entry)}


def ingest_one(root: Path, source: Path, provider: str, command_text: str | None,
               subject: str, title: str | None) -> dict[str, Any]:
    source_hash = sha256_file(source)
    duplicate = find_duplicate(root, source_hash)
    if duplicate:
        return {"input": str(source), "status": "duplicate", "entry_id": duplicate}

    entry_id = f"{date.today():%Y%m%d}-{safe_slug(title or source.stem)}-{source_hash[:8]}"
    entry = root / "entries" / entry_id
    assets = entry / "assets"
    assets.mkdir(parents=True, exist_ok=False)
    stored_source = assets / f"original{source.suffix.lower()}"
    shutil.copy2(source, stored_source)

    return _ingest_image(
        root=root,
        stored_image=stored_source,
        entry=entry,
        entry_id=entry_id,
        title=title or source.stem.replace("_", " "),
        subject=subject,
        provider=provider,
        command_text=command_text,
        cache_dir=root / ".cache",
        original_name=source.name,
        source_type=source.suffix.lower().lstrip("."),
   )


def ingest_pdf_pages(
    root: Path,
    pdf_source: Path,
    provider: str,
    command_text: str | None,
    subject: str,
) -> list[dict[str, Any]]:
    """Render a multi-page PDF and ingest each page as a separate entry."""
    pdf_slug = safe_slug(pdf_source.stem)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        stored_pdf = tmp_path / "original.pdf"
        shutil.copy2(pdf_source, stored_pdf)
        pages = render_pdf(stored_pdf, tmp_path)

        results: list[dict[str, Any]] = []
        for index, page_path in enumerate(pages, 1):
            page_hash = sha256_file(page_path)
            duplicate = find_duplicate(root, page_hash)
            if duplicate:
                results.append({
                    "input": str(pdf_source), "status": "duplicate",
                    "entry_id": duplicate, "page": index,
                })
                continue

            entry_id = f"{date.today():%Y%m%d}-{pdf_slug}-p{index}-{page_hash[:8]}"
            entry = root / "entries" / entry_id
            entry_assets = entry / "assets"
            entry_assets.mkdir(parents=True, exist_ok=False)
            stored_image = entry_assets / f"page-{index}.png"
            shutil.copy2(page_path, stored_image)

            result = _ingest_image(
                root=root,
                stored_image=stored_image,
                entry=entry,
                entry_id=entry_id,
                title=f"{pdf_source.stem} 第{index}页",
                subject=subject,
                provider=provider,
                command_text=command_text,
                cache_dir=root / ".cache",
                original_name=f"{pdf_source.name} (第{index}页)",
                source_type="png",
            )
            results.append(result)

    return results


def copy_assets(asset_paths: list[Path], destination: Path) -> list[str]:
    refs: list[str] = []
    used: set[str] = set()
    for source in asset_paths:
        if not source.is_file():
            raise FileNotFoundError(source)
        name = source.name
        stem, suffix = source.stem, source.suffix
        counter = 2
        while name in used or (destination / name).exists():
            name = f"{stem}-{counter}{suffix}"
            counter += 1
        shutil.copy2(source, destination / name)
        used.add(name)
        refs.append(f"assets/{name}")
    return refs


def create_generated(root: Path, record_path: Path, problem_path: Path,
                     solution_path: Path, asset_paths: list[Path]) -> dict[str, Any]:
    record = load_json(record_path, {})
    problem = problem_path.read_text(encoding="utf-8")
    solution = solution_path.read_text(encoding="utf-8")
    digest = sha256_text(problem + "\n" + solution)
    entry_id = f"{date.today():%Y%m%d}-generated-{safe_slug(str(record.get('title', 'question')))}-{digest[:8]}"
    entry = root / "entries" / entry_id
    if entry.exists():
        return {"status": "duplicate", "entry_id": entry_id, "entry": str(entry)}
    assets = entry / "assets"
    assets.mkdir(parents=True, exist_ok=False)
    copied = copy_assets(asset_paths, assets)
    record.update(
        {
            "schema_version": SCHEMA_VERSION,
            "id": entry_id,
            "kind": "generated",
            "status": "needs-review",
            "answer_status": "pending",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "library_folder": str(date.today()),
            "source": {"sha256": digest, "source_type": "generated", "stored_files": copied},
            "ocr": {"engine": "not-applicable", "average_confidence": 1.0, "review_required": False},
        }
    )
    record.setdefault("generated_from", [])
    record.setdefault("error_types", ["巩固练习"])
    record.setdefault("student_error", "针对既有错因生成的迁移练习。")
    record.setdefault("review", {"mastery": 0, "next_review": str(date.today()), "history": []})
    record.setdefault("visualization_review", {"status": "not-ready"})
    write_json(entry / "record.json", record)
    write_text(entry / "problem.md", problem)
    write_text(entry / "solution.md", solution)
    return {"status": "created", "entry_id": entry_id, "entry": str(entry)}


def reocr_entry(root: Path, entry_id: str, provider: str, command_text: str | None) -> dict[str, Any]:
    entry = root / "entries" / entry_id
    record = load_json(entry / "record.json")
    if not record:
        raise FileNotFoundError(entry_id)
    source_type = record.get("source", {}).get("source_type", "")
    stored = [entry / relative for relative in record.get("source", {}).get("stored_files", [])]
    if source_type == "pdf":
        images = sorted(path for path in stored if path.suffix.lower() in IMAGE_EXTENSIONS)
        if not images:
            original = next((path for path in stored if path.suffix.lower() == ".pdf" and path.exists()), None)
            if not original:
                raise FileNotFoundError("stored PDF source is missing")
            images = render_pdf(original, entry / "assets")
    else:
        images = [path for path in stored if path.suffix.lower() in IMAGE_EXTENSIONS and path.exists()]
    if not images:
        raise FileNotFoundError("stored image source is missing")
    results = [ocr_image(image, provider, command_text, root / ".cache") for image in images]
    ocr = combine_ocr(results)
    write_json(entry / "ocr.json", ocr)
    record["ocr"] = {
        "engine": ocr.get("engine", "unknown"),
        "average_confidence": ocr.get("average_confidence", 0.0),
        "review_required": True,
    }
    record["updated_at"] = now_iso()
    write_json(entry / "record.json", record)
    return {"entry_id": entry_id, "engine": record["ocr"]["engine"], "average_confidence": record["ocr"]["average_confidence"]}


def markdown_image_refs(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def validate_entry(root: Path, entry: Path, *, ready_rules: bool = True, require_answer_review: bool = True) -> list[str]:
    errors: list[str] = []
    record = load_json(entry / "record.json", {})
    problem_path, solution_path = entry / "problem.md", entry / "solution.md"
    problem = problem_path.read_text(encoding="utf-8") if problem_path.exists() else ""
    solution = solution_path.read_text(encoding="utf-8") if solution_path.exists() else ""

    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append("record.json: unsupported schema_version")
    if record.get("id") != entry.name:
        errors.append("record.json: id must match directory name")
    for field in ("title", "subject"):
        if not str(record.get(field, "")).strip():
            errors.append(f"record.json: {field} is required")
    for field in ("knowledge_points", "error_types"):
        if not isinstance(record.get(field), list) or not record.get(field):
            errors.append(f"record.json: {field} must be a non-empty list")
    if not problem_path.exists() or len(problem.strip()) < 30:
        errors.append("problem.md: missing or too short")
    if not solution_path.exists() or len(solution.strip()) < 100:
        errors.append("solution.md: missing or too short")
    if ready_rules:
        if record.get("ocr", {}).get("review_required"):
            errors.append("record.json: OCR review_required must be false")
        if "source_review" in record and record.get("source_review", {}).get("status") != "passed":
            errors.append("record.json: source_review.status must be passed")
        if require_answer_review and "answer_review" in record:
            answer_review = record.get("answer_review", {})
            if answer_review.get("status") != "passed":
                errors.append("record.json: answer_review.status must be passed")
            elif answer_review.get("answer_digest") != answer_artifact_digest(entry):
                errors.append("record.json: answer changed after teacher approval")
        for marker in PENDING_MARKERS:
            if marker in problem or marker in solution:
                errors.append(f"markdown: unresolved marker {marker!r}")
        for heading in REQUIRED_SOLUTION_HEADINGS:
            if heading not in solution:
                errors.append(f"solution.md: missing heading {heading!r}")
        if not markdown_image_refs(solution):
            errors.append("solution.md: at least one explanatory/source image is required")

    for markdown_name, text in (("problem.md", problem), ("solution.md", solution)):
        for ref in markdown_image_refs(text):
            if re.match(r"^(https?:|data:)", ref):
                continue
            target = (entry / ref).resolve()
            try:
                target.relative_to(entry.resolve())
            except ValueError:
                errors.append(f"{markdown_name}: image escapes entry directory: {ref}")
                continue
            if not target.exists():
                errors.append(f"{markdown_name}: missing image {ref}")

    stored = record.get("source", {}).get("stored_files", [])
    for relative in stored:
        if not (entry / relative).exists():
            errors.append(f"record.json: missing stored file {relative}")
    source_hash = record.get("source", {}).get("sha256", "")
    original_name = next((name for name in stored if Path(name).name.startswith("original")), None)
    if original_name and source_hash and sha256_file(entry / original_name) != source_hash:
        errors.append("record.json: original source hash mismatch")
    return errors


def finalize_entry(root: Path, entry_id: str) -> list[str]:
    entry = root / "entries" / entry_id
    if not entry.exists():
        return [f"entry not found: {entry_id}"]
    errors = validate_entry(root, entry, ready_rules=True)
    if errors:
        return errors
    record = load_json(entry / "record.json", {})
    record["status"] = "ready"
    record["answer_status"] = "complete"
    record["updated_at"] = now_iso()
    write_json(entry / "record.json", record)
    rebuild_index(root)
    return []


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z]+(?:[0-9]+)?|[0-9]+(?:\.[0-9]+)?", lowered)
    for segment in re.findall(r"[\u3400-\u9fff]+", lowered):
        if len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
            if len(segment) <= 8:
                tokens.append(segment)
    return tokens


def entry_search_text(entry: Path, record: dict[str, Any]) -> tuple[str, dict[str, int]]:
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").exists() else ""
    solution = (entry / "solution.md").read_text(encoding="utf-8") if (entry / "solution.md").exists() else ""
    weighted_fields = {
        "title": (str(record.get("title", "")), 6),
        "knowledge": (" ".join(record.get("knowledge_points", [])), 5),
        "errors": (" ".join(record.get("error_types", [])) + " " + str(record.get("student_error", "")), 4),
        "subject": (str(record.get("subject", "")) + " " + str(record.get("grade", "")), 3),
        "problem": (problem, 1),
        "solution": (solution, 1),
    }
    counts: Counter[str] = Counter()
    pieces: list[str] = []
    for value, weight in weighted_fields.values():
        pieces.append(value)
        for token in tokenize(value):
            counts[token] += weight
    return "\n".join(pieces), dict(counts)


def rebuild_index(root: Path) -> dict[str, Any]:
    catalog: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for entry in entry_dirs(root):
        record = load_json(entry / "record.json", {})
        _, term_counts = entry_search_text(entry, record)
        item = {
            "id": record.get("id", entry.name),
            "kind": record.get("kind", "error"),
            "status": record.get("status", "needs-review"),
            "title": record.get("title", ""),
            "subject": record.get("subject", ""),
            "grade": record.get("grade", ""),
            "knowledge_points": record.get("knowledge_points", []),
            "error_types": record.get("error_types", []),
            "next_review": record.get("review", {}).get("next_review"),
            "updated_at": record.get("updated_at"),
            "library_folder": record.get("library_folder", default_library_folder(record, entry.name)),
            "path": str(entry.relative_to(root)),
        }
        catalog.append(item)
        documents.append({"id": item["id"], "length": sum(term_counts.values()), "terms": term_counts})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "entries": catalog,
        "documents": documents,
    }
    write_json(root / "indexes" / "catalog.json", payload)
    return payload


def search(root: Path, query: str, top_k: int) -> list[dict[str, Any]]:
    index = rebuild_index(root)
    docs = [doc for doc in index["documents"]]
    catalog = {item["id"]: item for item in index["entries"]}
    query_tokens = list(dict.fromkeys(tokenize(query)))
    if not query_tokens or not docs:
        return []
    df: Counter[str] = Counter()
    for doc in docs:
        for token in set(doc["terms"]):
            df[token] += 1
    average_length = sum(doc["length"] for doc in docs) / len(docs)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for doc in docs:
        if catalog[doc["id"]].get("status") != "ready":
            continue
        score = 0.0
        matched: list[str] = []
        for token in query_tokens:
            tf = float(doc["terms"].get(token, 0))
            if not tf:
                continue
            matched.append(token)
            idf = math.log(1.0 + (len(docs) - df[token] + 0.5) / (df[token] + 0.5))
            denominator = tf + 1.2 * (0.25 + 0.75 * doc["length"] / max(average_length, 1.0))
            score += idf * (tf * 2.2) / denominator
        if score:
            coverage = len(matched) / len(query_tokens)
            score *= 0.6 + 0.4 * coverage
            scored.append((score, catalog[doc["id"]], matched))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [dict(item, score=round(score, 4), matched_terms=matched) for score, item, matched in scored[:top_k]]


def apply_review(root: Path, entry_id: str, result: str, note: str) -> dict[str, Any]:
    entry = root / "entries" / entry_id
    record = load_json(entry / "record.json")
    if not record:
        raise FileNotFoundError(entry_id)
    review = record.setdefault("review", {"mastery": 0, "next_review": str(date.today()), "history": []})
    mastery = int(review.get("mastery", 0))
    if result == "wrong":
        mastery, interval = max(0, mastery - 1), 1
    elif result == "partial":
        mastery, interval = mastery, 3
    else:
        mastery = min(5, mastery + 1)
        interval = (1, 3, 7, 14, 30, 60)[mastery]
    review["mastery"] = mastery
    review["next_review"] = str(date.today() + timedelta(days=interval))
    review.setdefault("history", []).append({"date": str(date.today()), "result": result, "note": note})
    record["updated_at"] = now_iso()
    write_json(entry / "record.json", record)
    rebuild_index(root)
    return {"entry_id": entry_id, "mastery": mastery, "next_review": review["next_review"]}


def due_entries(root: Path, on_date: date) -> list[dict[str, Any]]:
    index = rebuild_index(root)
    return [
        item for item in index["entries"]
        if item.get("status") == "ready" and item.get("next_review") and item["next_review"] <= str(on_date)
    ]


def export_entry(root: Path, entry_id: str, output_base: Path | None = None) -> dict[str, Any]:
    # Answer text is hand-edited; refresh retrieval before every downstream export.
    rebuild_index(root)
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"error": f"entry not found: {entry_id}"}
    record = load_json(entry / "record.json", {})
    title = record.get("title", entry_id)
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title).strip()[:60]
    out_dir = (output_base or root.parent / "output") / safe_title
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").exists() else ""
    solution = (entry / "solution.md").read_text(encoding="utf-8") if (entry / "solution.md").exists() else ""
    student_solution = (entry / "student-solution.md").read_text(encoding="utf-8") if (entry / "student-solution.md").exists() else solution
    teacher_solution = (entry / "teacher-solution.md").read_text(encoding="utf-8") if (entry / "teacher-solution.md").exists() else solution

    # Rewrite image paths to point back to entries assets — no duplication.
    # output/<title>/  →  ../../student-error-library/entries/<id>/assets/
    rel_base = os.path.relpath(str(entry / "assets"), str(out_dir))
    def _relink(text: str) -> str:
        return re.sub(r'!\[([^\]]*)\]\(assets/([^)]+)\)', rf'![\1]({rel_base}/\2)', text)
    problem_linked = _relink(problem)
    student_linked = _relink(student_solution)
    teacher_linked = _relink(teacher_solution)

    student_combined = f"{problem_linked}\n\n---\n\n{student_linked}"
    teacher_combined = f"{problem_linked}\n\n---\n\n{teacher_linked}"
    write_text(out_dir / "带答案错题.md", student_combined)
    write_text(out_dir / "学生版解析.md", student_combined)
    write_text(out_dir / "教师版解析.md", teacher_combined)
    model_path = entry / "physics-model.json"
    if model_path.exists():
        shutil.copy2(model_path, out_dir / "physics-model.json")

    # PDF: copy assets temporarily, run pandoc, then clean up
    pdf_result = _generate_pdf(out_dir, entry, "带答案错题.md", "带答案错题.pdf")

    return {
        "entry_id": entry_id,
        "title": title,
        "output": str(out_dir),
        "files": [str(p.relative_to(out_dir)) for p in sorted(out_dir.rglob("*")) if p.is_file()],
        "pdf": pdf_result,
    }


def _generate_pdf(out_dir: Path, entry: Path, md_name: str, pdf_name: str) -> dict[str, Any]:
    tmp_assets = out_dir / "assets"
    tmp_assets_created = False
    try:
        entry_assets = entry / "assets"
        if entry_assets.exists() and entry_assets.is_dir():
            tmp_assets.mkdir(parents=True, exist_ok=True)
            tmp_assets_created = True
            for f in entry_assets.iterdir():
                if f.is_file():
                    shutil.copy2(f, tmp_assets / f.name)
        pdf_path = out_dir / pdf_name
        return pdf_export.generate_markdown_pdf(out_dir / md_name, pdf_path, success_status="ok")
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:200]}
    finally:
        if tmp_assets_created and tmp_assets.exists():
            shutil.rmtree(tmp_assets, ignore_errors=True)


def stats(root: Path) -> dict[str, Any]:
    index = rebuild_index(root)
    items = index["entries"]
    by_subject: Counter[str] = Counter(item.get("subject") or "未分类" for item in items)
    by_status: Counter[str] = Counter(item.get("status") or "unknown" for item in items)
    by_topic: Counter[str] = Counter(topic for item in items for topic in item.get("knowledge_points", []))
    return {
        "total": len(items),
        "by_status": dict(by_status),
        "by_subject": dict(by_subject),
        "top_knowledge_points": by_topic.most_common(10),
        "due_today": len(due_entries(root, date.today())),
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root_parser = argparse.ArgumentParser(description=__doc__)
    root_parser.add_argument("--library", help="Knowledge-base directory (default: ./student-error-library)")
    commands = root_parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize the local knowledge base")
    ingest = commands.add_parser("ingest", help="Copy images/PDFs locally and run OCR")
    ingest.add_argument("input")
    ingest.add_argument("--ocr", choices=("auto", "vision", "command", "none"), default="auto")
    ingest.add_argument("--ocr-command", help="Executable command; use {input} placeholder or input is appended")
    ingest.add_argument("--subject", default="")
    ingest.add_argument("--title")

    create = commands.add_parser("create", help="Create a generated practice question and answer")
    create.add_argument("--record", required=True, type=Path)
    create.add_argument("--problem", required=True, type=Path)
    create.add_argument("--solution", required=True, type=Path)
    create.add_argument("--asset", action="append", default=[], type=Path)

    reocr = commands.add_parser("reocr", help="Run OCR again for an imported entry without replacing edits")
    reocr.add_argument("entry_id")
    reocr.add_argument("--ocr", choices=("auto", "vision", "command", "none"), default="auto")
    reocr.add_argument("--ocr-command", help="Executable command; use {input} placeholder or input is appended")

    finalize = commands.add_parser("finalize", help="Validate an edited entry, mark it ready, and auto-export to output/")
    finalize.add_argument("entry_id")
    finalize.add_argument("--output", type=Path, help="Output base directory (default: workspace/output)")
    validate = commands.add_parser("validate", help="Validate one entry or the whole library")
    validate.add_argument("entry_id", nargs="?")
    commands.add_parser("rebuild", help="Rebuild the local lexical index")
    search_parser = commands.add_parser("search", help="BM25 search over ready entries")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=5)
    review = commands.add_parser("review", help="Record a review result and schedule the next review")
    review.add_argument("entry_id")
    review.add_argument("result", choices=("wrong", "partial", "correct"))
    review.add_argument("--note", default="")
    due = commands.add_parser("due", help="List entries due for review")
    due.add_argument("--date", default=str(date.today()))
    export_parser = commands.add_parser("export", help="Export entry to output/<title>/ with combined markdown + assets")
    export_parser.add_argument("entry_id")
    export_parser.add_argument("--output", type=Path, help="Output base directory (default: library/output)")
    commands.add_parser("stats", help="Show library summary")
    return root_parser


def main() -> int:
    args = parser().parse_args()
    root = library_path(args.library)
    init_library(root)

    if args.command == "init":
        print_json({"status": "initialized", "library": str(root)})
    elif args.command == "ingest":
        inputs = discover_inputs(Path(args.input).expanduser().resolve())
        if not inputs:
            print_json({"error": "No supported image or PDF input found"})
            return 2
        results: list[dict] = []
        for item in inputs:
            if item.suffix.lower() == ".pdf":
                results.extend(ingest_pdf_pages(root, item, args.ocr, args.ocr_command, args.subject))
            else:
                results.append(
                    ingest_one(root, item, args.ocr, args.ocr_command, args.subject,
                               args.title if len(inputs) == 1 else None)
                )
        rebuild_index(root)
        print_json(results)
    elif args.command == "create":
        result = create_generated(root, args.record.resolve(), args.problem.resolve(), args.solution.resolve(),
                                  [path.resolve() for path in args.asset])
        rebuild_index(root)
        print_json(result)
    elif args.command == "reocr":
        print_json(reocr_entry(root, args.entry_id, args.ocr, args.ocr_command))
    elif args.command == "finalize":
        errors = finalize_entry(root, args.entry_id)
        result = {"entry_id": args.entry_id, "valid": not errors, "errors": errors}
        if not errors:
            export_result = export_entry(root, args.entry_id, args.output)
            result["export"] = export_result
        print_json(result)
        return 1 if errors else 0
    elif args.command == "validate":
        targets = [root / "entries" / args.entry_id] if args.entry_id else list(entry_dirs(root))
        report = {entry.name: validate_entry(root, entry, ready_rules=load_json(entry / "record.json", {}).get("status") == "ready") for entry in targets}
        rebuild_index(root)
        print_json(report)
        return 1 if any(report.values()) else 0
    elif args.command == "rebuild":
        print_json(rebuild_index(root))
    elif args.command == "search":
        print_json(search(root, args.query, args.top_k))
    elif args.command == "review":
        print_json(apply_review(root, args.entry_id, args.result, args.note))
    elif args.command == "due":
        print_json(due_entries(root, date.fromisoformat(args.date)))
    elif args.command == "export":
        print_json(export_entry(root, args.entry_id, args.output))
    elif args.command == "stats":
        print_json(stats(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
