"""Stage 2B tests: REAL IMAGE -> OCR + VISION -> RECONCILIATION.

The vision provider is invoked from the actual POST /ocr/extract
production path (route-level, after the untouched RapidOCR + Tesseract
flow). OCR behaviour when vision is disabled is byte-identical.
"""
from __future__ import annotations

import io
import itertools

import pytest

from app.services.ocr.base import OcrLine, OcrOutput
from app.services.package_intelligence import validators as validators_mod
from app.services.package_intelligence import vision_stage as vs_mod
from app.services.vision import service as vision_service
from app.services.vision.mock_provider import MockVisionProvider

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "off-2b"}

_UID = itertools.count(1)


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (240, 240), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _ScriptedOCR:
    """Deterministic stand-in for RapidOCR (real pipeline otherwise)."""

    name = "scripted-ocr"

    def __init__(self, lines: list[tuple[str, float]] | None = None):
        self._lines = lines if lines is not None else [("MRP Rs. 50", 0.9)]

    def extract(self, image_np, image_side):
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, box=None,
                           image=image_side)
                   for t, c in self._lines], engine=self.name)


def _patch_ocr(monkeypatch, lines=None):
    from app.services.ocr import rapidocr_provider as rapid_mod

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider",
                        lambda: _ScriptedOCR(lines))


def _enable_vision(monkeypatch, script=None, provider=None):
    from app.services.vision import provider as provider_mod

    prov = provider if provider is not None else MockVisionProvider(
        script=script)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: prov)
    return prov


def _extract(client, monkeypatch, ocr_lines=None, vision_script=None,
             query=""):
    _patch_ocr(monkeypatch, ocr_lines)
    _enable_vision(monkeypatch, vision_script)
    return client.post(
        f"/api/v1/ocr/extract{query}",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER)


# --- 1. vision enabled integration ---


def test_1_vision_enabled_end_to_end(monkeypatch):
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    out = _extract(
        client, monkeypatch,
        ocr_lines=[("NET QUANTITY 270 g", 0.9), ("MRP Rs. 108", 0.88)],
        vision_script={
            "quantity": {"field": "quantity", "value": "270",
                         "unit": "g", "status": "DETECTED",
                         "confidence": 0.96,
                         "evidence_text": "NET QUANTITY 270 g"}},
        query="?food=true").json()
    vision = out.get("vision", {})
    assert vision.get("vision_enabled") is True
    assert vision.get("vision_calls", 0) >= 1
    assert vision.get("vision_calls", 0) <= 6
    assert "reconciliation" in out
    # §15 diagnostics keys, no secrets.
    for key in ("vision_enabled", "vision_provider", "vision_calls",
                "vision_latency_ms", "ocr_calls", "fields"):
        assert key in vision, key
    assert "api_key" not in str(vision).lower()


# --- 2. vision disabled = OCR-only ---
def test_2_vision_disabled_ocr_only(monkeypatch):
    from conftest import make_client, make_repo

    from app.services.vision import provider as provider_mod

    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: None)
    _patch_ocr(monkeypatch, [("MRP Rs. 50", 0.9)])
    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    assert out["fields"]["mrp"] == {"value": "50", "provenance": "OCR",
                                    "confidence": 0.9}
    assert out["vision"]["vision_enabled"] is False
    assert out["vision"]["vision_status"] == "unavailable"
    assert "reconciliation" not in out


# --- 3. provider unavailable ---
def test_3_provider_unavailable_fallback(monkeypatch):
    from conftest import make_client, make_repo

    _patch_ocr(monkeypatch, [("MRP Rs. 50", 0.9)])
    _enable_vision(monkeypatch, provider=_Unavailable())
    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    assert out["status"] == "OK"
    assert out["fields"]["mrp"]["value"] == "50"
    assert out["vision"]["vision_enabled"] is False


class _Unavailable:
    name = "down"

    def extract_package_fields(self, *a, **k):
        raise RuntimeError("model down")


