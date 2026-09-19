"""Tesseract 5 fallback provider (targeted ingredient/small-print OCR).

Role: SECONDARY engine only. RapidOCR remains primary; this provider is
invoked solely by the hybrid ingredient path (see ingredient_hybrid.py),
never for full-package passes, nutrition, MRP, or consumer-care.

Design constraints:
- pytesseract is imported lazily so application import never fails when
  the wrapper (or the binary) is absent.
- The Tesseract binary path is resolved from ``LEGALAKSHI_TESSERACT_CMD``
  or PATH lookup — never a hard-coded developer machine path.
- Missing binary / missing eng data / inference failure all raise
  OcrError, which the service layer converts to RapidOCR-only behaviour.
- Output is structured OcrLine/OcrOutput compatible with the existing
  OCR abstractions: per-line text from TSV word boxes, confidence as the
  mean of Tesseract's own reported word confidences (0-1), boxes kept.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from app.services.ocr.base import OcrError, OcrLine, OcrOutput

#: Env override for the Tesseract 5 executable (no hard-coded paths).
TESSERACT_CMD_ENV = "LEGALAKSHI_TESSERACT_CMD"

#: Engine contract for every production Tesseract call.
TESSERACT_LANG = "eng"
TESSERACT_OEM = 1  # LSTM only
TESSERACT_DEFAULT_PSM = 6  # uniform block of text (ingredient paragraph)


def resolve_tesseract_cmd(explicit: str | None = None) -> str | None:
    """Locate the Tesseract binary: explicit path > env > PATH lookup."""
    candidates = [explicit, os.environ.get(TESSERACT_CMD_ENV),
                  shutil.which("tesseract")]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    if shutil.which("tesseract"):
        return shutil.which("tesseract")
    return None


class TesseractProvider:
    """OCRProvider backed by Tesseract 5 via pytesseract (lazy, guarded)."""

    name = "tesseract"

    def __init__(self, cmd: str | None = None, lang: str = TESSERACT_LANG,
                 oem: int = TESSERACT_OEM,
                 default_psm: int = TESSERACT_DEFAULT_PSM) -> None:
        # No binary probing here: construction must stay cheap and
        # infallible so route wiring can always build one.
        self._cmd = cmd
        self.lang = lang
        self.oem = oem
        self.default_psm = default_psm
        self._available: bool | None = None

    def available(self) -> bool:
        """True when wrapper importable AND binary resolvable (cached)."""
        if self._available is None:
            try:
                import pytesseract  # noqa: F401

                self._available = resolve_tesseract_cmd(self._cmd) is not None
            except ImportError:
                self._available = False
        return self._available

    def extract(self, image_np: Any, image_side: str,
                psm: int | None = None) -> OcrOutput:
        """OCR one pre-cropped region. Raises OcrError when unusable."""
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrError(f"pytesseract is not installed: {exc}")
        cmd = resolve_tesseract_cmd(self._cmd)
        if cmd is None:
            raise OcrError(
                "Tesseract binary not found (set "
                f"{TESSERACT_CMD_ENV} or install Tesseract 5)")
        pytesseract.pytesseract.tesseract_cmd = cmd
        config = f"--oem {self.oem} --psm {psm or self.default_psm}"
        try:
            data = pytesseract.image_to_data(
                image_np, lang=self.lang, config=config,
                output_type=pytesseract.Output.DICT, timeout=120)
        except Exception as exc:
            raise OcrError(f"Tesseract inference failed: {exc}")
        lines: dict[int, dict[str, Any]] = {}
        n = len(data.get("text", []))
        for i in range(n):
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            word = str(data["text"][i] or "").strip()
            if conf < 0 or not word:
                continue  # empty / rejected token: not evidence
            try:
                line_no = int(data["line_num"][i])
                left = int(data["left"][i])
                top = int(data["top"][i])
                width = int(data["width"][i])
                height = int(data["height"][i])
            except (TypeError, ValueError, KeyError):
                continue
            slot = lines.setdefault(
                line_no, {"words": [], "boxes": [], "confs": []})
            slot["words"].append(word)
            slot["boxes"].append([left, top, left + width, top + height])
            slot["confs"].append(max(0.0, min(100.0, conf)) / 100.0)
        out: list[OcrLine] = []
        for line_no in sorted(lines):
            slot = lines[line_no]
            text = " ".join(slot["words"]).strip()
            if not text:
                continue
            xs = [b[0] for b in slot["boxes"]] + [b[2] for b in slot["boxes"]]
            ys = [b[1] for b in slot["boxes"]] + [b[3] for b in slot["boxes"]]
            box = [[min(xs), min(ys)], [max(xs), min(ys)],
                   [max(xs), max(ys)], [min(xs), max(ys)]]
            mean_conf = round(sum(slot["confs"]) / len(slot["confs"]), 3)
            out.append(OcrLine(text=text, confidence=mean_conf, box=box,
                               image=image_side))
        return OcrOutput(lines=out, engine=self.name)
