"""Standalone benchmark: Tesseract 5 vs RapidOCR on Maggi-style label crops.

BENCHMARK ONLY — production application code must never import this module.
It imports production helpers (region proposals, coherence scoring) purely
as measurement harnesses; no production flow is altered.

What it does:
  1. Builds deterministic synthetic Maggi front/back panels (the repo has
     no real Maggi photos; attached_assets are IDE screenshots).
  2. Runs one RapidOCR Stage-1 pass per panel, derives 5 crops with the
     existing region logic (full back, ingredients, MRP, nutrition, care).
  3. Runs the RapidOCR baseline on the exact same crops.
  4. Runs Tesseract 5 LSTM (--oem 1, eng) on the same crops:
     ingredients -> PSM {6, 11, 12} x 3 preps (max 9 calls);
     MRP/nutrition/care -> PSM {6, 11} x {orig, thresh};
     full back -> PSM 6 x orig.
  5. Scores ingredient fidelity against the known expected text with
     EXACT matching only (no fuzzy hiding), prints raw outputs, writes
     backend/tools/benchmark_tesseract_results.json, and prints a
     factual A/B/C recommendation.

Usage (from backend/):
    .\\.venv\\Scripts\\python.exe tools/benchmark_tesseract.py
"""
from __future__ import annotations

import difflib
import io
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
RESULTS_PATH = HERE / "benchmark_tesseract_results.json"

EXPECTED_INGREDIENTS = (
    "Refined Wheat Flour (Maida) (70%), Palm Oil, Salt, Sugar, "
    "Mixed Spices (0.5%), Acidity Regulators (INS 500(ii)) "
    "and Humectant (E451)."
)
# What the ingredient crop actually contains (heading row excluded by
# design; the heading remainder is preserved separately as seed text).
# Scored alongside the full text so crop-boundary penalties (equal for
# both engines) never masquerade as engine errors.
INCROP_EXPECTED = (
    "Salt, Sugar, Mixed Spices (0.5%), Acidity Regulators "
    "(INS 500(ii)) and Humectant (E451)."
)

# Exact (case-insensitive) probes into the known ingredient text.
ING_PROBES = ["ingredients", "refined wheat flour", "(maida)", "(70%)",
              "palm oil", "salt", "sugar", "mixed spices", "(0.5%)",
              "acidity regulators", "ins 500", "(ii)", "humectant", "e451"]
INS_PROBES = ["ins 500", "(ii)", "e451", "acidity", "humectant"]


# ------------------------------------------------------- fixtures ---
def _fonts():
    from PIL import ImageFont

    for path, sizes in (
            ("C:/Windows/Fonts/arial.ttf", (64, 40, 30)),
            ("C:/Windows/Fonts/arialbd.ttf", (72, 44, 32))):
        try:
            kind = "truetype:" + path
            return kind, tuple(ImageFont.truetype(path, s) for s in sizes)
        except OSError:
            continue
    from PIL import ImageFont as _IF

    return "bitmap-fallback", (_IF.load_default(),) * 3


def make_maggi_panels() -> tuple[bytes, bytes, str]:
    """Deterministic synthetic Maggi 70 g front/back panels (PNG bytes)."""
    from PIL import Image, ImageDraw

    font_kind, (big, med, small) = _fonts()
    front = Image.new("RGB", (1200, 900), "white")
    d = ImageDraw.Draw(front)
    d.text((80, 120), "MAGGI", fill="red", font=big)
    d.text((80, 260), "2-Minute Noodles", fill="black", font=med)
    d.text((80, 380), "Masala", fill="black", font=med)
    d.text((80, 620), "NET WT 70 g", fill="black", font=med)
    back = Image.new("RGB", (1600, 1200), "white")
    d = ImageDraw.Draw(back)
    y = 50
    for chunk in ["INGREDIENTS: Refined Wheat Flour (Maida) (70%), Palm Oil,",
                  "Salt, Sugar, Mixed Spices (0.5%), Acidity Regulators",
                  "(INS 500(ii)) and Humectant (E451).",
                  "Nutrition Information per 100g: Energy 450 kcal,",
                  "Protein 8 g, Total Fat 15 g, Sodium 900 mg.",
                  "MRP Rs. 50 (Incl. of all taxes)",
                  "MFD 05/2024   Best Before 9 months from manufacture",
                  "FSSAI Lic No. 10012043001234",
                  "Mfd. by Nestle India Limited, Moga, Punjab 142001",
                  "Customer Care 1800-103-1947"]:
        d.text((60, y), chunk, fill="black", font=small)
        y += 95
    out = []
    for img in (front, back):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out[0], out[1], font_kind