# --- 4. vision timeout ---
def test_4_vision_timeout_fallback(monkeypatch):
    from conftest import make_client, make_repo

    _patch_ocr(monkeypatch, [("MRP Rs. 50", 0.9)])
    _enable_vision(monkeypatch, provider=_Slow())
    real_enhance = vs_mod.enhance_ocr_with_vision
    monkeypatch.setattr(
        vs_mod, "enhance_ocr_with_vision",
        lambda result, raws, reqs, inspection_id=None, provider=None,
        per_call_timeout_s=30.0, overall_timeout_s=100.0:
        real_enhance(result, raws, reqs, inspection_id, provider,
                     per_call_timeout_s=0.05, overall_timeout_s=0.2))
    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    assert out["status"] == "OK"
    assert out["fields"]["mrp"]["value"] == "50"


class _Slow:
    name = "slow"
    model = "slow-1"

    def extract_package_fields(self, *a, **k):
        import time as _t

        _t.sleep(5)
        return []


def test_4_direct_timeout_unit():
    summary = vision_service.extract_with_vision(
        _Slow(), [(object(), "front")], ["mrp"],
        inspection_id=f"timeout-{next(_UID)}",
        group_override="B_declarations", per_call_timeout_s=0.05)
    assert summary["candidates"] == [] and summary["calls"] == 0
    assert "timed out" in (summary["error"] or "")


# --- 5. malformed response ---
def test_5_malformed_response_dropped():
    # Malformed vision payloads validate to nothing: OCR stands, the
    # reconciliation records no phantom fields.
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": "50", "confidence": 0.9,
                    "status": "DETECTED", "image": "front",
                    "box": None}}},
        [{"nonsense": 1}, "junk", None], None)
    assert rec["fields"] == {}
    assert rec["dropped_vision"] == []


