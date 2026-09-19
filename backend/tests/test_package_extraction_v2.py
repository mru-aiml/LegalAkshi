"""Package Intelligence / Extraction v2: column-aware regions, orientation,
boundaries, contamination guards, field validators, and the real-package
regression case.

Nothing here weakens existing contracts: mock providers return canned
lines, geometry tests use explicit boxes (box=None lines keep legacy
behaviour), and the single live-RapidOCR fixture test asserts
contamination absence strictly with tolerant presence checks.
"""
from __future__ import annotations

import io

import pytest

from app.services.ocr.base import OcrLine, OcrOutput


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _line(text, conf=0.9, image="back", box=None):
    return OcrLine(text=text, confidence=conf, box=box, image=image)


class _MockProvider:
    """Per-label canned lines (boxes preserved when given)."""

    name = "mock-ocr"

    def __init__(self, by_label):
        self._by_label = by_label
        self.calls: list[str] = []

    def extract(self, image_np, image_side):
        self.calls.append(str(image_side))
        label = str(image_side).split(":")[0]
        out = []
        for row in self._by_label.get(label, []):
            text, conf = row[0], row[1]
            box = row[2] if len(row) > 2 else None
            out.append(OcrLine(text=text, confidence=conf, box=box,
                               image=label))
        return OcrOutput(lines=out, engine=self.name)


def _statuses_all_missing():
    return {k: {"status": "NOT_DETECTED"}
            for k in ("mrp", "manufacturing_date", "fssai_license",
                      "consumer_care")}


# ------------------------------------------- 1. heading variants ---
@pytest.mark.parametrize("heading", [
    "INGREDIENTS",
    "INGREDIENTS:",
    "INGREDIENTS-",
    "INGREDIENTS.",
    "Ingredients :",
    "  INGREDIENTS  ",
    "INGREDIENTS / CONTENTS",
])
def test_ingredient_heading_variants(heading):
    from app.services.ocr.food import _is_ingredient_heading

    assert _is_ingredient_heading(heading) is True


def test_ingredient_heading_rejects_non_headings():
    from app.services.ocr.food import _is_ingredient_heading

    assert _is_ingredient_heading("Net contents 70 g") is False
    assert _is_ingredient_heading("MRP Rs. 50") is False
    assert _is_ingredient_heading("") is False


# --------------------------------- 2. heading bbox + column --------
def _two_column_lines():
    # Left column x 60-700: heading + body. Right column x 900-1600:
    # storage + care decoys at overlapping y (the contamination trap).
    left = [
        ("INGREDIENTS:", 0.95, _box(80, 100, 400, 140)),
        ("REFINED WHEAT FLOUR (MAIDA) (66%),", 0.9, _box(80, 160, 640, 200)),
        ("SUGAR, IODISED SALT,", 0.88, _box(80, 210, 600, 250)),
        ("BEST BEFORE 9 MONTHS", 0.9, _box(80, 270, 500, 310)),
    ]
    right = [
        ("STORAGE: Keep in a cool place.", 0.9, _box(920, 160, 1560, 200)),
        ("FOR FEEDBACK CALL 1800-123-456", 0.9, _box(920, 210, 1560, 250)),
    ]
    return ([_line(t, c, box=b) for t, c, b in left + right], 700, 900)


def test_heading_bbox_selects_body_column():
    from app.services.ocr import service

    lines, body_right_edge, decoy_left_edge = _two_column_lines()
    regions = service._propose_regions(
        "back", lines, _statuses_all_missing(), (1800, 1400))
    ing = [r for r in regions if r["kind"] == "ingredients"]
    assert len(ing) == 1
    rect = ing[0]["rect"]
    assert rect[2] <= body_right_edge + 16, rect  # pad-bounded, no spill
    assert rect[2] < decoy_left_edge  # adjacent column excluded
    assert ing[0]["column"]["source"] == "heading-column"
    assert ing[0]["orientation"] == "0"


# --------------------------------------- 3. same-column pull --------
def test_same_column_extraction_keeps_body():
    from app.services.ocr.food import extract_food_label

    lines, _, _ = _two_column_lines()
    out = extract_food_label(
        lines, image_columns={"back": (60.0, 700.0)})
    raw = out["fields"]["ingredients"]["raw_text"] or ""
    assert "REFINED WHEAT FLOUR" in raw
    assert "IODISED SALT" in raw


