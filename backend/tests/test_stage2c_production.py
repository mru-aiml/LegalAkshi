"""Stage 2C tests: production diagnostics + real-package field fixes.

No self-training, no new providers, no OCR budget changes, no engine
changes. Covers the exact failures seen on the real package.
"""
from __future__ import annotations

import io
import itertools

from app.services.package_intelligence import regions as regions_mod
from app.services.package_intelligence import validators as validators_mod
from app.services.package_intelligence import vision_stage as vs_mod
from app.services.vision import provider as provider_mod
from app.services.vision.mock_provider import MockVisionProvider

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "off-2c"}
CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "c-2c"}

_UID = itertools.count(100)


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (240, 240), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_settings(monkeypatch, provider="", model="", key="",
                   enabled=False):
    from app.core import config as config_mod

    monkeypatch.setenv("LEGALAKSHI_VISION_PROVIDER", provider)
    monkeypatch.setenv("LEGALAKSHI_VISION_MODEL", model)
    monkeypatch.setenv("LEGALAKSHI_VISION_ENABLED",
                       "true" if enabled else "false")
    if key:
        monkeypatch.setenv("LEGALAKSHI_VISION_API_KEY", key)
    else:
        monkeypatch.delenv("LEGALAKSHI_VISION_API_KEY", raising=False)
    # NOTE: conftest's autouse fixture also clears the settings cache
    # per test, so no cross-test pollution via lru_cache.
    config_mod.get_settings.cache_clear()


# --- vision-status states ---
def test_status_disabled(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["status"] == "DISABLED"
    assert body["enabled"] is False and body["reachable"] is False
    assert body["api_key_present"] is False


def test_status_not_configured_missing_key(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="gemini", model="gemini-2.0-flash",
                   enabled=True)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["status"] == "NOT_CONFIGURED"
    assert body["reachable"] is False


def test_status_not_configured_missing_model(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="gemini", key="k", enabled=True)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["status"] == "NOT_CONFIGURED"


def test_status_invalid_configuration(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="mystery", enabled=True)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["status"] == "INVALID_CONFIGURATION"


def test_status_available_mock(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="mock", enabled=True)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["status"] == "AVAILABLE"
    assert body["reachable"] is True
    assert body["api_key_present"] in (True, False)
    assert "api_key" not in str(
        {k: v for k, v in body.items()
         if k != "api_key_present"}).lower()


def test_status_never_leaks_key_material(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="gemini", model="m",
                   key="sk-live-abcdef123456", enabled=True)
    client = make_client(make_repo())
    body = client.get("/api/v1/package-intelligence/vision-status",
                      headers=OFFICER).json()
    assert body["api_key_present"] is True
    assert "sk-live-abcdef123456" not in str(body)


# --- health endpoint ---
def _health(client, monkeypatch, **env):
    _mock_settings(monkeypatch, **env)
    return client.post("/api/v1/package-intelligence/vision-health",
                       headers=OFFICER)


def test_health_disabled(monkeypatch):
    from conftest import make_client, make_repo

    out = _health(make_client(make_repo()), monkeypatch).json()
    assert out["status"] == "UNAVAILABLE"


def test_health_missing_key(monkeypatch):
    from conftest import make_client, make_repo

    out = _health(make_client(make_repo()), monkeypatch, provider="gemini",
                  model="m", enabled=True).json()
    assert out["status"] == "UNAVAILABLE"


def test_health_missing_model(monkeypatch):
    from conftest import make_client, make_repo

    out = _health(make_client(make_repo()), monkeypatch, provider="gemini",
                  key="k", enabled=True).json()
    assert out["status"] == "UNAVAILABLE"


def test_health_provider_unavailable(monkeypatch):
    from conftest import make_client, make_repo

    class _Down:
        name = "down"

        def health_check(self, timeout_s=10.0):
            raise RuntimeError("connection refused")

    _mock_settings(monkeypatch, provider="mock", enabled=True)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: _Down())
    client = make_client(make_repo())
    out = client.post("/api/v1/package-intelligence/vision-health",
                      headers=OFFICER).json()
    assert out["status"] == "UNAVAILABLE"
    assert "connection refused" in out["reason"]
    assert "sk-" not in out["reason"]


def test_health_successful_mock(monkeypatch):
    from conftest import make_client, make_repo

    out = _health(make_client(make_repo()), monkeypatch, provider="mock",
                  enabled=True).json()
    assert out["status"] == "AVAILABLE"
    assert out["provider"] == "mock"


def test_health_malformed_response(monkeypatch):
    from conftest import make_client, make_repo

    class _Weird:
        name = "weird"

        def health_check(self, timeout_s=10.0):
            return {"unexpected": "shape"}

    _mock_settings(monkeypatch, provider="mock", enabled=True)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: _Weird())
    client = make_client(make_repo())
    out = client.post("/api/v1/package-intelligence/vision-health",
                      headers=OFFICER).json()
    assert out["status"] == "UNAVAILABLE"