# --- 6/7. agreement / conflict ---
def test_6_agreement_detected_with_sources():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "quantity": {"value": "270", "confidence": 0.9,
                         "status": "DETECTED", "image": "back",
                         "box": None},
            "unit": {"value": "g"}}},
        [{"field": "quantity", "value": "270", "unit": "g",
          "status": "DETECTED", "confidence": 0.96,
          "evidence_text": "NET QUANTITY 270 g",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    hit = rec["fields"]["quantity"]
    assert hit["status"] == "DETECTED"
    assert hit["final_value"] == "270"
    assert set(hit["sources"]) == {"rapidocr", "vision"}
    assert hit["agreement"] == "AGREE"


def test_7_conflict_preserves_both():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "quantity": {"value": "270", "confidence": 0.9,
                         "status": "DETECTED", "image": "back",
                         "box": None},
            "unit": {"value": "g"}}},
        [{"field": "quantity", "value": "210", "unit": "g",
          "status": "DETECTED", "confidence": 0.9,
          "evidence_text": "NET QUANTITY 210 g",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    hit = rec["fields"]["quantity"]
    assert hit["status"] == "NEEDS_REVIEW"
    assert hit["final_value"] is None
    assert {c["value"] for c in hit["candidates"]} == {"270", "210"}
    assert "conflicting candidates" in hit["needs_review_reason"]


# --- 8. vision-only strong candidate ---
def test_8_vision_only_strong_evidence():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": None, "confidence": None,
                    "status": "NOT_DETECTED"}}},
        [{"field": "mrp", "value": "108", "unit": "INR",
          "status": "DETECTED", "confidence": 0.95,
          "evidence_text": "MRP Rs. 108",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    assert rec["fields"]["mrp"]["status"] == "DETECTED"
    weak = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": None, "confidence": None,
                    "status": "NOT_DETECTED"}}},
        [{"field": "mrp", "value": "108", "unit": "INR",
          "status": "DETECTED", "confidence": 0.5,
          "evidence_text": "MRP Rs. 108",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    assert weak["fields"]["mrp"]["status"] == "NEEDS_REVIEW"


# --- 9-17. field-specific protection ---
def test_9_quantity_standalone_one_rejected():
    ok, reason = validators_mod.validate_quantity_candidate(
        "1", "g", "1 g")
    assert not ok and "small count" in reason
    ok, _ = validators_mod.validate_quantity_candidate(
        "1", "kg", "NET WT 1 kg")
    assert ok


def test_10_batch_no_rejected():
    for bad in ("No", "no", "NA", "n/a", "-"):
        ok, _ = validators_mod.validate_batch_candidate(
            bad, "Batch No")
        assert not ok, bad
    ok, _ = validators_mod.validate_batch_candidate("B12", "Batch No B12")
    assert ok


def test_10_batch_no_rejected_in_ocr_extraction():
    from app.services.ocr.base import OcrLine
    from app.services.ocr.fields import extract_fields_detailed

    lines = [OcrLine(text="Batch No", confidence=0.9, image="back")]
    det = extract_fields_detailed(lines)
    assert det["batch_lot"]["value"] is None


def test_11_invalid_date_rejected():
    ok, _ = validators_mod.validate_date_candidate("70.16", "MFD 70.16")
    assert not ok
    ok, _ = validators_mod.validate_date_candidate("05/2024", "MFD 05/2024")
    assert ok


def test_12_mrp_without_context_rejected():
    ok, reason = validators_mod.validate_mrp_candidate("108", "108 only")
    assert not ok and "context" in reason
    ok, _ = validators_mod.validate_mrp_candidate("108", "MRP Rs. 108")
    assert ok


def test_13_fssai_context_recovery():
    ok, _ = validators_mod.validate_fssai_candidate(
        "10012043001234", "FSSAI Lic No 10012043001234")
    assert ok
    ok, reason = validators_mod.validate_fssai_candidate(
        "10012043001234", "random digits line")
    assert not ok and "context" in reason


def test_14_manufacturer_opener_recovery():
    ok, _ = validators_mod.validate_manufacturer_candidate(
        "Acme Foods Pvt Ltd", "Manufactured by Acme Foods Pvt Ltd")
    assert ok
    ok, reason = validators_mod.validate_manufacturer_candidate(
        "NESTLEINDIA LIMITED B", "some brand-like line")
    assert not ok and "maker context" in reason
    assert validators_mod.normalize_maker_spacing(
        "NESTLEINDIA LIMITED B") == "NESTLEINDIA LIMITED"


def test_15_ingredient_contamination():
    ok, reason = validators_mod.validate_ingredient_candidate(
        "Sugar, Salt, Protein 12g per 100g Approximate Values",
        "INGREDIENTS: Sugar ... Nutrition per 100g ...")
    assert not ok
    ok, _ = validators_mod.validate_ingredient_candidate(
        "Sugar, Salt, Wheat Flour", "INGREDIENTS: Sugar, Salt")
    assert ok


def test_16_nutrition_table_structure():
    ok, _ = validators_mod.validate_nutrition_candidate(
        "energy", "120", "kcal", "Energy 120 kcal per 100g Nutrition")
    assert ok
    ok, reason = validators_mod.validate_nutrition_candidate(
        "energy", "120", "kcal", "Protein row evidence only")
    assert not ok and "row" in reason


def test_17_veg_symbol():
    ok, _ = validators_mod.validate_veg_nonveg_candidate(
        "VEG", "green dot symbol in square logo")
    assert ok
    ok, _ = validators_mod.validate_veg_nonveg_candidate(
        "UNKNOWN", "symbol unclear")
    assert not ok
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {}},
        [{"field": "veg_nonveg", "value": "UNKNOWN",
          "status": "NEEDS_REVIEW", "confidence": 0.4,
          "evidence_text": "unclear", "image_id": "front",
          "source": "vision", "provider": "mock", "model": None}],
        None)
    assert rec["fields"]["veg_nonveg"]["status"] == "NOT_DETECTED"