# --------------------------------- 4. adjacent-column rejection -----
def test_adjacent_column_lines_rejected_with_reasons():
    from app.services.ocr.food import filter_offcolumn_lines

    lines, _, _ = _two_column_lines()
    kept, rejected = filter_offcolumn_lines(
        lines, {"x0": 60.0, "x1": 700.0})
    kept_text = " ".join(getattr(ln, "text", "") for ln in kept)
    assert "REFINED WHEAT FLOUR" in kept_text
    rej_text = " ".join(r["text"] for r in rejected)
    assert "STORAGE" in rej_text
    assert "FEEDBACK" in rej_text
    assert all(r["reasons"] for r in rejected)


# ------------------------------ 5. blue-panel marketing ------------
def test_blue_panel_marketing_excluded():
    from app.services.ocr import service
    from app.services.ocr.food import filter_offcolumn_lines

    marketing = _line("TASTE THE MAGIC", 0.92, box=_box(950, 60, 1550, 110))
    lines, _, _ = _two_column_lines()
    kept, rejected = filter_offcolumn_lines([marketing, *lines])
    rej_text = " ".join(r["text"] for r in rejected)
    out_text = " ".join(getattr(ln, "text", "") for ln in kept)
    # Wide marketing row must not survive in the kept ingredient lines.
    assert "MAGIC" not in out_text
    assert "MAGIC" in rej_text
    # Structural fact locked in: wide row is not a member of the
    # narrow ingredient column.
    from app.services.ocr.food import column_membership
    verdict = column_membership(marketing, {"x0": 60.0, "x1": 700.0})
    assert verdict["score"] < 0.3


# ------------------------------------ 6. storage rejection ---------
@pytest.mark.parametrize("line", [
    "STORAGE: Keep in a cool, dry place.",
    "Storage instructions: keep away from sunlight.",
])
def test_storage_text_stops_ingredient_block(line):
    from app.services.ocr.food import reconstruct_ingredient_text

    text, notes = reconstruct_ingredient_text([
        _line("Refined wheat flour, sugar,"),
        _line(line),
        _line("More ingredients here,"),
    ])
    assert "cool" not in text and "sunlight" not in text
    assert any("section boundary" in n for n in notes)


# -------------------------------- 7. consumer-care rejection -------
def test_consumer_care_rejected_from_ingredients():
    from app.services.ocr.food import reconstruct_ingredient_text

    text, notes = reconstruct_ingredient_text([
        _line("Refined wheat flour, sugar,"),
        _line("Customer Care 1800-103-1947"),
        _line("Call us toll free"),
    ])
    assert "1800" not in text
    assert any("section boundary" in n for n in notes)


# -------------------------------- 8. section boundaries ------------
@pytest.mark.parametrize("line,expected", [
    ("Contains: Milk, Soy", "CONTAINS"),
    ("Contains milk solids", "CONTAINS"),
    ("ALLERGEN ADVICE", "ALLERGEN"),
    ("Nutrition Information", "NUTRITION"),
    ("Manufactured by Foods Ltd", "MANUFACTURER"),
    ("Marketed by Foods Ltd", "MANUFACTURER"),
    ("Packed by Foods Ltd", "PACKER"),
    ("STORAGE: keep cool", "STORAGE"),
    ("Directions for use", "DIRECTIONS"),
    ("FOR FEEDBACK call us", "FEEDBACK"),
    ("For queries contact us", "FEEDBACK"),
    ("BEST BEFORE 9 MONTHS", "BEST_BEFORE"),
    ("Use by 01/2026", "BEST_BEFORE"),
    ("Expiry Date 01/2026", "BEST_BEFORE"),
    ("MRP Rs. 50", "MRP"),
    ("Salt, Sugar, Milk", None),
    ("Refined wheat flour (Maida)", None),
])
def test_section_boundary_normalized(line, expected):
    from app.services.ocr.food import match_section_boundary

    assert match_section_boundary(line) == expected, line


# -------------------------------------- 9. rotated package ---------
def test_rotated_package_region_maps_back():
    from app.services.ocr import service

    # Portrait-captured (transposed) frame: tall boxes.
    lines = [
        _line("INGREDIENTS:", 0.9, box=_box(60, 80, 100, 400)),
        _line("SUGAR, SALT,", 0.88, box=_box(120, 80, 160, 600)),
        _line("WHEAT FLOUR", 0.88, box=_box(180, 80, 220, 560)),
    ]
    assert service._detect_orientation(lines)["transpose"] is True
    regions = service._propose_regions(
        "back", lines, _statuses_all_missing(), (900, 1400))
    ing = [r for r in regions if r["kind"] == "ingredients"]
    assert len(ing) == 1
    x0, y0, x1, y1 = ing[0]["rect"]
    assert 0 <= x0 <= x1 <= 900 and 0 <= y0 <= y1 <= 1400
    assert ing[0]["orientation"] == "transposed-90/270"
    # Heading x-range covered in original coordinates.
    assert x0 <= 100 and x1 >= 60


