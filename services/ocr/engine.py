import time
from collections import defaultdict
from typing import Dict, List

import pytesseract
from pytesseract import Output
from PIL import Image, ImageOps


def _installed_langs() -> set:
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return {"eng"}


def load_image(file_obj) -> Image.Image:
    """Open an upload, fix phone-camera rotation (EXIF), force RGB."""
    img = Image.open(file_obj)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def run_ocr(image: Image.Image, lang: str = "eng+nep", psm: int = 6) -> Dict:
    """
    Run Tesseract and return text, per-line text, and mean word confidence (0-100).
    Languages not installed are dropped silently so it never crashes.
    """
    available = _installed_langs()
    langs = [l for l in lang.split("+") if l in available] or ["eng"]
    lang_used = "+".join(langs)

    start = time.perf_counter()
    data = pytesseract.image_to_data(
        image,
        lang=lang_used,
        config=f"--oem 1 --psm {psm}",
        output_type=Output.DICT,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    lines = defaultdict(list)
    confs: List[float] = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines[key].append(word)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)

    line_list = [" ".join(words) for _, words in sorted(lines.items())]
    return {
        "text": "\n".join(line_list),
        "lines": line_list,
        "confidence": round(sum(confs) / len(confs), 2) if confs else 0.0,
        "word_count": len(confs),
        "language": lang_used,
        "processing_ms": elapsed_ms,
    }