# --- 18/19. cache + call budget ---
def test_18_per_inspection_cache():
    prov = MockVisionProvider(script={
        "mrp": {"field": "mrp", "value": "50", "status": "DETECTED",
                "confidence": 0.9, "evidence_text": "MRP Rs. 50"}})
    iid = f"cache-{next(_UID)}"
    s1 = vision_service.extract_with_vision(
        prov, [(object(), "front")], ["mrp"], inspection_id=iid,
        group_override="B_declarations")
    n_calls = len(prov.calls)
    s2 = vision_service.extract_with_vision(
        prov, [(object(), "front")], ["mrp"], inspection_id=iid,
        group_override="B_declarations")
    assert len(prov.calls) == n_calls
    assert s2["cached"] == 1 and s1["candidates"] == s2["candidates"]


def test_19_six_call_maximum():
    # One invocation spanning every group plus the unmapped tail is
    # capped at 6 provider calls.
    from app.services.vision.schemas import VISION_FIELD_GROUPS

    prov = MockVisionProvider()
    iid = f"budget-{next(_UID)}"
    fields = [members[0] for members in VISION_FIELD_GROUPS.values()]
    fields += [f"custom_{i}" for i in range(10)]
    summary = vision_service.extract_with_vision(
        prov, [(object(), "front")], fields, inspection_id=iid)
    assert summary["calls"] <= 6
    # Stage 2B group planner is capped the same way.
    plan = vs_mod.plan_groups(
        [f"custom_{i}" for i in range(30)])
    assert len(plan) <= 6


# --- 20. readiness after reconciliation ---
def test_20_readiness_after_reconciliation(monkeypatch):
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    reqs = client.get("/api/v1/analysis/requirements?food=true").json()
    statuses = {r.get("ocr_key") or r.get("field"): {"status": "DETECTED"}
                for r in reqs.get("required", [])}
    from app.services.package_intelligence import service as pi_mod

    ready = pi_mod.readiness(statuses, reqs)
    assert ready["ready"] is True
    out = _extract(
        client, monkeypatch,
        ocr_lines=[("MRP Rs. 108", 0.9)],
        vision_script={},
        query="?food=true").json()
    assert "vision" in out and "reconciliation" in out
    readiness = out["reconciliation"]["readiness"]
    assert "ready" in readiness and "blocked_fields" in readiness


# --- 21/22. frontend badges + conflict display (static) ---
def _scan_text():
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent.parent
            / "artifacts" / "nutricheck" / "src" / "pages"
            / "officer-scan.tsx").read_text(encoding="utf-8")


def test_21_frontend_evidence_badges():
    scan = _scan_text()
    assert "OCR + AI agreement" in scan
    assert "Vision-supported" in scan
    assert "Auto-detected" in scan  # continuity pin retained
    assert "text-vision-status" in scan


def test_22_frontend_conflict_display():
    scan = _scan_text()
    assert "OCR/AI conflict" in scan
    assert "conflict-panel" in scan
    assert "View evidence" in scan
    # Stage 2C §18 wording (supersedes the older subtitle).
    assert "verify only the highlighted fields before analysis" in scan


def test_vision_error_never_leaks_secrets(monkeypatch):
    monkeypatch.setenv("LEGALAKSHI_VISION_API_KEY", "sk-test-secret-xyz")
    from app.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        err = vs_mod._sanitize_error(
            RuntimeError("call failed with sk-test-secret-xyz attached"))
        assert "sk-test-secret-xyz" not in err
    finally:
        config_mod.get_settings.cache_clear()


def test_only_relevant_groups_requested(monkeypatch):
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    prov = _enable_vision(monkeypatch, script={})
    out = _extract(
        client, monkeypatch,
        ocr_lines=[("MRP Rs. 108", 0.9), ("MFD 05/2024", 0.9),
                   ("NET QUANTITY 270 g", 0.9)],
        vision_script={}, query="?food=true").json()
    requested = out["vision"].get("fields_requested", [])
    # Resolved high-value fields cost no calls; groups stay bounded.
    assert out["vision"]["vision_calls"] <= 6
    assert prov is not None and requested is not None