# ---------------------------------------- 10. skewed boxes ---------
def test_skewed_boxes_still_cluster():
    from app.services.ocr.food import cluster_text_columns, \
        filter_offcolumn_lines

    def skew(x0, y0, x1, y1, dx=4):
        return [[x0, y0], [x1 + dx, y0 + 3], [x1 + dx, y1 + 3], [x0, y1]]

    lines = [
        _line("INGREDIENTS:", 0.9, box=skew(80, 100, 400, 140)),
        _line("SUGAR, SALT,", 0.88, box=skew(84, 160, 620, 200)),
        _line("WHEAT FLOUR", 0.88, box=skew(76, 210, 590, 250)),
    ]
    cols = cluster_text_columns(lines)
    assert len(cols) == 1
    kept, rejected = filter_offcolumn_lines(lines)
    assert len(kept) == 3 and rejected == []


# ----------------------------------------- 11-13. parse forms ------
def test_regression_percentages_preserved():
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients(
        "REFINED WHEAT FLOUR (MAIDA) (66%), SUGAR, MINERALS (0.5%)")
    pcts = [i["percentage"] for i in items]
    assert "(66%)" in pcts or "66%" in pcts
    assert "(0.5%)" in pcts or "0.5%" in pcts


def test_regression_ins_bracket_forms():
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients("RAISING AGENTS [503(ii) & 500(i)]")
    by_raw = " ".join(i["raw_segment"] for i in items)
    assert "503(ii)" in by_raw and "500(i)" in by_raw
    solo = parse_ingredients("EMULSIFIERS [322(i)]")
    assert solo and solo[0]["ins_number"] == "INS 322(i)"
    # Bare bracketed digits are kept verbatim, never promoted.
    bare = parse_ingredients("EMULSIFIERS [471]")
    assert bare and bare[0]["ins_number"] is None
    assert "[471]" in bare[0]["raw_segment"]


def test_regression_e_numbers_kept_verbatim():
    from app.services.ocr.food import parse_ingredients

    items = parse_ingredients("Humectant (E451), salt")
    assert "E451" in items[0]["raw_segment"]


# ------------------------------ 14. multi-image reconciliation -----
def test_multi_image_ingredient_evidence_pooled():
    from app.services.ocr import service

    provider = _MockProvider({
        "front": [("MAGGI Noodles", 0.95), ("NET WT 70 g", 0.93)],
        "back": [("INGREDIENTS: Refined wheat flour,", 0.9),
                 ("palm oil, salt", 0.88)],
    })
    out = service.extract_label_multi(
        [(_png(), "front"), (_png(), "back")], provider=provider)
    raw = out["food"]["fields"]["ingredients"]["raw_text"] or ""
    assert "palm oil" in raw
    assert out["fields_detailed"]["quantity"]["value"] == "70"


