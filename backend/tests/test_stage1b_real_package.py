"""Stage-1B regressions: real-package extraction accuracy + OCR budget.

Maps to the observed 4-image package failure: 29 OCR passes / 176.6 s,
ingredient text contaminated with storage ("TO A CLEAN AIRTIGHT
CONTAINER ONCE OPENED") and consumer-care ("Forfeeback-Contacd:
ExecufiveConsumerCareCell") lines, plus MRP/date/care misses.

All provider interaction uses deterministic mock providers (no model,
no wall-clock assertions — CI machines vary). Performance is asserted
via provider-call counts and stage counts, never elapsed seconds.
"""
from __future__ import annotations

import io
import pathlib

from app.services.ocr.base import OcrLine, OcrOutput

# Real-package contaminated ingredient OCR (verbatim failure shapes).
REAL_INGREDIENT_LINES = [
    "REFINEDWHEATFLOUR(MAIDA)(663)",
    "TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.",
    "SUGAR REFINEDPALMOILMILK",
    "COHTAINSWHEATMILKSOYANDSULPHITE",
    "Forfeeback-Contacd:ExecufiveConsumerCareCell",
]


def _line(text: str, conf: float = 0.85, image: str = "back") -> OcrLine:
    return OcrLine(text=text, confidence=conf, box=None, image=image)


def _boxed(text: str, x0: float, y0: float, x1: float, y1: float,
           conf: float = 0.9, image: str = "back") -> OcrLine:
    return OcrLine(text=text, confidence=conf,
                   box=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                   image=image)


class _CountingProvider:
    """Mock provider: per-label Stage-1 lines + call log for budget tests."""

    name = "mock-ocr"

    def __init__(self, by_label):
        self._by_label = by_label
        self.calls: list[str] = []

    def extract(self, image_np, image_side):
        self.calls.append(str(image_side))
        label = str(image_side).split(":")[0]
        rows = self._by_label.get(label, [])
        lines = []
        for row in rows:
            if isinstance(row, OcrLine):
                lines.append(OcrLine(text=row.text,
                                     confidence=row.confidence,
                                     box=row.box, image=label))
            else:
                t, c = row
                lines.append(OcrLine(text=t, confidence=c, image=label))
        return OcrOutput(lines=lines, engine=self.name)


def _png_bytes(w: int = 900, h: int = 300, slot: int | None = None) -> bytes:
    # Distinct panels need distinct pixels (Stage 3A.5 dedup); slots are
    # 2D grid positions. Same slot twice = intentional duplicate.
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), "white")
    if slot is not None:
        col, row = slot % 3, (slot // 3) % 2
        x, y = 60 + col * 280, 50 + row * 120
        d = ImageDraw.Draw(img)
        d.rectangle([x, y, x + 180, y + 70], fill="black")
        d.text((x + 10, y + 20), f"P{slot}", fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------- ingredient contamination ---
def test_storage_text_excluded_from_ingredients():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: REFINED WHEAT FLOUR (MAIDA), SUGAR,"),
             _line("PALM OIL, MILK SOLIDS, SALT."),
             _line("TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED."),
             _line("STORE IN A COOL DRY PLACE.")]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    cleaned = ing["cleaned_text"] or ""
    assert "AIRTIGHT" not in cleaned.upper()
    assert "ONCE OPENED" not in cleaned.upper()
    assert "STORE IN" not in cleaned.upper()
    assert "REFINED WHEAT FLOUR" in cleaned
    assert ing["rejected_lines"], "excluded lines must be recorded"
    assert any("storage" in r["reason"].lower()
               for r in ing["rejected_lines"])
    for rej in ing["rejected_lines"]:
        assert set(("text", "reason", "confidence", "source_box")) <= set(rej)


def test_consumer_care_contamination_excluded():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: Refined wheat flour, sugar, palm oil,"),
             _line("Forfeeback-Contacd:ExecufiveConsumerCareCell"),
             _line("Consumer Care: 1800-103-1947"),
             _line("salt, mixed spices.")]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    cleaned = ing["cleaned_text"] or ""
    assert "Forfeeback" not in cleaned
    assert "Consumer Care" not in cleaned
    assert "1800" not in cleaned
    assert "palm oil" in cleaned.lower() or "Refined wheat" in cleaned
    # The block terminates at the first feedback/care opener, so one
    # recorded rejection (+ the stop) is the correct outcome.
    assert len(ing["rejected_lines"]) >= 1


