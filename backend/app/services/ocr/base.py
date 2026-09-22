"""OCR provider abstraction.

OCR converts pixels to text. It NEVER decides compliance: the rule engine
receives only the final reviewed declaration. Providers raise OcrError on
failure; the service layer converts that into a reviewable NEEDS_REVIEW
response instead of crashing the inspection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class OcrLine:
    text: str
    confidence: float
    box: list | None = None
    image: str = ""  # "front" | "back"


@dataclass
class OcrOutput:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""


class OcrError(Exception):
    """Provider failure (missing dependency, corrupt image, model error)."""


class TooManyImagesError(OcrError):
    """Inspection supplies more package images than OCR_MAX_IMAGES.

    Subclasses OcrError so legacy callers still see a provider-layer
    failure; the FastAPI route maps it to 422 (caller error) instead
    of 502, and the message always states the configured limit.
    """


class OCRProvider(Protocol):
    name: str

    def extract(self, image_np: Any, image_side: str) -> OcrOutput:
        """Run OCR on an RGB numpy image. Raises OcrError on failure."""
        ...