def _png():
    import io

    from PIL import Image

    img = Image.new("RGB", (900, 300), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------ 15-19. field validators ------
@pytest.mark.parametrize("value,expected", [
    ("50", True), ("Rs. 50", True), ("1999.00", True),
    ("1010005", False), ("", False), (None, False), ("0", False),
])
def test_validate_mrp(value, expected):
    from app.services.ocr.fields import validate_mrp

    assert validate_mrp(value) is expected


@pytest.mark.parametrize("value,expected", [
    ("05/2024", True), ("04/06/2024", True), ("May 2024", True),
    ("32/13/2024", False), ("not a date", False), ("", False),
])
def test_validate_date(value, expected):
    from app.services.ocr.fields import validate_date

    assert validate_date(value) is expected


@pytest.mark.parametrize("value,expected", [
    ("10012043001234", True), ("10012 04300 1234", True),
    ("1001204300123", False), ("abcdefghij1234", False), (None, False),
])
def test_validate_fssai(value, expected):
    from app.services.ocr.fields import validate_fssai

    assert validate_fssai(value) is expected


def test_manufacturer_address_continuation():
    from app.services.ocr.food import extract_food_label

    out = extract_food_label([
        _line("Mfd. by Nestle India Limited,"),
        _line("Moga, Punjab 142001"),
        _line("MRP Rs. 50"),
    ])
    fields = out["fields"]
    assert "Nestle" in (fields["manufacturer"]["value"] or "")
    assert "142001" in (fields["manufacturer_address"]["value"] or "")


def test_phone_email_website_extraction():
    from app.services.ocr.fields import extract_contact_codes

    out = extract_contact_codes([
        _line("Customer Care 1800-103-1947"),
        _line("Email care@example.com"),
        _line("Visit www.example.com for more"),
    ])
    assert out["email"]["value"] == "care@example.com"
    assert out["website"]["value"] == "www.example.com"
    assert out["email"]["status"] == "DETECTED"


# ------------------------------------------ 20. nutrition ----------
def test_nutrition_split_rows_paired():
    from app.services.ocr.food import extract_food_label

    out = extract_food_label([
        _line("Nutrition Information per 100g:"),
        _line("Energy 450 kcal"),
        _line("Protein"),
        _line("8 g"),
        _line("Total Fat 15 g"),
    ])
    nut = out["fields"]["nutrition"]
    assert "450" in (nut["energy"]["value"] or "")
    assert nut["energy"]["value"] and "kcal" in nut["energy"]["value"]
    assert "8" in (nut["protein"]["value"] or "")
    assert nut["sodium"]["value"] is None  # never fabricated


# --------------------------------------------- 21. veg -------------
def test_veg_detector_independent_of_fields():
    from app.services.ocr.veg_symbol import detect_veg_symbol

    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 400), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    blank = detect_veg_symbol([buf.getvalue()])
    assert blank["status"] in ("NOT_DETECTED", "NEEDS_REVIEW")
    assert blank["provenance"] == "IMAGE"
    veg = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(veg)
    d.rectangle([180, 180, 220, 220], outline=(0, 150, 0), width=4)
    d.ellipse([188, 188, 212, 212], fill=(0, 150, 0))
    buf2 = io.BytesIO()
    veg.save(buf2, format="PNG")
    found = detect_veg_symbol([buf2.getvalue()])
    assert found["classification"] == "VEGETARIAN"
    assert found["status"] == "DETECTED"


# ------------------------------------ 22. barcode interface --------
def test_barcode_candidates_and_checksum():
    from app.services.ocr.fields import decode_barcode, ean13_check_digit, \
        find_barcode_candidates

    first12 = "890103087016"
    check = ean13_check_digit(first12)
    assert check is not None and len(check) == 1
    valid = first12 + check
    assert ean13_check_digit(valid[:12]) == valid[12]
    assert ean13_check_digit("123") is None
    cands = find_barcode_candidates([_line(f"890103087016{check}")])
    assert any(c["digits"] == valid and c["ean13_checksum_valid"] is True
               for c in cands)
    # FSSAI-context 14-digit runs and care numbers are not barcodes.
    excl = find_barcode_candidates([
        _line("FSSAI Lic No. 10012043001234"),
        _line("Customer Care 1800-103-1947"),
    ])
    assert all(c["digits"] != "10012043001234" for c in excl)
    assert all(c["digits"] != "18001031947" for c in excl)
    res = decode_barcode(digits=valid)
    assert res["status"] == "NEEDS_MANUAL_SCAN"
    assert res["value"] is None


# ---------------------------------- 23/24. tess gating ------------
def test_tesseract_fallback_single_call_budget():
    from app.services.ocr import service

    provider = _MockProvider({
        "back": [("INGREDIENTS:", 0.9, _box(80, 100, 400, 140)),
                 ("Refined wheat flour, palm oil", 0.4,
                  _box(80, 160, 700, 220))],
    })

    class _Tess:
        name = "tesseract"

        def __init__(self):
            self.calls = []

        def available(self):
            return True

        def extract(self, image_np, image_side, psm=6):
            self.calls.append(image_side)
            from app.services.ocr.base import OcrOutput
            return OcrOutput(lines=[], engine=self.name)

    tess = _Tess()
    out = service.extract_label_multi([(_png(), "back")], provider=provider,
                                      tess_provider=tess)
    assert len(tess.calls) <= 1, tess.calls