def test_allergen_declaration_line_stops_ingredients():
    from app.services.ocr.food import extract_food_label

    lines = [_line("INGREDIENTS: Refined wheat flour, sugar, palm oil, salt."),
             _line("COHTAINSWHEATMILKSOYANDSULPHITE"),
             _line("MRP Rs. 50")]
    out = extract_food_label(lines)
    ing = out["fields"]["ingredients"]
    cleaned = ing["cleaned_text"] or ""
    assert "COHTAINS" not in cleaned
    assert "MRP" not in cleaned


def test_region_lines_contamination_guard():
    from app.services.ocr.food import extract_food_label

    region = [_line("REFINED WHEAT FLOUR (MAIDA), SUGAR, PALM OIL,"),
              _line("TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED."),
              _line("SALT, MIXED SPICES.")]
    out = extract_food_label([], region_lines=region)
    ing = out["fields"]["ingredients"]
    assert "AIRTIGHT" not in (ing["cleaned_text"] or "").upper()
    assert "SALT" in (ing["cleaned_text"] or "").upper()
    assert ing["rejected_lines"]


# --------------------------------- section stopping ---
def test_boundary_decision_storage_and_care():
    from app.services.ocr.food import ingredient_boundary_decision

    is_b, section, _ = ingredient_boundary_decision(
        "TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.")
    assert is_b and section == "STORAGE"
    is_b, section, _ = ingredient_boundary_decision(
        "Forfeeback-Contacd:ExecufiveConsumerCareCell")
    assert is_b
    is_b, section, _ = ingredient_boundary_decision(
        "Consumer Care Cell, call 1800-103-1947")
    assert is_b and section == "CONSUMER_CARE"


def test_boundary_never_stops_inside_real_declaration():
    from app.services.ocr.food import ingredient_boundary_decision

    # A genuine comma-separated declaration mentioning dairy must stay.
    is_b, _section, _ = ingredient_boundary_decision(
        "Refined wheat flour (Maida), sugar, palm oil, milk solids, salt.")
    assert not is_b


def test_section_stop_covers_all_required_markers():
    from app.services.ocr.food import match_section_boundary

    for marker, name in [
        ("STORE IN A COOL DRY PLACE", "STORAGE"),
        ("Best Before 9 months from manufacture", "BEST_BEFORE"),
        ("Batch No M50924A", "BATCH"),
        ("FSSAI Lic No. 10012043001234", "FSSAI"),
        ("MRP Rs. 50 (Incl. of all taxes)", "MRP"),
        ("Nutrition Information per 100g", "NUTRITION"),
        ("Mfd. by Nestle India Limited", "MANUFACTURER"),
    ]:
        assert match_section_boundary(marker) == name, marker


# --------------------------------- spacing reconstruction ---
def test_glued_ingredient_words_separated():
    from app.services.ocr.food import repair_ingredient_spacing

    repaired, notes = repair_ingredient_spacing("REFINEDWHEATFLOUR(MAIDA)")
    assert "REFINED" in repaired and "WHEAT" in repaired
    assert "FLOUR" in repaired and "(MAIDA)" in repaired
    assert notes


def test_bare_number_never_becomes_ins():
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients("Refined wheat flour (Maida) (663), salt")
    assert items[0]["ins_number"] is None
    assert "(663)" in items[0]["raw_segment"]


# --------------------------------- field-specific extraction ---
def test_quantity_keeps_unit_and_rejects_bare_numbers():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("NET WT 100 g")])
    assert out["quantity"]["value"] == "100"
    assert out["unit"]["value"] == "g"
    out2 = extract_fields([_line("100")])
    assert out2["quantity"]["value"] is None


def test_mrp_validated_price_only():
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [_line("MRP Rs. 50 (Incl. of all taxes)")])["mrp"]["value"] == "50"
    assert extract_fields([_line("MRP 1010005")])["mrp"]["value"] is None
    assert extract_fields([_line("250")])["mrp"]["value"] is None


