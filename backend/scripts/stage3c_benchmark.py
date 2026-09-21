"""Stage 3C field benchmark: legacy generic OCR vs field-aware pipeline.

Runs the SAME synthetic fixtures twice through the REAL RapidOCR
pipeline — once with ``targeted_stages=False`` (BEFORE: Stage-1
full-page OCR plus deterministic field assembly only) and once with
defaults (AFTER: field-specific targeted crops, rotation fallback,
Tesseract path available, duplicate reuse, normalization) — then
reports, per field::

    field | before | after | status

plus total time, full/targeted/Tesseract/Gemini call counts. Decode,
duplicate handling and normalization are identical in both modes, so
the delta isolates field-aware targeting. Synthetic PIL fixtures only
(the text rendered is the expectation — authored, never model
output); no real package photographs exist in this repository, so
nothing here measures real-world accuracy.
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

for _v in ("CLERK_JWKS_URL", "CLERK_AUDIENCE", "DEV_AUTH_ROLE"):
    os.environ.pop(_v, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic_settings import SettingsConfigDict  # noqa: E402

from app.core import config as config_mod  # noqa: E402


class _TestSettings(config_mod.Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


config_mod.Settings = _TestSettings
config_mod.get_settings.cache_clear()

FIELDS = ["product_name", "quantity", "mrp", "manufacturing_date",
          "best_before", "batch_lot", "manufacturer", "fssai_license",
          "consumer_care", "ingredients", "veg_nonveg"]


def _panel(lines: list[str], w: int = 900, h: int = 700) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=36)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 56
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


PRODUCTS = [
    {"product_id": "synth-noodles-70g",
     "panels": {
         "front": ["TASTY NOODLES", "Masala Flavour", "NET WT 70 g"],
         "back": ["INGREDIENTS: Wheat Flour, Salt, Sugar",
                  "Nutrition Information per 100g",
                  "Energy 450 kcal", "Protein 8 g",
                  "MRP Rs. 14", "MFD 05/2024",
                  "FSSAI Lic No 10012043001234",
                  "Customer care 18001031947",
                  "Batch No B12",
                  "Manufactured by Acme Foods Pvt Ltd"]},
     "expected": {"product_name": "TASTY NOODLES", "quantity": "70",
                  "mrp": "14", "manufacturing_date": "05/2024",
                  "fssai_license": "10012043001234",
                  "consumer_care": "18001031947", "batch_lot": "B12"}},
    {"product_id": "synth-biscuits-200g",
     "panels": {
         "front": ["CRUNCHY BISCUITS", "NET WT 200 g"],
         "back": ["INGREDIENTS: Wheat Flour, Sugar, Palm Oil",
                  "MRP Rs. 45", "MFD 01/2025",
                  "FSSAI Lic No 10015043001129",
                  "Batch No C34",
                  "Manufactured by Biscuit Works"]},
     "expected": {"product_name": "CRUNCHY BISCUITS", "quantity": "200",
                  "mrp": "45", "manufacturing_date": "01/2025",
                  "fssai_license": "10015043001129",
                  "batch_lot": "C34"}},
]


def _run(images: list[tuple[bytes, str]], targeted: bool) -> dict:
    from app.services.ocr import service as svc

    t0 = time.perf_counter()
    out = svc.extract_label_multi(images, targeted_stages=targeted)
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    timings = out.get("timings", {}) or {}
    calls = timings.get("provider_calls_detail", []) or []
    detailed = out.get("fields_detailed", {}) or {}
    food = ((out.get("food") or {}).get("fields", {}) or {})
    values = {f: (detailed.get(f) or {}).get("value") for f in FIELDS}
    values["ingredients"] = ((food.get("ingredients") or {}).get(
        "cleaned_text"))
    veg = (out.get("veg_nonveg_symbol") or {}).get("classification")
    values["veg_nonveg"] = veg if veg != "UNKNOWN" else None
    return {"values": values, "wall_ms": wall_ms,
            "full": sum(1 for e in calls
                        if e.get("stage") == "full-page"),
            "targeted": sum(1 for e in calls
                            if e.get("stage") == "targeted"),
            "tesseract": sum(1 for e in calls
                             if e.get("provider") == "tesseract"),
            "gemini": 0}


def main() -> int:
    print(f"{'field':<18}{'before':<22}{'after':<22}status")
    improved = regressed = same = 0
    totals = {"before_ms": 0.0, "after_ms": 0.0, "full": [0, 0],
              "targeted": [0, 0], "tesseract": [0, 0]}
    for product in PRODUCTS:
        images = [(_panel(lines), label)
                  for label, lines in product["panels"].items()]
        before = _run(images, targeted=False)
        after = _run(images, targeted=True)
        totals["before_ms"] += before["wall_ms"]
        totals["after_ms"] += after["wall_ms"]
        for key in ("full", "targeted", "tesseract"):
            totals[key][0] += before[key]
            totals[key][1] += after[key]
        print(f"--- {product['product_id']} ---")
        for field in FIELDS:
            exp = (product.get("expected") or {}).get(field)
            if exp is None:
                continue
            b, a = before["values"].get(field), after["values"].get(field)
            b_ok = str(b or "").strip() == str(exp).strip()
            a_ok = str(a or "").strip() == str(exp).strip()
            if a_ok and not b_ok:
                status, improved = "IMPROVED", improved + 1
            elif b_ok and not a_ok:
                status, regressed = "REGRESSED", regressed + 1
            else:
                status, same = "SAME", same + 1
            print(f"{field:<18}{str(b):<22}{str(a):<22}{status}")
    print(f"total time: before={totals['before_ms']:.0f}ms "
          f"after={totals['after_ms']:.0f}ms")
    print(f"full OCR calls: before={totals['full'][0]} "
          f"after={totals['full'][1]}")
    print(f"targeted OCR calls: before={totals['targeted'][0]} "
          f"after={totals['targeted'][1]}")
    print(f"Tesseract calls: before={totals['tesseract'][0]} "
          f"after={totals['tesseract'][1]}")
    print("Gemini calls: before=0 after=0 (vision disabled in benchmark)")
    print(f"fields: improved={improved} regressed={regressed} same={same}")
    print("Synthetic fixtures only — no real-world accuracy claimed.")
    return 0 if regressed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