# ------------------------------------------------------- OCR glue ---
def _rapid_provider():
    from app.services.ocr.rapidocr_provider import RapidOCRProvider

    t0 = time.perf_counter()
    provider = RapidOCRProvider()
    # Warm the singleton once so model-load time is not billed per crop.
    provider._engine_or_raise()
    return provider, round((time.perf_counter() - t0) * 1000, 1)


def _tess_available() -> tuple[bool, str]:
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        ver = pytesseract.get_tesseract_version()
        return True, str(ver)
    except Exception as exc:  # noqa: BLE001 - benchmark diagnostics
        return False, f"unavailable: {exc}"


def tess_ocr(pil_crop, psm: int, prep: str) -> dict:
    """One Tesseract call. Records raw text + runtime + config only.

    No confidence values are recorded: Tesseract word confidences do not
    share RapidOCR's line-confidence semantics, so reporting them side by
    side would mislead.
    """
    import time as _t

    import pytesseract
    from PIL import Image as _Image

    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    t0 = _t.perf_counter()
    try:
        text = pytesseract.image_to_string(
            pil_crop, lang="eng",
            config=f"--oem 1 --psm {psm}", timeout=120)
        err = None
    except Exception as exc:  # noqa: BLE001
        text, err = "", f"{type(exc).__name__}: {exc}"
    ms = round((_t.perf_counter() - t0) * 1000, 1)
    return {"engine": "tesseract", "psm": psm, "prep": prep,
            "oem": 1, "lang": "eng", "ms": ms,
            "raw_text": text, "error": err}


def rapid_ocr(provider, image_np, side: str) -> dict:
    from app.services.ocr.food import score_ingredient_coherence

    t0 = time.perf_counter()
    out = provider.extract(image_np, side)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    lines = [{"text": ln.text, "confidence": ln.confidence,
              "box": ln.box} for ln in out.lines]
    text = "\n".join(ln.text for ln in out.lines)
    confs = [ln.confidence for ln in out.lines]
    mean_conf = (round(sum(confs) / len(confs), 3) if confs else None)
    return {"engine": "rapidocr", "side": side, "ms": ms,
            "raw_text": text, "lines": lines,
            "mean_confidence": mean_conf,
            "coherence": score_ingredient_coherence(text, confs)}


# ------------------------------------------------------- preps ---
def prep_variants(pil_crop) -> dict[str, object]:
    """Original / grayscale-contrast / Otsu-threshold (PIL+numpy only)."""
    import numpy as np
    from PIL import Image, ImageEnhance, ImageOps

    out = {"orig": pil_crop.convert("RGB")}
    gray = pil_crop.convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    out["gray-contrast"] = gray.convert("RGB")
    arr = np.asarray(pil_crop.convert("L"), dtype=np.uint8)
    hist = np.bincount(arr.ravel(), minlength=256).astype(float)
    total = arr.size
    sum_all = float((hist * np.arange(256)).sum())
    sum_b = w_b = 0.0
    best, thresh = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        between = (sum_all * w_b - sum_b) ** 2 / (w_b * w_f)
        if between > best:
            best, thresh = between, t
    out["otsu-thresh"] = Image.fromarray(
        ((arr > thresh) * 255).astype(np.uint8)).convert("RGB")
    return out


