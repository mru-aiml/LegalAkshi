"""Learning evaluation exports + active-learning queue (Parts L/N).

No automatic training, ever. Verified corrections export to an
evaluation dataset; the review queue prioritises useful examples with
explainable reasons (HIGH/MEDIUM/LOW).
"""
from __future__ import annotations

from typing import Any

from app.services.learning.error_patterns import classify_failure
from app.services.learning.schemas import to_learning_example


def export_verified_dataset(
        corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only verified=true rows enter the trusted learning dataset."""
    out: list[dict[str, Any]] = []
    for corr in corrections or []:
        if not corr.get("verified"):
            continue
        out.append(to_learning_example(corr, classify_failure(corr)))
    return out


def _priority(corr: dict[str, Any], required_fields: set[str]) -> tuple[str,
                                                                        str]:
    snap = corr.get("evidence_snapshot") or {}
    snap_text = str(snap)
    field = str(corr.get("field_key", ""))
    conf = corr.get("original_confidence")
    if isinstance(snap, dict) and snap.get("vision_value") not in (None, "") \
            and str(snap.get("vision_value")) != \
            str(corr.get("original_value", "")):
        return "HIGH", f"{field}: OCR/vision conflict needs evaluation"
    if field in required_fields:
        return "HIGH", f"{field}: high-impact required field corrected"
    if isinstance(conf, (int, float)) and conf < 0.6:
        return "MEDIUM", f"{field}: low-confidence extraction"
    if "contaminat" in snap_text.lower() or field == "ingredients":
        return "MEDIUM", f"{field}: low extraction coherence"
    if "poor" in snap_text.lower() or "readability" in snap_text.lower():
        return "MEDIUM", f"{field}: poor image quality example"
    return "LOW", f"{field}: optional/routine feedback"


def review_queue(corrections: list[dict[str, Any]],
                 required_fields: set[str] | None = None,
                 limit: int = 50) -> list[dict[str, Any]]:
    """Prioritised unverified corrections for evaluation/review."""
    required_fields = required_fields or set()
    pending = [c for c in corrections or [] if not c.get("verified")]
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    scored = []
    for corr in pending:
        prio, reason = _priority(corr, required_fields)
        scored.append((rank[prio], corr.get("created_at", ""), prio, reason,
                       corr))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [{"priority": prio, "reason": reason,
             "correction": {k: corr.get(k) for k in
                            ("id", "inspection_id", "product_id",
                             "field_key", "original_value",
                             "corrected_value", "original_status",
                             "original_confidence", "created_at")}}
            for _, _, prio, reason, corr in scored[:limit]]
