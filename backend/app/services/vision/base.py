"""Vision provider interface (mirrors the OCR provider abstraction).

The provider converts (image, requested fields, OCR candidates, layout
context) into STRUCTURED candidates. It never decides compliance and
never invents values: insufficient evidence -> null / NEEDS_REVIEW.
"""
from __future__ import annotations

from typing import Any, Protocol


class VisionProvider(Protocol):
    name: str

    def extract_package_fields(
        self,
        image: Any,
        requested_fields: list[str],
        ocr_candidates: list[dict[str, Any]] | None = None,
        layout_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return structured candidates (schemas.validate_vision_candidate
        shape). May raise VisionError; the service converts failures into
        OCR-only fallback, never into blocked inspections."""
        ...

    def health_check(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """ONE minimal liveness probe (Stage 2C §2): text-only, no
        package images, short timeout. Returns {"ok": bool,
        "reason": str}. Optional for third-party providers — the
        health endpoint reports UNSUPPORTED when absent."""
        ...


class VisionError(Exception):
    """Provider failure (missing credentials, transport, model error)."""
