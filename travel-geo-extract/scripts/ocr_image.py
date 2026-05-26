"""OCR a travel DM image and emit plain text to stdout.

Tries OCR backends in this order:
    1. paddleocr  — best for traditional/simplified Chinese (heaviest install)
    2. easyocr    — decent for CJK, smaller
    3. pytesseract — relies on system Tesseract binary

Usage:
    python ocr_image.py path/to/dm.jpg [--lang zh|zh+en]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ocr_paddle(path: Path, lang: str) -> str | None:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return None
    paddle_lang = {"zh": "ch", "en": "en", "zh+en": "ch"}.get(lang, "ch")
    ocr = PaddleOCR(use_angle_cls=True, lang=paddle_lang, show_log=False)
    result = ocr.ocr(str(path), cls=True)
    if not result or not result[0]:
        return ""
    lines = [item[1][0] for item in result[0] if item and item[1]]
    return "\n".join(lines)


def _ocr_easyocr(path: Path, lang: str) -> str | None:
    try:
        import easyocr  # type: ignore
    except ImportError:
        return None
    langs = {"zh": ["ch_tra"], "en": ["en"], "zh+en": ["ch_tra", "en"]}.get(lang, ["ch_tra"])
    reader = easyocr.Reader(langs, gpu=False, verbose=False)
    result = reader.readtext(str(path), detail=0)
    return "\n".join(result)


def _ocr_tesseract(path: Path, lang: str) -> str | None:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    tess_lang = {"zh": "chi_tra", "en": "eng", "zh+en": "chi_tra+eng"}.get(lang, "chi_tra")
    img = Image.open(str(path))
    return pytesseract.image_to_string(img, lang=tess_lang)


def ocr(path: Path, lang: str = "zh+en") -> str:
    for backend in (_ocr_paddle, _ocr_easyocr, _ocr_tesseract):
        try:
            result = backend(path, lang)
        except Exception as e:  # noqa: BLE001
            print(f"[{backend.__name__}] failed: {e}", file=sys.stderr)
            continue
        if result is not None:
            return result
    raise SystemExit(
        "No OCR backend available. Install one of:\n"
        "  pip install paddleocr paddlepaddle    # best for Chinese\n"
        "  pip install easyocr                   # lighter\n"
        "  pip install pytesseract pillow        # needs system tesseract"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR a DM image")
    parser.add_argument("path", help="image path (.jpg/.png/.webp)")
    parser.add_argument("--lang", default="zh+en", choices=["zh", "en", "zh+en"])
    args = parser.parse_args()
    p = Path(args.path)
    if not p.exists():
        print(f"file not found: {p}", file=sys.stderr)
        sys.exit(2)
    sys.stdout.write(ocr(p, args.lang))


if __name__ == "__main__":
    main()
