"""Hybrid ingredient OCR: RapidOCR primary, Tesseract targeted fallback.

Covers the 15 required behaviours with a mocked Tesseract provider (the
suite never depends on the developer machine having the Tesseract binary;
provider-level availability is tested with a bogus path instead):

 1. good RapidOCR result -> no Tesseract call
 2. low coherence -> fallback
 3. incomplete result -> fallback
 4. suspicious OCR -> fallback
 5. malformed INS/E-number -> fallback (+ variant disagreement)
 6. agreement -> deterministic result
 7. disagreement -> deterministic reconciliation (valid additive wins)
 8. unresolved disagreement -> NEEDS_REVIEW, never a violation
 9. Tesseract unavailable -> RapidOCR-only
10. Tesseract exception -> RapidOCR-only
11. ordering preserved
12. percentages preserved
13. benchmark case: INS 500(ii) preserved over INS 500(i)
14. no Tesseract calls for MRP/nutrition extraction
15. no Tesseract calls for the full-package path
16-18. existing suites (full pytest run at the end)
"""
from __future__ import annotations

import io

import pytest

from app.services.ocr.base import OcrLine, OcrOutput

# Benchmark raw outputs (backend/tools/benchmark_tesseract_results.json):
# RapidOCR misread the roman suffix, Tesseract recovered it exactly.
RAPID_BENCH = ("Salt, Sugar, Mixed Spices (0.5%), Acidity Regulators\n"
               "(INS 500(i)) and Humectant (E451)")
TESS_BENCH = ("Salt, Sugar, Mixed Spices (0.5%), Acidity Regulators\n"
              "(INS 500(ii)) and Humectant (E451).\n")


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _bline(text, conf=0.9, image="back", box=None):
    return OcrLine(text=text, confidence=conf, box=box, image=image)


class _MockRapid:
    """Deterministic RapidOCR stand-in keyed by image label."""

    name = "mock-rapid"

    def __init__(self, by_label):
        self._by_label = by_label
        self.calls: list[str] = []

    def extract(self, image_np, image_side):
        self.calls.append(str(image_side))
        label = str(image_side).split(":")[0]
        return OcrOutput(lines=[
            OcrLine(text=t, confidence=c, box=list(b) if b else None,
                    image=label)
            for t, c, b in self._by_label.get(label, [])],
            engine=self.name)


class _MockTess:
    """Deterministic Tesseract stand-in: canned lines or failure."""

    name = "tesseract"

    def __init__(self, lines=None, fail=None, available=True):
        self._lines = lines or []
        self._fail = fail  # None | "oererror" | "boom"
        self._available = available
        self.calls: list[str] = []

    def available(self):
        return self._available

    def extract(self, image_np, image_side, psm=6):
        self.calls.append(f"{image_side}|psm{psm}")
        if self._fail == "oererror":
            from app.services.ocr.base import OcrError
            raise OcrError("mock tesseract missing")
        if self._fail == "boom":
            raise RuntimeError("mock tesseract crashed")
        return OcrOutput(
            lines=[OcrLine(text=t, confidence=c, box=None, image="t")
                   for t, c in self._lines], engine=self.name)


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (900, 300), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _food_of(out):
    return out["food"]["fields"]["ingredients"]


# ------------------------------------------------ gate unit tests ---
def test_gate_low_coherence_fires():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, reasons = needs_tesseract_fallback(
        rapid_text="NdsWhf Edibiegbieil Sal",
        rapid_coherence={"score": 0.42})
    assert fire
    assert any("coherence" in r for r in reasons)


def test_gate_coherent_result_does_not_fire():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, _ = needs_tesseract_fallback(
        rapid_text=("Refined wheat flour (Maida), palm oil, salt, sugar, "
                    "mixed spices"),
        rapid_coherence={"score": 0.91})
    assert not fire


def test_gate_incomplete_result_fires():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, reasons = needs_tesseract_fallback(
        rapid_text="Salt", rapid_coherence={"score": 0.9},
        region_height_px=200.0, line_height_px=20.0)
    assert fire
    assert any("too short" in r for r in reasons)


def test_gate_suspicious_characters_fire():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, reasons = needs_tesseract_fallback(
        rapid_text="Salt @nd sugar #4", rapid_coherence={"score": 0.9})
    assert fire
    assert any("suspicious" in r for r in reasons)


def test_gate_malformed_ins_fires():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    for bad in ("Acidity Regulators (INS 5O1), salt",
                "Humectant (E45), salt"):
        fire, reasons = needs_tesseract_fallback(
            rapid_text=bad, rapid_coherence={"score": 0.9})
        assert fire, bad
        assert any("malformed" in r for r in reasons)


def test_gate_variant_disagreement_fires():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, reasons = needs_tesseract_fallback(
        rapid_text="Salt (INS 202)",
        rapid_coherence={"score": 0.9},
        variant_texts=[("v1", "Salt (INS 202)", 0.8),
                       ("v2", "Salt (INS 203)", 0.75)])
    assert fire
    assert any("disagree" in r for r in reasons)