def test_health_rbac():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    assert client.post(
        "/api/v1/package-intelligence/vision-health",
        headers=CONSUMER).status_code == 403
    assert client.post(
        "/api/v1/package-intelligence/vision-health").status_code in (
            401, 403)


# --- image bytes reach vision + call logging ---
def test_image_bytes_reach_provider(monkeypatch):
    seen: list = []

    class _Spy(MockVisionProvider):
        def extract_package_fields(self, image, requested_fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            seen.append(image)
            return super().extract_package_fields(
                image, requested_fields, ocr_candidates, layout_context)

    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="mock", enabled=True)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: _Spy())
    from app.services.ocr import rapidocr_provider as rapid_mod
    from app.services.ocr.base import OcrLine, OcrOutput

    class _OCR:
        name = "spy-ocr"

        def extract(self, image_np, image_side):
            return OcrOutput(lines=[], engine=self.name)

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider", lambda: _OCR())
    client = make_client(make_repo())
    raw = _png_bytes()
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", raw, "image/png")},
        headers=OFFICER).json()
    assert seen, "provider received no image payload"
    assert isinstance(seen[0], (bytes, bytearray)) and len(seen[0]) > 0
    logs = (out.get("vision") or {}).get("calls_log", [])
    assert logs, "call log missing"
    entry = logs[0]
    for key in ("image_id", "group", "image_bytes_size", "mime_type",
                "provider", "model", "latency_ms", "status"):
        assert key in entry, key
    assert entry["image_bytes_size"] > 0
    assert entry["mime_type"] in ("image/png", "image/jpeg",
                                  "application/octet-stream")


def test_call_log_never_embeds_contents(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="mock", enabled=True)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: MockVisionProvider())
    from app.services.ocr import rapidocr_provider as rapid_mod
    from app.services.ocr.base import OcrOutput

    class _OCR:
        name = "empty-ocr"

        def extract(self, image_np, image_side):
            return OcrOutput(lines=[], engine=self.name)

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider", lambda: _OCR())
    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    blob = str((out.get("vision") or {}).get("calls_log", []))
    assert "JFIF" not in blob and "\x89PNG" not in blob


# --- region proposals + crops ---
def _ocr_result_with_boxes():
    mrp_box = [[10, 10], [60, 10], [60, 30], [10, 30]]
    ing_box = [[10, 100], [80, 100], [80, 120], [10, 120]]
    return {
        "diagnostics": {"images": {"back": {
            "stage_dimensions": [200, 200],
            "ocr_boxes": [
                {"text": "MRP Rs. 108", "box": mrp_box},
                {"text": "INGREDIENTS", "box": ing_box}],
            "candidate_boxes": {
                "mrp": [{"text": "MRP Rs. 108", "box": mrp_box}],
                "ingredient_heading": [{"text": "INGREDIENTS",
                                        "box": ing_box}],
                "date": [], "fssai": [], "care": [], "nutrition": []}}}},
        "timings": {"images": {}}}


def test_region_proposals_from_anchors():
    regions = regions_mod.propose_regions(_ocr_result_with_boxes(), "back")
    assert "MRP" in regions and "rect" in regions["MRP"]
    assert "INGREDIENTS" in regions
    x0, y0, x1, y1 = regions["MRP"]["rect"]
    assert x1 > x0 and y1 > y0
    rtype, hit = regions_mod.region_for_group("B_declarations", regions)
    assert rtype == "MRP"
    assert set(regions_mod.REGION_TYPES) >= {
        "PRODUCT_FRONT", "QUANTITY", "MRP", "DATE", "BATCH",
        "MANUFACTURER", "FSSAI", "CONSUMER_CARE", "INGREDIENTS",
        "NUTRITION", "VEG_SYMBOL", "STORAGE", "PREPARATION", "MARKETING"}


def test_region_fallback_full_image():
    regions = regions_mod.propose_regions({"diagnostics": {"images": {}}},
                                          "front")
    assert regions == {}
    rtype, hit = regions_mod.region_for_group("A_product", regions)
    assert rtype is None and hit is None


def test_crop_differs_from_full_and_valid():
    crop = regions_mod.crop_region_jpeg(_png_bytes(), [10, 10, 60, 60],
                                        [240, 240])
    assert crop is not None and crop[:2] == b"\xff\xd8"
    assert regions_mod.guess_mime(_png_bytes()) == "image/png"
    assert regions_mod.crop_region_jpeg(_png_bytes(), [0, 0, 1, 1],
                                        [240, 240]) is None


