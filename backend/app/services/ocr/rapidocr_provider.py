"""RapidOCR provider (rapidocr_onnxruntime).

Selected because it installs from PyPI with no system binaries (unlike
Tesseract), runs on CPU-only Windows/Python 3.13, and is orders of magnitude
lighter than PaddleOCR/EasyOCR (no torch/paddle — critical given disk
constraints). Returns word boxes + text + confidence per line.
"""
from __future__ import annotations

from typing import Any

from app.services.ocr.base import OcrError, OcrLine, OcrOutput


def _parse_item(item: Any, side: str) -> OcrLine | None:
    """Defensively parse one result item across rapidocr versions.

    Observed shape: [box_points, text, score_string]. Types are probed
    rather than assumed so minor version drift cannot crash extraction.
    """
    try:
        parts = list(item)
    except TypeError:
        return None
    box: Any = None
    text: Any = None
    score: Any = None
    for part in parts:
        if isinstance(part, str) and text is None and not _is_number(part):
            text = part
        elif isinstance(part, (int, float)) and score is None:
            score = float(part)
        elif isinstance(part, str) and score is None and _is_number(part):
            score = float(part)
        elif isinstance(part, (list, tuple)) and box is None:
            box = part
    if not text or not text.strip():
        return None
    try:
        conf = max(0.0, min(1.0, float(score))) if score is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    box_list = [list(map(float, p)) if isinstance(p, (list, tuple)) else p
                for p in box] if box else None
    return OcrLine(text=text.strip(), confidence=round(conf, 3),
                   box=box_list, image=side)


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


class RapidOCRProvider:
    """OCRProvider backed by rapidocr_onnxruntime (lazy singleton engine)."""

    name = "rapidocr"
    _engine: Any = None

    def _engine_or_raise(self) -> Any:
        if RapidOCRProvider._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise OcrError(f"rapidocr_onnxruntime is not installed: {exc}")
            try:
                RapidOCRProvider._engine = RapidOCR()
            except Exception as exc:
                raise OcrError(f"RapidOCR engine failed to start: {exc}")
        return RapidOCRProvider._engine

    def extract(self, image_np: Any, image_side: str) -> OcrOutput:
        try:
            engine = self._engine_or_raise()
            out = engine(image_np)
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(f"OCR inference failed: {exc}")
        lines: list[OcrLine] = []
        result = out[0] if isinstance(out, (list, tuple)) and out else None
        for item in result or []:
            line = _parse_item(item, image_side)
            if line is not None:
                lines.append(line)
        return OcrOutput(lines=lines, engine=self.name)
