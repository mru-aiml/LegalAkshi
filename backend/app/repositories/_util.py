"""Shared repository helpers."""
from __future__ import annotations

from typing import Any


def meta_confidence(value: Any) -> float | None:
    """Validate reviewer-supplied confidence: float in [0, 1], else None.

    Anything unparseable becomes NULL rather than a fabricated number.
    """
    try:
        if value is None:
            return None
        conf = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= conf <= 1.0:
        return conf
    return None


# Backwards-compatible alias used inside repositories.
_meta_confidence = meta_confidence