# --- exact real-package failures ---
def test_quantity_bare_numbers_rejected():
    for bad_ev in ("1", "4", "8", "270", "Pack of 4", "4 servings",
                   "1 packet", "Sugar 8g per serve"):
        ok, _ = validators_mod.validate_quantity_candidate(
            bad_ev.split()[0], "g", bad_ev)
        assert not ok, bad_ev
    ok, _ = validators_mod.validate_quantity_candidate(
        "270", "g", "NET QUANTITY 270 g")
    assert ok


def test_mrp_bare_108_rejected():
    ok, reason = validators_mod.validate_mrp_candidate("108", "108")
    assert not ok and "context" in reason
    ok, _ = validators_mod.validate_mrp_candidate("108", "MRP Rs. 108")
    assert ok


def test_batch_generic_words_rejected():
    for bad in ("No", "NET", "NOT", "NEW", "PACK", "LOT"):
        ok, _ = validators_mod.validate_batch_candidate(bad, "Batch No")
        assert not ok, bad
    ok, _ = validators_mod.validate_batch_candidate(
        "LOT123", "Lot No. LOT123")
    assert ok


def test_manufacturer_conflict_preserves_both():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "manufacturer": {"value": "NESTLEINDIA LIMITED B",
                             "confidence": 0.8, "status": "NEEDS_REVIEW",
                             "image": "back", "box": None}}},
        [{"field": "manufacturer", "value": "NESTLÉ INDIA LIMITED",
          "status": "DETECTED", "confidence": 0.9,
          "evidence_text": "Manufactured by NESTLÉ INDIA LIMITED",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    hit = rec["fields"]["manufacturer"]
    assert hit["status"] == "NEEDS_REVIEW"
    assert {c["value"] for c in hit["candidates"]} == {
        "NESTLEINDIA LIMITED B", "NESTLÉ INDIA LIMITED"}


def test_fssai_lic_n0_context_tolerated():
    ok, _ = validators_mod.validate_fssai_candidate(
        "10012043001234", "FSSA LIC N0 10012043001234")
    assert ok


def test_care_normalization_preserves_digits():
    assert validators_mod.normalize_care_number("1800 103 1947") == \
        "18001031947"
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "consumer_care": {"value": "1800 103 1947",
                              "confidence": 0.87,
                              "status": "DETECTED", "image": "back",
                              "box": None}}},
        [{"field": "consumer_care", "value": "18001031947",
          "status": "DETECTED", "confidence": 0.9,
          "evidence_text": "Toll Free 1800 103 1947",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    assert rec["fields"]["consumer_care"]["status"] == "DETECTED"
    assert rec["fields"]["consumer_care"]["agreement"] == "AGREE"


def test_ingredients_nutrition_contamination_rejected():
    bad = ("INGREDIENTS Sugar, Salt Sodium Total Fat Fiber "
           "Added Sugars Approximate Values per 100g")
    ok, reason = validators_mod.validate_ingredient_candidate(
        bad, "INGREDIENTS Sugar, Salt Nutrition Information ...")
    assert not ok


def test_nutrition_table_context_accepted():
    ok, _ = validators_mod.validate_nutrition_candidate(
        "energy", "120", "kcal",
        "NUTRITIONAL INFORMATION per 100g Energy 120 kcal")
    assert ok
    ok, reason = validators_mod.validate_nutrition_candidate(
        "energy", "120", "kcal", "Protein row only")
    assert not ok


def test_dates_reject_arbitrary_numbers():
    for bad in ("70.16", "108", "325", "2021"):
        ok, _ = validators_mod.validate_date_candidate(bad, "MFD " + bad)
        assert not ok, bad
    ok, _ = validators_mod.validate_date_candidate("05/24", "MFD 05/24")
    assert ok


def test_cross_field_gate_demotes_anchorless_detection():
    fields = {
        "mrp": {"field": "mrp", "final_value": "108", "unit": "INR",
                "status": "DETECTED", "confidence": 0.9,
                "sources": ["vision"], "candidates": [],
                "agreement": "SINGLE_SOURCE", "evidence": ["108"],
                "needs_review_reason": ""},
        "quantity": {"field": "quantity", "final_value": "270",
                     "unit": "g", "status": "DETECTED",
                     "confidence": 0.9, "sources": ["rapidocr"],
                     "candidates": [], "agreement": "SINGLE_SOURCE",
                     "evidence": ["NET QUANTITY 270 g"],
                     "needs_review_reason": ""}}
    out = validators_mod.cross_validate_final(fields)
    assert out["mrp"]["status"] == "NEEDS_REVIEW"
    assert "cross-field" in out["mrp"]["needs_review_reason"]
    # Pure-OCR entries are never demoted here.
    assert out["quantity"]["status"] == "DETECTED"


# --- agreement / conflict / budget / partial ---
def test_agreement_detected():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": "108", "confidence": 0.9,
                    "status": "DETECTED", "image": "back",
                    "box": None}}},
        [{"field": "mrp", "value": "108", "unit": "INR",
          "status": "DETECTED", "confidence": 0.94,
          "evidence_text": "MRP Rs. 108",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    assert rec["fields"]["mrp"]["status"] == "DETECTED"
    assert rec["fields"]["mrp"]["agreement"] == "AGREE"


def test_conflict_visible():
    rec = vs_mod.overlay_reconciliation(
        {"fields_detailed": {
            "mrp": {"value": "108", "confidence": 0.9,
                    "status": "DETECTED", "image": "back",
                    "box": None}}},
        [{"field": "mrp", "value": "180", "unit": "INR",
          "status": "DETECTED", "confidence": 0.9,
          "evidence_text": "MRP Rs. 180",
          "image_id": "back", "source": "vision",
          "provider": "mock", "model": None}],
        None)
    hit = rec["fields"]["mrp"]
    assert hit["status"] == "NEEDS_REVIEW"
    assert hit["final_value"] is None
    assert len(hit["candidates"]) == 2


def test_max_vision_call_budget(monkeypatch):
    _mock_settings(monkeypatch, provider="mock", enabled=True)
    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: MockVisionProvider())
    from app.services.ocr import rapidocr_provider as rapid_mod
    from app.services.ocr.base import OcrLine, OcrOutput

    class _OCR:
        name = "empty"

        def extract(self, image_np, image_side):
            return OcrOutput(lines=[], engine=self.name)

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider", lambda: _OCR())
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    assert (out.get("vision") or {}).get("vision_calls", 0) <= 6


def test_partial_status_when_group_fails(monkeypatch):
    from conftest import make_client, make_repo

    _mock_settings(monkeypatch, provider="mock", enabled=True)

    class _Partial(MockVisionProvider):
        def __init__(self):
            super().__init__(script={})
            self._n = 0

        def extract_package_fields(self, image, requested_fields,
                                   ocr_candidates=None,
                                   layout_context=None):
            self._n += 1
            if self._n == 1:
                raise RuntimeError("group exploded")
            return super().extract_package_fields(
                image, requested_fields, ocr_candidates, layout_context)

    monkeypatch.setattr(provider_mod, "get_vision_provider",
                        lambda explicit=None: _Partial())
    from app.services.ocr import rapidocr_provider as rapid_mod
    from app.services.ocr.base import OcrLine, OcrOutput

    class _OCR:
        name = "weak"

        def extract(self, image_np, image_side):
            return OcrOutput(
                lines=[OcrLine(text="MRP Rs. 108", confidence=0.5,
                               box=None, image=image_side)],
                engine=self.name)

    monkeypatch.setattr(rapid_mod, "RapidOCRProvider", lambda: _OCR())
    client = make_client(make_repo())
    out = client.post(
        "/api/v1/ocr/extract?food=true",
        files={"front_image": ("f.png", _png_bytes(), "image/png")},
        headers=OFFICER).json()
    vision = out.get("vision", {})
    if vision.get("vision_calls", 0) > 0:
        assert vision.get("vision_status_detail") == "partial"
        assert vision.get("vision_error")


# --- requirements endpoint (§19) ---
def test_requirements_endpoint_works():
    from conftest import make_client, make_repo

    client = make_client(make_repo())
    res = client.get("/api/v1/analysis/requirements?food=true")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body.get("required"), list)
    assert len(body["required"]) > 0
    assert all("field" in r for r in body["required"])


# --- frontend static pins (§3/§18/§20) ---
def _scan():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    return (root / "artifacts" / "nutricheck" / "src" / "pages"
            / "officer-scan.tsx").read_text(encoding="utf-8")


def _app():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    return (root / "artifacts" / "nutricheck" / "src"
            / "App.tsx").read_text(encoding="utf-8")


def test_frontend_explicit_vision_messaging():
    scan = _scan()
    assert "Vision AI active — OCR and visual evidence are being reconciled" \
        in scan
    assert "Vision AI unavailable — OCR-only extraction is being used" \
        in scan
    assert "Vision AI partially unavailable — affected fields require " \
        "review" in scan


def test_frontend_readiness_no_manual_fallback():
    scan = _scan()
    assert "verify only the highlighted fields before analysis" in scan
    assert "Rule requirements temporarily unavailable. Analysis " \
        "readiness cannot be fully determined" in scan
    assert "review every field manually" not in scan


def test_frontend_vision_profile_card():
    app = _app()
    assert "vision-ai-card" in app
    assert "Test Vision AI" in app
    assert "vision-ai-status" in app
    assert "visionHealth" in app or "vision-health" in app