# ------------------------------------------------------- scoring ---
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def score_ingredient_text(raw: str,
                          expected: str = EXPECTED_INGREDIENTS,
                          probes: list[str] = ING_PROBES) -> dict:
    """Exact-match comparison vs a known text (no fuzzy hiding).

    ``merged_words`` is token-level: a single output token that fuses two
    known words ("mixedspices"), not an artefact of stripping all spaces.
    """
    # Plain normalisation only: padding parens with spaces would break
    # exact probes such as "(ii)" / "(maida)" for both engines alike.
    hyp_norm = _norm(raw)
    exp_norm = _norm(expected)
    char_sim = round(difflib.SequenceMatcher(None, exp_norm,
                                             _norm(raw)).ratio(), 3)
    exp_toks = _tokens(expected)
    hyp_toks = _tokens(raw)
    exp_set, hyp_set = set(exp_toks), set(hyp_toks)
    recall = round(len(exp_set & hyp_set) / len(exp_set), 3) if exp_set else 0.0
    precision = (round(len(exp_set & hyp_set) / len(hyp_set), 3)
                 if hyp_set else 0.0)
    pairs = [("mixed", "spices"), ("acidity", "regulators"),
             ("refined", "wheat"), ("wheat", "flour"), ("palm", "oil")]
    merged = sorted({a + b for a, b in pairs if a + b in hyp_set})
    probes_hit = {p: (p in hyp_norm) for p in probes}
    ins = {p: (p in hyp_norm) for p in INS_PROBES}
    order_idx = [hyp_norm.find(p) for p in probes if p in hyp_norm]
    ordered = all(a < b for a, b in zip(order_idx, order_idx[1:]))
    missing = [p for p in probes if p not in hyp_norm]
    return {"char_similarity": char_sim,
            "token_recall": recall, "token_precision": precision,
            "probes": probes_hit, "probes_hit": sum(probes_hit.values()),
            "probes_total": len(probes),
            "ins_recall": round(sum(ins.values()) / len(ins), 3),
            "ins_hits": ins,
            "ordering_preserved": ordered,
            "missing_words": missing,
            "merged_words": merged}