def test_date_forms_and_fssai_and_maker_and_care():
    from app.services.ocr.fields import extract_fields

    assert extract_fields(
        [_line("MFD 05/2024")])["manufacturing_date"]["value"] == "05/2024"
    assert extract_fields(
        [_line("MFG May 2024")])["manufacturing_date"]["value"] == "May 2024"
    assert extract_fields(
        [_line("FSSAI Lic No. 10012043001234")]
    )["fssai_license"]["value"] == "10012043001234"
    assert extract_fields(
        [_line("FSSAI Lic No. 123")])["fssai_license"]["value"] is None
    maker = extract_fields(
        [_line("Mfd. by Nestle India Limited, Moga, Punjab 142001")]
    )["manufacturer"]["value"]
    assert maker and "Nestle" in maker
    assert extract_fields(
        [_line("Customer Care 1800-103-1947")]
    )["consumer_care"]["value"] == "1800-103-1947"


def test_product_name_never_from_declaration_lines():
    from app.services.ocr.fields import extract_fields

    out = extract_fields([_line("Nutrition Information per 100g"),
                          _line("INGREDIENTS: Refined wheat flour, sugar"),
                          _line("Sunfeast Biscuits")])
    assert out["product_name"]["value"] == "Sunfeast Biscuits"


def test_nutrition_separated_from_ingredients():
    from app.services.ocr.food import extract_food_label

    out = extract_food_label(
        [_line("INGREDIENTS: Refined wheat flour, sugar, salt."),
         _line("Nutrition Information per 100g:"),
         _line("Energy 450 kcal"),
         _line("Protein 8 g")])
    ing = out["fields"]["ingredients"]
    assert "Energy" not in (ing["cleaned_text"] or "")
    assert out["fields"]["nutrition"]["energy"]["value"] == "450 kcal"


def test_barcode_requires_checksum_evidence():
    from app.services.ocr.fields import (
        decode_barcode, find_barcode_candidates)

    cands = find_barcode_candidates([_line("8901058845123")])
    assert cands and cands[0]["ean13_checksum_valid"] in (True, False)
    # OCR digit runs are never presented as decoded barcodes.
    res = decode_barcode(digits="8901058845123")
    assert res["status"] == "NEEDS_MANUAL_SCAN"
    assert res["value"] is None
    # FSSAI numbers are not barcode candidates.
    assert find_barcode_candidates(
        [_line("FSSAI Lic No. 10012043001234")]) == []


def test_orientation_detected_from_box_geometry():
    from app.services.ocr.service import _detect_orientation

    honestly_wide = [_boxed("Refined wheat flour paragraph text here",
                            10, 10, 300, 28, image="back")]
    assert _detect_orientation(honestly_wide)["orientation"] == "0"
    sideways = [_boxed("x", 10, 10, 26, 300, image="back"),
                _boxed("y", 30, 10, 46, 300, image="back")]
    assert _detect_orientation(sideways)["transpose"] is True


# --------------------------------- call budget + timings ---
def test_call_budget_respected_four_images():
    from app.services.ocr import service

    provider = _CountingProvider({
        "front": [("Sunfeast Biscuits", 0.95), ("NET WT 100 g", 0.93)],
        "back": [("Ingredients: Refined wheat flour, sugar, salt,", 0.8),
                 ("MRP Rs. 50", 0.91),
                 ("MFD 05/2024", 0.9),
                 ("FSSAI Lic No. 10012043001234", 0.88),
                 ("Customer Care 1800-103-1947", 0.9)],
        "image_0": [("Mfd. by ITC Limited, Kolkata 700001", 0.85)],
        "image_1": [("Best Before 9 months from manufacture", 0.8)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(slot=0), "front"),
         (_png_bytes(slot=1), "back"),
         (_png_bytes(slot=2), "image_0"),
         (_png_bytes(slot=3), "image_1")],
        provider=provider)
    t = out["timings"]
    # One full-page OCR per image maximum, never rerun for fields.
    full = [e for e in t["provider_calls_detail"]
            if e.get("stage") == "full-page"]
    assert len(full) == 4
    # Bounded targeted calls per image.
    for label in ("front", "back", "image_0", "image_1"):
        n = sum(1 for e in t["provider_calls_detail"]
                if e.get("image_id") == label)
        assert n <= service.MAX_PROVIDER_CALLS_PER_IMAGE, (label, n)
    assert t["call_budget"]["budget_respected"] is True
    assert t["provider_calls"] == len(t["provider_calls_detail"])
    assert t["provider_calls"] <= 4 * service.MAX_PROVIDER_CALLS_PER_IMAGE
    # No duplicate full-page OCR.
    stage1 = [c for c in provider.calls if c.endswith(":stage1")]
    assert len(stage1) == 4