def test_tesseract_unavailable_is_rapid_only():
    from app.services.ocr import service
    from app.services.ocr.tesseract_provider import TesseractProvider

    assert TesseractProvider(cmd="/nonexistent/tesseract").available() is False
    provider = _MockProvider({
        "back": [("MRP Rs. 50", 0.9)],
    })
    out = service.extract_label_multi(
        [(_png(), "back")], provider=provider,
        tess_provider=TesseractProvider(cmd="/nonexistent/tesseract"))
    assert out["fields"]["mrp"]["value"] == "50"
    assert out["timings"]["tesseract_fallback_ms"] == 0

# --------------------------------- real-package fixture ------
def _render_two_column_fixture():
    """Two-column back panel resembling the supplied real package.

    Left column: ingredient declaration (regression targets). Right
    column: storage + consumer-care decoys at overlapping heights.
    Marketing header spans the top. Requires Arial (Windows); skipped
    elsewhere so Linux CI stays green.
    """
    import os

    from PIL import Image, ImageDraw, ImageFont

    arial = "C:/Windows/Fonts/arial.ttf"
    if not os.path.isfile(arial):
        pytest.skip("Arial unavailable: fixture needs scalable fonts")
    img = Image.new("RGB", (1800, 1500), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1800, 130], fill=(20, 60, 160))
    head = ImageFont.truetype(arial, 54)
    body = ImageFont.truetype(arial, 34)
    small = ImageFont.truetype(arial, 30)
    d.text((80, 30), "TASTE THE MAGIC", font=head, fill="white")
    left = [
        "INGREDIENTS:",
        "REFINED WHEAT FLOUR (MAIDA) (66%),",
        "SUGAR, REFINED PALM OIL,",
        "MILK PRODUCTS, WHEY POWDER,",
        "SWEETENED CONDENSED PARTLY SKIMMED MILK,",
        "EDIBLE LACTOSE, INVERT SUGAR SYRUP,",
        "RAISING AGENTS [503(ii) & 500(i)],",
        "IODISED SALT, EMULSIFIERS [471 & 322(i)],",
        "ARTIFICIAL FLAVOURING SUBSTANCES,",
        "VITAMINS, DOUGH CONDITIONER (223), MINERALS.",
        "BEST BEFORE 9 MONTHS FROM MFG",
    ]
    y = 200
    for t in left:
        d.text((80, y), t, font=body, fill="black")
        y += 62
    right = [
        "STORAGE:",
        "Store in a cool,",
        "dry place away",
        "from sunlight.",
        "FOR FEEDBACK,",
        "CALL 1800-123-456",
        "care@example.com",
        "MRP Rs. 50",
    ]
    y = 200
    for t in right:
        d.text((980, y), t, font=small, fill="black")
        y += 58
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_real_package_fixture_no_contamination():
    """Live RapidOCR on the two-column fixture (the MUST NOT case).

    Contamination absence is asserted strictly: decoy strings can only
    appear via cross-column leakage. Target presence uses a documented
    tolerance because glyph-level OCR errors are honest uncertainty,
    not contamination.
    """
    pytest.importorskip("rapidocr_onnxruntime")
    import time

    from app.services.ocr import service

    raw = _render_two_column_fixture()
    t0 = time.perf_counter()
    out = service.extract_label_multi([(raw, "back")])
    elapsed = time.perf_counter() - t0
    assert elapsed < 120, elapsed
    assert out["timings"]["provider_calls"] <= 12
    ing = out["food"]["fields"]["ingredients"]
    cleaned = ((ing["cleaned_text"] or "") + " " +
               (ing["raw_text"] or "")).upper()
    for decoy in ("STORAGE", "MAGIC", "FEEDBACK", "1800-123",
                  "CARE@EXAMPLE"):
        assert decoy not in cleaned, decoy
    targets = ["REFINED WHEAT FLOUR", "MAIDA", "66%", "SUGAR", "PALM OIL",
               "WHEY POWDER", "LACTOSE", "503", "500", "SALT", "471",
               "322", "VITAMINS", "MINERALS"]
    flat = cleaned.replace(" ", "")
    hits = sum(1 for t in targets if t.replace(" ", "") in flat)
    assert hits >= 9, f"only {hits}/14 targets: {cleaned[:200]}"
    # Geometry evidence: column crop excludes the right column.
    regions = [r for v in out["timings"]["images"].values()
               for r in v.get("regions", [])
               if str(r.get("kind", "")).startswith("ingredients")]
    assert regions, "no ingredient region proposed"
    assert regions[0].get("column", {}).get("source") == "heading-column"
