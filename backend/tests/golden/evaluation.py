"""Golden dataset loader + field-level evaluation metrics (Stage 3C §A).

Layout::

    backend/tests/golden/
      products/<product_id>/{front.jpg, back.jpg, side_1.jpg, ...}
      products/<product_id>/expected.json

``expected.json`` maps the twelve Stage 3C fields to expected values
(``null`` where a field genuinely does not exist — never invented).
Only products whose ``expected.json`` sets ``"verified": true`` are
measured; everything else is skipped with a reason. No real package
photographs are committed; with zero verified products every metric
reports ``n=0`` instead of any accuracy claim.

Metrics per field: exact match, normalized match (case/space/punct
folded), numeric/date match (MRP/quantity/dates compared as values,
not strings), extraction coverage (non-null extracted / expected
non-null), NEEDS_REVIEW rate. Aggregates never collapse to one
"AI accuracy" number.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

GOLDEN_FIELDS = (
    "product_name", "manufacturer", "quantity", "unit", "mrp",
    "manufacturing_date", "best_before", "fssai_license", "batch",
    "consumer_care", "ingredients", "veg_nonveg",
)

NUMERIC_FIELDS = {"mrp", "quantity"}
DATE_FIELDS = {"manufacturing_date", "best_before"}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def golden_root(base: str | Path | None = None) -> Path:
    """Root of the golden dataset (defaults to this package's dir)."""
    if base is not None:
        return Path(base)
    return Path(__file__).resolve().parent


def _normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = re.sub(r"[\s\-_.,;:|/\\]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _numeric_value(value: Any) -> float | None:
    try:
        cleaned = re.sub(r"[^0-9.\-]", "", str(value or ""))
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except (TypeError, ValueError):
        return None


def _date_value(value: Any) -> str | None:
    """Canonical date token for comparison (None when not date-like)."""
    if value is None:
        return None
    from app.services.ocr.fields import _find_date as _find
    from app.services.ocr.fields import _valid_date_token as _valid

    try:
        m = _find(str(value))
    except Exception:
        return None
    if not m:
        return None
    token = m.group(1)
    try:
        if not _valid(token):
            return None
    except Exception:
        return None
    return re.sub(r"[.\-]", "/", token).strip().lower()


def load_products(root: str | Path | None = None
                  ) -> list[dict[str, Any]]:
    """Load verified golden products: images + expected values.

    Returns [{product_id, images: [(bytes, label)], expected: {...}},
    ...] for verified products only. Unverified/missing/invalid
    products are skipped (never measured, never fabricated).
    """
    base = golden_root(root) / "products"
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for product_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        expected_path = product_dir / "expected.json"
        try:
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(expected, dict) or not expected.get("verified"):
            continue
        fields = expected.get("fields") if isinstance(
            expected.get("fields"), dict) else expected
        images: list[tuple[bytes, str]] = []
        for img_path in sorted(product_dir.iterdir()):
            if not img_path.is_file():
                continue
            if img_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if img_path.name == "expected.json":
                continue
            try:
                images.append((img_path.read_bytes(), img_path.stem))
            except Exception:
                continue
        if not images:
            continue
        out.append({"product_id": product_dir.name, "images": images,
                    "expected": {f: fields.get(f) for f in GOLDEN_FIELDS}})
    return out


def score_field(field: str, expected: Any,
                extracted: Any) -> dict[str, Any]:
    """Score one field: exact / normalized / numeric-or-date match."""
    exp_null = expected is None or (isinstance(expected, str)
                                    and not expected.strip())
    got_null = extracted is None or (isinstance(extracted, str)
                                     and not extracted.strip())
    if exp_null:
        return {"scored": False, "reason": "no ground truth for field",
                "exact": None, "normalized": None, "value_match": None}
    exact = (not got_null) and (str(extracted).strip() == str(expected).strip())
    normalized = (not got_null) and (
        _normalize_text(extracted) == _normalize_text(expected))
    value_match: bool | None = None
    if field in NUMERIC_FIELDS:
        exp_n, got_n = _numeric_value(expected), _numeric_value(extracted)
        value_match = (exp_n is not None and got_n is not None
                       and abs(exp_n - got_n) < 1e-9)
    elif field in DATE_FIELDS:
        exp_d, got_d = _date_value(expected), _date_value(extracted)
        value_match = (exp_d is not None and got_d is not None
                       and exp_d == got_d)
    return {"scored": True, "exact": bool(exact),
            "normalized": bool(normalized), "value_match": value_match}


def evaluate_product(expected: dict[str, Any],
                     extracted: dict[str, Any]) -> dict[str, Any]:
    """Field-level evaluation of one product's extraction result.

    ``extracted`` maps field -> {"value":..., "status":...} (status
    optional; NEEDS_REVIEW rate counts explicit NEEDS_REVIEW only).
    """
    per_field: dict[str, dict[str, Any]] = {}
    for field in GOLDEN_FIELDS:
        hit = extracted.get(field) if isinstance(extracted, dict) else None
        value = hit.get("value") if isinstance(hit, dict) else hit
        status = hit.get("status") if isinstance(hit, dict) else None
        scored = score_field(field, expected.get(field), value)
        scored["extracted_null"] = value is None or (
            isinstance(value, str) and not value.strip())
        scored["needs_review"] = (status == "NEEDS_REVIEW")
        per_field[field] = scored
    return {"fields": per_field, "summary": summarize(per_field)}


def summarize(per_field: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scored fields (counts only — never one accuracy %)."""
    scored = [v for v in per_field.values() if v.get("scored")]
    n = len(scored)
    return {
        "n_scored": n,
        "exact_matches": sum(1 for v in scored if v.get("exact")),
        "normalized_matches": sum(1 for v in scored if v.get("normalized")),
        "value_matches": sum(1 for v in scored
                             if v.get("value_match") is True),
        "extraction_coverage": (
            sum(1 for v in scored if not v.get("extracted_null")) / n
            if n else 0.0),
        "needs_review_rate": (
            sum(1 for v in scored if v.get("needs_review")) / n
            if n else 0.0),
    }