def test_gate_agreeing_variants_do_not_fire():
    from app.services.ocr.ingredient_hybrid import needs_tesseract_fallback

    fire, _ = needs_tesseract_fallback(
        rapid_text="Refined wheat flour, palm oil, salt",
        rapid_coherence={"score": 0.9},
        variant_texts=[("v1", "Refined wheat flour, palm oil, salt", 0.9),
                       ("v2", "Refined wheat flour, palm oil, salt", 0.88)])
    assert not fire


# ---------------------------------------- reconcile unit tests -----
def test_agreement_is_deterministic():
    from app.services.ocr.ingredient_hybrid import reconcile_ingredient_texts

    first = reconcile_ingredient_texts("Salt, Sugar", "Salt,  Sugar")
    second = reconcile_ingredient_texts("Salt, Sugar", "Salt,  Sugar")
    assert first == second
    assert first["ocr_source"] == "hybrid"
    assert first["uncertain"] is False
    assert first["text"] == "Salt, Sugar"


def test_empty_sides_fall_back_to_evidence():
    from app.services.ocr.ingredient_hybrid import reconcile_ingredient_texts

    only_tess = reconcile_ingredient_texts("", "Salt, Sugar")
    assert only_tess["ocr_source"] == "tesseract"
    assert only_tess["text"] == "Salt, Sugar"
    only_rapid = reconcile_ingredient_texts("Salt, Sugar", "")
    assert only_rapid["ocr_source"] == "rapidocr"
    both_empty = reconcile_ingredient_texts("", "")
    assert both_empty["text"] == ""


def test_ordering_preserved_on_swap():
    from app.services.ocr.ingredient_hybrid import reconcile_ingredient_texts

    out = reconcile_ingredient_texts("Salt, Sugar", "Sugar, Salt")
    # Same items, swapped order: rapid (primary) order kept, flagged.
    assert out["text"] == "Salt, Sugar"
    assert out["uncertain"] is True


def test_percentages_preserved():
    from app.services.ocr.ingredient_hybrid import reconcile_ingredient_texts

    out = reconcile_ingredient_texts(
        "Sugar (68%), Salt", "Sugar (72%), Salt",
        rapid_conf=0.5, tess_conf=0.5)
    assert "(68%)" in out["text"]
    assert "(72%)" not in out["text"]
    assert out["uncertain"] is True


def test_benchmark_ins_500ii_preserved():
    from app.services.ocr.ingredient_hybrid import reconcile_ingredient_texts

    out = reconcile_ingredient_texts(RAPID_BENCH, TESS_BENCH,
                                     rapid_conf=0.5, tess_conf=0.85)
    assert "(INS 500(ii))" in out["text"]
    assert "500(i))" not in out["text"].replace("500(ii))", "")
    assert "(0.5%)" in out["text"] and "E451" in out["text"]
    assert out["ocr_source"] in ("tesseract", "hybrid")


# --------------------------------- service integration tests -------
def _back_png():
    return _png_bytes()


def test_good_rapid_result_no_tesseract_call():
    from app.services.ocr import service

    rapid = _MockRapid({
        "back": [("INGREDIENTS:", 0.9, _box(10, 10, 200, 40)),
                 ("Refined wheat flour (Maida), palm oil, salt", 0.9,
                  _box(10, 50, 800, 110))],
    })
    tess = _MockTess(lines=[("Salt", 0.9)])
    out = service.extract_label_multi([(_back_png(), "back")],
                                      provider=rapid, tess_provider=tess)
    assert tess.calls == []
    ing = _food_of(out)
    assert ing["ocr_source"] == "rapidocr"
    assert ing["fallback"] is None
    assert ing["detection"] == "DETECTED"
    assert "palm oil" in (ing["cleaned_text"] or "")


def test_weak_rapid_triggers_single_tesseract_call():
    from app.services.ocr import service

    rapid = _MockRapid({
        "back": [("INGREDIENTS:", 0.9, _box(10, 10, 200, 40)),
                 ("NdsWhf Edibiegbieil Sal (INS 5O1)", 0.4,
                  _box(10, 50, 800, 110))],
    })
    tess = _MockTess(lines=[
        ("Refined wheat flour, palm oil, salt (INS 501)", 0.85)])
    out = service.extract_label_multi([(_back_png(), "back")],
                                      provider=rapid, tess_provider=tess)
    assert len(tess.calls) == 1  # at most one ingredient call
    assert "psm6" in tess.calls[0]
    ing = _food_of(out)
    assert ing["ocr_source"] in ("tesseract", "hybrid")
    assert "palm oil" in (ing["cleaned_text"] or ing["raw_text"] or "")
    t = out["timings"]
    assert t["tesseract_fallback_ms"] > 0
    assert any(r.get("engine") == "tesseract"
               for v in t["images"].values()
               for r in v.get("regions", []))