# ------------------------------------------------------- main ---
def main() -> dict:
    import numpy as np

    from app.services.ocr import service as svc
    from app.services.ocr import food as food_mod

    report: dict = {"tesseract_required": "Tesseract 5 LSTM --oem 1 + eng",
                    "crops": {}, "timings": {}}
    front_raw, back_raw, font_kind = make_maggi_panels()
    report["fixtures"] = {
        "note": "Repo holds no real Maggi photos; deterministic synthetic "
                "Maggi-70g front/back panels rendered in-script.",
        "font": font_kind,
        "front_bytes": len(front_raw), "back_bytes": len(back_raw)}
    print(f"[fixtures] synthetic Maggi panels via {font_kind}", flush=True)

    tess_ok, tess_ver = _tess_available()
    report["tesseract_version"] = tess_ver
    report["tesseract_available"] = tess_ok
    print(f"[tesseract] {tess_ver}", flush=True)
    if not tess_ok:
        print("[tesseract] unavailable - RapidOCR baseline only", flush=True)

    provider, load_ms = _rapid_provider()
    report["timings"]["rapidocr_model_load_ms"] = load_ms
    print(f"[rapidocr] model warm: {load_ms}ms", flush=True)

    # Stage-1 both panels (production helper path).
    staged = {}
    for label, raw in (("front", front_raw), ("back", back_raw)):
        original = svc._decode(raw)
        stage_img = svc._fit(original, svc.STAGE1_MAX_DIM)
        t0 = time.perf_counter()
        stage_lines = provider.extract(
            np.array(svc._enhance(stage_img)),
            f"{label}:stage1").lines
        ms = round((time.perf_counter() - t0) * 1000, 1)
        staged[label] = {"original": original, "stage": stage_img,
                         "lines": stage_lines, "ms": ms,
                         "size": original.size,
                         "stage_size": stage_img.size}
        print(f"[stage1] {label}: {ms}ms, {len(stage_lines)} lines",
              flush=True)
    report["timings"]["stage1_ms"] = sum(
        v["ms"] for v in staged.values())

    # Crop plan (stage coords, then mapped to full-res): reuse the
    # production region proposals so Tesseract sees identical pixels.
    back = staged["back"]
    statuses = svc.extract_with_status(back["lines"])
    regions = svc._propose_regions("back", back["lines"], statuses,
                                   back["stage_size"])
    by_kind = {r["kind"]: r for r in regions}

    def fullres_crop(rect):
        orig = back["original"]
        ow, oh = orig.size
        sw, sh = back["stage_size"]
        sx, sy = ow / max(sw, 1), oh / max(sh, 1)
        x0, y0, x1, y1 = rect
        return orig.crop((max(0, int(x0 * sx)), max(0, int(y0 * sy)),
                          min(ow, int(x1 * sx)), min(oh, int(y1 * sy))))

    sw, sh = back["stage_size"]
    plan: dict[str, dict] = {
        "full_back": {"crop": back["original"], "psms": [6],
                      "preps": ["orig"]},
        "ingredients": {
            "crop": (fullres_crop(by_kind["ingredients"]["rect"])
                     if "ingredients" in by_kind else back["original"]),
            "psms": [6, 11, 12], "preps": ["orig", "gray-contrast",
                                           "otsu-thresh"]},
        "mrp": {"crop": (fullres_crop(by_kind["mrp"]["rect"])
                         if "mrp" in by_kind
                         else back["original"].crop(
                             (0, int(back["original"].size[1] * 0.55),
                              back["original"].size[0],
                              back["original"].size[1]))),
                "psms": [6, 11], "preps": ["orig", "otsu-thresh"]},
        "nutrition": {"crop": None, "psms": [6, 11],
                      "preps": ["orig", "otsu-thresh"]},
        "care": {"crop": (fullres_crop(by_kind["care"]["rect"])
                          if "care" in by_kind
                          else back["original"].crop(
                              (0, int(back["original"].size[1] * 0.55),
                               back["original"].size[0],
                               back["original"].size[1]))),
                 "psms": [6, 11], "preps": ["orig", "otsu-thresh"]},
    }
    # Nutrition band: below the NUTRITION heading until next section.
    nutri_rect = None
    for ln in back["lines"]:
        if food_mod._NUTRI_HEAD.search(ln.text or "") and ln.box:
            r = svc._box_rect(ln.box)
            if r is not None:
                nutri_rect = (0, r[3], sw, min(sh, r[3] + 0.3 * sh))
                break
    plan["nutrition"]["crop"] = (fullres_crop(nutri_rect)
                                 if nutri_rect else back["original"])
    plan["nutrition"]["nutrition_band_found"] = nutri_rect is not None
    plan["ingredients"]["heading_region_found"] = "ingredients" in by_kind

    rapid_total = 0.0
    for name, spec in plan.items():
        crop = spec["crop"]
        print(f"[crop] {name}: {crop.size}, "
              f"psms={spec['psms']} preps={spec['preps']}", flush=True)
        entry: dict = {"crop_size": list(crop.size), "psms": spec["psms"],
                       "rapidocr": None, "tesseract": []}
        # RapidOCR baseline on the identical crop.
        base = rapid_ocr(provider, np.array(crop), f"bench:{name}")
        rapid_total += base["ms"]
        entry["rapidocr"] = base
        # Tesseract matrix (bounded: <=3 PSM x <=3 preps).
        if tess_ok:
            for prep_name in spec["preps"]:
                prepped = prep_variants(crop)[prep_name]
                for psm in spec["psms"]:
                    res = tess_ocr(prepped, psm, prep_name)
                    entry["tesseract"].append(res)
                    print(f"  [tess] {name}/{prep_name}/psm{psm}: "
                          f"{res['ms']}ms "
                          f"{len(res['raw_text'])} chars", flush=True)
        report["crops"][name] = entry
    report["timings"]["rapidocr_crops_ms"] = round(rapid_total, 1)

    # Ingredient scoring: best Tesseract config vs RapidOCR, exact only.
    # Full-text scores (heading/first-line penalty applies equally) plus
    # in-crop scores (only what the crop pixels contain) for the decision.
    INCROP_PROBES = ["salt", "sugar", "mixed spices", "(0.5%)",
                     "acidity regulators", "ins 500", "(ii)",
                     "humectant", "e451"]
    ing = report["crops"]["ingredients"]
    rapid_ing = {"score": score_ingredient_text(ing["rapidocr"]["raw_text"]),
                 "incrop": score_ingredient_text(
                     ing["rapidocr"]["raw_text"], INCROP_EXPECTED,
                     INCROP_PROBES),
                 "coherence": ing["rapidocr"]["coherence"],
                 "ms": ing["rapidocr"]["ms"]}
    tess_scored = [{"cfg": f"psm{r['psm']}/{r['prep']}", "ms": r["ms"],
                    "score": score_ingredient_text(r["raw_text"]),
                    "incrop": score_ingredient_text(
                        r["raw_text"], INCROP_EXPECTED, INCROP_PROBES),
                    "raw_text": r["raw_text"]}
                   for r in ing["tesseract"] if not r["error"]]
    for t in tess_scored:
        t["coherence"] = food_mod.score_ingredient_coherence(
            t["raw_text"], [])["score"]
    best_tess = (max(tess_scored,
                     key=lambda t: (t["incrop"]["token_recall"],
                                    t["incrop"]["char_similarity"]))
                 if tess_scored else None)
    # Confidence-free coherence for both engines (Tesseract reports no
    # confidences by design): the only apples-to-apples text comparison.
    rapid_fair_coh = food_mod.score_ingredient_coherence(
        ing["rapidocr"]["raw_text"], [])["score"]
    for t in tess_scored:
        t["fair_coherence"] = food_mod.score_ingredient_coherence(
            t["raw_text"], [])["score"]
    if best_tess is not None:
        best_tess["fair_coherence"] = food_mod.score_ingredient_coherence(
            best_tess["raw_text"], [])["score"]
    rapid_ing["fair_coherence"] = rapid_fair_coh

    # Per-crop exact-token recall (same key tokens, both engines).
    CROP_KEYS = {
        "ingredients": INCROP_PROBES,
        "mrp": ["mrp", "rs", "50", "incl", "taxes", "mfd", "05/2024"],
        "nutrition": ["nutrition", "energy", "450", "protein", "fat",
                      "sodium"],
        "care": ["fssai", "10012043001234", "customer", "care",
                 "1800-103-1947", "nestle", "moga"],
        "full_back": ["ingredients", "refined wheat flour", "mrp", "rs",
                      "50", "mfd", "fssai", "nutrition", "energy"],
    }
    crop_wins = {}
    for name, keys in CROP_KEYS.items():
        entry = report["crops"][name]
        r_hits = sum(1 for k in keys if k in _norm(entry["rapidocr"]
                                                   ["raw_text"]))
        t_best = 0
        t_best_cfg = None
        for res in entry["tesseract"]:
            if res["error"]:
                continue
            hits = sum(1 for k in keys if k in _norm(res["raw_text"]))
            if hits > t_best:
                t_best, t_best_cfg = hits, f"psm{res['psm']}/{res['prep']}"
        crop_wins[name] = {
            "keys": keys,
            "rapidocr_hits": r_hits, "rapidocr_recall": round(r_hits / len(keys), 3),
            "tesseract_hits": t_best, "tesseract_recall": round(t_best / len(keys), 3),
            "tesseract_cfg": t_best_cfg}
    report["crop_token_recall"] = crop_wins
    report["ingredient_comparison"] = {
        "expected": EXPECTED_INGREDIENTS,
        "rapidocr": rapid_ing,
        "tesseract_best": ({"cfg": best_tess["cfg"], "ms": best_tess["ms"],
                            "score": best_tess["score"],
                            "incrop": best_tess["incrop"],
                            "coherence": best_tess["coherence"],
                            "fair_coherence": best_tess["fair_coherence"],
                            "raw_text": best_tess["raw_text"]}
                           if best_tess else None),
        "tesseract_all": [
            {"cfg": t["cfg"], "ms": t["ms"],
             "token_recall": t["score"]["token_recall"],
             "char_similarity": t["score"]["char_similarity"],
             "ins_recall": t["score"]["ins_recall"],
             "coherence": t["coherence"]} for t in tess_scored],
    }

    # Timing rollup: RapidOCR-only vs +Tesseract ingredient fallback
    # (fallback runs only when RapidOCR coherence < 0.70).
    rapid_only = report["timings"]["stage1_ms"] + rapid_total
    fallback_extra = (best_tess["ms"] if best_tess and
                      ing["rapidocr"]["coherence"]["score"] < 0.70 else 0.0)
    report["timings"]["rapidocr_only_ms"] = round(rapid_only, 1)
    report["timings"]["rapidocr_plus_tess_fallback_ms"] = round(
        rapid_only + fallback_extra, 1)
    report["timings"]["fallback_would_run"] = fallback_extra > 0

    # Factual recommendation (thresholds stated, no hype).
    # B (targeted fallback): Tesseract strictly beats RapidOCR on the
    #   in-crop ingredient text on confidence-free coherence AND fixes at
    #   least one probe RapidOCR misses AND the fallback total stays
    #   under 60 s.
    # C (investigate wider switch): Tesseract recall exceeds RapidOCR by
    #   >=0.10 on at least 3 of the 5 crops.
    # A otherwise.
    rec, why = "A", ""
    if not tess_ok or not best_tess:
        rec, why = "A", "No Tesseract runs available."
    else:
        r_inc = rapid_ing["incrop"]["token_recall"]
        t_inc = best_tess["incrop"]["token_recall"]
        total = report["timings"]["rapidocr_plus_tess_fallback_ms"]
        wins = sum(1 for _n, w in crop_wins.items()
                   if w["tesseract_recall"] >= w["rapidocr_recall"] + 0.10)
        fixed = [p for p in INCROP_PROBES
                 if p in best_tess["raw_text"].lower()
                 and p not in ing["rapidocr"]["raw_text"].lower()]
        if wins >= 3:
            rec, why = ("C", f"Tesseract recall exceeds RapidOCR by >=0.10 "
                             f"on {wins}/5 crops; wider investigation is "
                             f"justified.")
        elif (t_inc > r_inc and best_tess["fair_coherence"] >= 0.60
                and fixed and total < 60000):
            rec, why = ("B", f"Tesseract strictly improves the ingredient "
                             f"crop (in-crop recall {r_inc}->{t_inc}, fair "
                             f"coherence "
                             f"{rapid_fair_coh}->{best_tess['fair_coherence']},"
                             f" fixes {fixed}) within budget; targeted "
                             f"fallback justified.")
        else:
            rec, why = ("A", "RapidOCR matches or beats Tesseract on the "
                             "measured crops; no material gain demonstrated.")
    report["recommendation"] = {"option": rec, "reason": why}

    RESULTS_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"[done] results -> {RESULTS_PATH}", flush=True)
    return report


if __name__ == "__main__":
    rep = main()
    ing = rep["ingredient_comparison"]
    print("\n===== INGREDIENT COMPARISON (exact matching) =====")
    print("RapidOCR:", ing["rapidocr"]["score"])
    if ing["tesseract_best"]:
        print("Tesseract best:", ing["tesseract_best"]["cfg"],
              ing["tesseract_best"]["score"])
    print("Timings (ms):", rep["timings"])
    print("Recommendation:", rep["recommendation"])
