#!/usr/bin/env python3
"""Split the official 2021 CPhO semifinal paper into eight source PDFs.

The crop map is intentionally specific to the four-page official paper.  It
keeps figures with their questions and avoids placing the official solutions
anywhere near the teacher-console workspace.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject

# (zero-based page index, top edge in PDF points, bottom edge in PDF points)
QUESTION_CROPS: dict[int, list[tuple[int, float, float]]] = {
    1: [(0, 25, 248)],
    2: [(0, 245, 440)],
    3: [(0, 440, 643)],
    4: [(0, 643, 820), (1, 20, 98)],
    5: [(1, 99, 820)],
    6: [(2, 20, 273)],
    7: [(2, 270, 607)],
    8: [(2, 603, 820), (3, 20, 285)],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def crop_page(page, top: float, bottom: float):
    height = float(page.mediabox.height)
    width = float(page.mediabox.width)
    lower_y = height - bottom
    upper_y = height - top
    page.mediabox.lower_left = (0, lower_y)
    page.mediabox.upper_right = (width, upper_y)
    page.cropbox.lower_left = (0, lower_y)
    page.cropbox.upper_right = (width, upper_y)
    return page


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_pdf", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.source_pdf)
    if len(reader.pages) != 4:
        raise ValueError("expected the four-page 2021 CPhO semifinal paper")

    manifest = {
        "schema_version": 1,
        "paper": "第38届全国中学生物理竞赛复赛试题（2021）",
        "question_count": 8,
        "source_sha256": sha256(args.source_pdf),
        "questions": [],
    }
    for question, segments in QUESTION_CROPS.items():
        writer = PdfWriter()
        cropped_pages = []
        for page_index, top, bottom in segments:
            cropped_pages.append(crop_page(copy.deepcopy(reader.pages[page_index]), top, bottom))
        width = max(float(page.mediabox.width) for page in cropped_pages)
        height = sum(float(page.mediabox.height) for page in cropped_pages)
        combined = PageObject.create_blank_page(width=width, height=height)
        consumed = 0.0
        for page in cropped_pages:
            page_height = float(page.mediabox.height)
            target_bottom = height - consumed - page_height
            transform = Transformation().translate(
                tx=-float(page.mediabox.left),
                ty=target_bottom - float(page.mediabox.bottom),
            )
            combined.merge_transformed_page(page, transform, expand=False)
            consumed += page_height
        writer.add_page(combined)
        target = args.output_dir / f"question-{question:02d}.pdf"
        with target.open("wb") as handle:
            writer.write(handle)
        manifest["questions"].append({
            "question": question,
            "file": target.name,
            "page_segments": [{"page": page + 1, "top_pt": top, "bottom_pt": bottom} for page, top, bottom in segments],
            "sha256": sha256(target),
        })

    (args.output_dir / "source-split-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