def test_structured_timing_diagnostics_shape():
    from app.services.ocr import service

    provider = _CountingProvider({
        "front": [("Sunfeast", 0.9), ("NET WT 100 g", 0.9),
                  ("MRP", 0.5)],
    })
    out = service.extract_label_multi([(_png_bytes(), "front")],
                                      provider=provider)
    t = out["timings"]
    for key in ("total_ms", "provider_calls", "rapidocr_calls",
                "tesseract_calls", "per_image_ms", "stage_timings"):
        assert key in t, key
    for entry in t["provider_calls_detail"]:
        assert set(("image_id", "stage", "purpose", "provider",
                    "crop_rect", "crop_size", "preprocessing_variant",
                    "duration_ms", "reason")) <= set(entry)
    assert "front" in t["per_image_ms"]


def test_real_package_end_to_end_no_contamination():
    from app.services.ocr import service

    stage_back = [
        ("INGREDIENTS:", 0.9),
        ("REFINEDWHEATFLOUR(MAIDA)(663)", 0.7),
        ("TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.", 0.75),
        ("SUGAR REFINEDPALMOILMILK", 0.7),
        ("COHTAINSWHEATMILKSOYANDSULPHITE", 0.7),
        ("Forfeeback-Contacd:ExecufiveConsumerCareCell", 0.7),
        ("MRP Rs. 50 (Incl. of all taxes)", 0.9),
        ("FSSAI Lic No. 10012043001234", 0.88),
    ]
    provider = _CountingProvider({
        "front": [("Sunfeast", 0.95), ("NET WT 100 g", 0.93)],
        "back": stage_back,
        "image_0": [("MFD 05/2024", 0.9)],
        "image_1": [("Customer Care 1800-103-1947", 0.9)],
    })
    out = service.extract_label_multi(
        [(_png_bytes(slot=0), "front"),
         (_png_bytes(slot=1), "back"),
         (_png_bytes(slot=2), "image_0"),
         (_png_bytes(slot=3), "image_1")],
        provider=provider)
    ing = out["food"]["fields"]["ingredients"]
    cleaned = (ing["cleaned_text"] or "").upper()
    assert "AIRTIGHT" not in cleaned
    assert "ONCE OPENED" not in cleaned
    assert "FORFEEBACK" not in cleaned.replace(" ", "")
    assert "CONSUMERCARE" not in cleaned.replace(" ", "")
    assert out["rejected_lines"], "contamination evidence must be recorded"
    # Key fields survive across the 4-image pool.
    det = out["fields_detailed"]
    assert det["quantity"]["value"] == "100"
    assert det["mrp"]["value"] == "50"
    assert det["fssai_license"]["value"] == "10012043001234"
    # Diagnostics artifact present without changing core response shape.
    assert "diagnostics" in out
    assert set(out["diagnostics"]["images"]) >= {"front", "back"}
    assert out["diagnostics"]["images"]["back"]["ingredient_heading_box"] \
        is not None or True  # boxless mocks: key present, value optional


def test_ingredient_variants_capped_no_duplicate_crops():
    from app.services.ocr import service

    assert service.INGREDIENT_VARIANT_BUDGET <= 2
    assert service.REGION_CALL_BUDGET <= 3
    assert service.MAX_TESSERACT_CALLS_PER_IMAGE == 1


# --------------------------------- sidebar static validation ---
def test_officer_sidebar_fixed_layout_static():
    root = pathlib.Path(__file__).resolve().parents[2]
    app_tsx = (root / "artifacts" / "nutricheck" / "src" / "App.tsx"
               ).read_text(encoding="utf-8")
    # Viewport-height shell with no document scroll; sidebar sticky +
    # viewport height; main content scrolls independently.
    assert 'data-testid="app-shell"' in app_tsx
    assert 'data-testid="officer-sidebar"' in app_tsx
    assert 'data-testid="main-content"' in app_tsx
    assert "h-[100dvh] overflow-hidden" in app_tsx
    assert "md:sticky" in app_tsx
    assert "overflow-y-auto" in app_tsx
    # Profile/Settings/Sign out remain in the stable bottom block.
    assert 'data-testid="link-profile"' in app_tsx
    assert 'data-testid="link-settings"' in app_tsx
    assert 'data-testid="button-logout"' in app_tsx
    # No second sidebar introduced.
    assert app_tsx.count("<aside") == 1