def test_unresolved_disagreement_needs_review_not_violation():
    from app.services.ocr import service

    rapid = _MockRapid({
        "back": [("INGREDIENTS:", 0.9, _box(10, 10, 200, 40)),
                 ("Sugar (68%), Salt", 0.5, _box(10, 50, 800, 110))],
    })
    tess = _MockTess(lines=[("Sugar (72%), Salt", 0.5)])
    out = service.extract_label_multi([(_back_png(), "back")],
                                      provider=rapid, tess_provider=tess)
    ing = _food_of(out)
    assert "(68%)" in (ing["cleaned_text"] or ing["raw_text"] or "")
    assert "(72%)" not in (ing["cleaned_text"] or ing["raw_text"] or "")
    assert ing["detection"] == "NEEDS_REVIEW"
    # Single-side win keeps that side's provenance; the disagreement is
    # recorded as uncertain in the fallback record, never a violation.
    assert ing["ocr_source"] == "rapidocr"
    assert ing["fallback"] and ing["fallback"]["triggered"] is True
    assert any("uncertain" in n or "disagree" in n or "confidence" in n
               for n in ing["fallback"]["notes"])


def test_tesseract_unavailable_keeps_rapid_only():
    from app.services.ocr import service

    rapid = _MockRapid({
        "back": [("INGREDIENTS:", 0.9, _box(10, 10, 200, 40)),
                 ("NdsWhf Edibiegbieil Sal", 0.4, _box(10, 50, 800, 110))],
    })
    tess = _MockTess(lines=[("Salt", 0.9)], available=False)
    out = service.extract_label_multi([(_back_png(), "back")],
                                      provider=rapid, tess_provider=tess)
    assert tess.calls == []
    ing = _food_of(out)
    assert ing["ocr_source"] == "rapidocr"
    assert out["timings"]["tesseract_fallback_ms"] == 0


@pytest.mark.parametrize("fail", ["oererror", "boom"])
def test_tesseract_exception_keeps_rapid_only(fail):
    from app.services.ocr import service

    rapid = _MockRapid({
        "back": [("INGREDIENTS:", 0.9, _box(10, 10, 200, 40)),
                 ("NdsWhf Edibiegbieil Sal", 0.4, _box(10, 50, 800, 110))],
    })
    tess = _MockTess(lines=[("Salt", 0.9)], fail=fail)
    out = service.extract_label_multi([(_back_png(), "back")],
                                      provider=rapid, tess_provider=tess)
    ing = _food_of(out)
    assert ing["ocr_source"] == "rapidocr"
    assert "NdsWhf" in (ing["raw_text"] or "")


def test_no_tesseract_for_mrp_nutrition_regions():
    from app.services.ocr import service

    rapid = _MockRapid({
        "front": [("MRP Rs. 50 (Incl. of all taxes)", 0.4,
                   _box(10, 10, 400, 50))],
    })
    tess = _MockTess(lines=[("MRP Rs. 50", 0.9)])
    out = service.extract_label_multi([(_back_png(), "front")],
                                      provider=rapid, tess_provider=tess)
    assert tess.calls == []
    kinds = [r.get("kind") for v in out["timings"]["images"].values()
             for r in v.get("regions", [])]
    assert not any("tesseract" in str(k) for k in kinds)
    assert out["fields_detailed"]["mrp"]["value"] == "50"


def test_no_tesseract_for_complete_full_package_path():
    from app.services.ocr import service

    rapid = _MockRapid({
        "front": [("MAGGI 2-Minute Noodles", 0.95),
                  ("NET WT 70 g", 0.93)],
        "back": [("MRP Rs. 50", 0.91), ("MFD 05/2024", 0.9)],
    })
    tess = _MockTess(lines=[("Salt", 0.9)])
    out = service.extract_label_multi(
        [(_back_png(), "front"), (_back_png(), "back")],
        provider=rapid, tess_provider=tess)
    assert tess.calls == []
    assert out["timings"]["tesseract_fallback_ms"] == 0
    assert out["timings"]["provider_calls"] == 2


# --------------------------------------- provider unit tests -------
def test_tesseract_provider_missing_binary_is_graceful():
    from app.services.ocr.base import OcrError
    from app.services.ocr.tesseract_provider import TesseractProvider

    prov = TesseractProvider(cmd="/nonexistent/tesseract-binary")
    assert prov.available() is False
    with pytest.raises(OcrError):
        prov.extract(object(), "test")


def test_resolve_cmd_prefers_explicit_and_env(monkeypatch):
    from app.services.ocr.tesseract_provider import resolve_tesseract_cmd

    assert resolve_tesseract_cmd("/nonexistent/x") is None
    monkeypatch.setenv("LEGALAKSHI_TESSERACT_CMD", "/nonexistent/y")
    assert resolve_tesseract_cmd(None) is None
    monkeypatch.delenv("PATH", raising=False)
    assert resolve_tesseract_cmd(None) is None
