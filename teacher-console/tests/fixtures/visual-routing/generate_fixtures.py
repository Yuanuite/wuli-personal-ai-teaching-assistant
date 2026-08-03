#!/usr/bin/env python3
"""Regenerate the synthetic, privacy-free visual fixtures (work-tree A0.2).

These images contain no student data and are used only by the vision probe,
unit tests, and isolated E2E runs. The clear image must be uniquely readable;
the blurred image must produce uncertainty.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

FIXTURE_DIR = Path(__file__).resolve().parent


def _draw_clear() -> Image.Image:
    image = Image.new("RGB", (720, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 40, 90, 110], outline="black", width=3)  # body
    draw.line([90, 75, 190, 75], fill="black", width=3)  # surface
    draw.line([95, 75, 150, 75], fill="black", width=3)  # force line
    draw.line([150, 75, 150, 55], fill="black", width=3)  # arrowhead
    draw.line([150, 75, 138, 75], fill="black", width=3)
    draw.text((40, 130), "m", fill="black")
    draw.text((160, 60), "F", fill="black")
    draw.text((20, 20), "A body of mass m slides on a frictionless plane", fill="black")
    draw.text((20, 150), "under a constant horizontal force F.", fill="black")
    draw.text((20, 175), "At t=0 the speed is v0=2 m/s. Find the speed at t=3 s.", fill="black")
    return image


def _draw_blurred() -> Image.Image:
    image = _draw_clear()
    image = image.filter(ImageFilter.GaussianBlur(radius=14))
    # Deterministic noise over the blurred image.
    import random

    random.seed(7)
    noise = Image.new("RGB", image.size)
    noise_pixels = noise.load()
    for y in range(image.size[1]):
        for x in range(image.size[0]):
            shift = random.randint(-60, 60)
            r, g, b = image.getpixel((x, y))
            noise_pixels[x, y] = (
                max(0, min(255, r + shift)),
                max(0, min(255, g + shift)),
                max(0, min(255, b + shift)),
            )
    return noise


CLEAR_FACTS = {
    "schema": "wuli.visual-facts.v1.fixture",
    "reviewed_text": "A body of mass m slides on a frictionless plane under a constant "
    "horizontal force F. At t=0 the speed is v0=2 m/s. Find the speed at t=3 s.",
    "printed_facts": [
        "mass is m",
        "plane is frictionless",
        "force F is constant and horizontal",
        "initial speed v0=2 m/s at t=0",
        "the question asks for the speed at t=3 s",
    ],
    "diagram_facts": [
        {
            "id": "body",
            "kind": "object",
            "statement": "a rectangle labelled m on a horizontal line",
            "confidence": 0.99,
        },
        {
            "id": "force",
            "kind": "arrow",
            "statement": "a horizontal arrow labelled F pointing right from the body",
            "confidence": 0.99,
        },
        {"id": "surface", "kind": "boundary", "statement": "a horizontal line under the body", "confidence": 0.99},
    ],
    "handwriting": [],
    "uncertainties": [],
}

BLURRED_FACTS = {
    "schema": "wuli.visual-facts.v1.fixture",
    "reviewed_text": "",
    "printed_facts": [],
    "diagram_facts": [],
    "handwriting": [],
    "uncertainties": [
        "image is too blurred to read the printed text",
        "the body/arrow geometry is not reliably identifiable",
    ],
}


def main() -> int:
    clear = _draw_clear()
    clear.save(FIXTURE_DIR / "clear-question.png")
    blurred = _draw_blurred()
    blurred.save(FIXTURE_DIR / "blurred-question.png")
    (FIXTURE_DIR / "clear-question.facts.json").write_text(
        json.dumps(CLEAR_FACTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (FIXTURE_DIR / "blurred-question.facts.json").write_text(
        json.dumps(BLURRED_FACTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"fixtures written to {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
