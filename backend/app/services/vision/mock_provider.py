"""Deterministic mock vision provider (tests / local dev, no network)."""
from __future__ import annotations

from typing import Any

from app.services.vision.base import VisionError
from app.services.vision.schemas import validate_vision_candidate


class MockVisionProvider:
    """ scripted structured candidates; default = empty (NOT_DETECTED)."""

    name = "mock"

    def __init__(self, script: dict[str, dict[str, Any]] | None = None) -> None:
        self._script = dict(script or {})
        self.calls: list[dict[str, Any]] = []

    def health_check(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """Deterministic liveness: always ok (no network, no images)."""
        _ = timeout_s
        return {"ok": True, "reason": "mock provider (no network)"}

    def extract_package_fields(
        self,
        image: Any,
        requested_fields: list[str],
        ocr_candidates: list[dict[str, Any]] | None = None,
        layout_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if image is None:
            raise VisionError("mock provider received no image")
        self.calls.append({
            "requested_fields": list(requested_fields or []),
            "n_ocr_candidates": len(ocr_candidates or []),
            "has_layout": bool(layout_context),
            "n_panels": len(image) if isinstance(image, (list, tuple))
            else (0 if image is None else 1),
        })
        out: list[dict[str, Any]] = []
        for field in requested_fields or []:
            scripted = self._script.get(field)
            raw = dict(scripted) if scripted else {
                "field": field, "value": None, "status": "NOT_DETECTED",
                "confidence": 0.0, "evidence_text": None,
                "bbox": None, "image_id": None, "source": "vision",
            }
            raw.setdefault("field", field)
            cand = validate_vision_candidate(raw)
            if cand is not None:
                out.append(cand)
        return out
