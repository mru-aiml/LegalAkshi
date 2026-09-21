"""Stage 3 six-image benchmark (§16/acceptance-gates).

Measures a 6-image inspection through the REAL pipeline
(preprocessing -> one full RapidOCR pass/image -> region detection ->
targeted OCR -> Tesseract fallback -> reconciliation) and reports
exact per-stage timings plus call-budget compliance:

  full-page calls == images (never duplicated)
  targeted OCR <= 3 / image
  Tesseract <= 1 / image
  Gemini disabled here (OCR-only benchmark; vision measured separately)

Synthetic label images only (no real package photos in repo) — this
measures pipeline cost/shape, never real-world accuracy.
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

PANELS = [
    ("front", ["TASTY NOODLES", "Masala Flavour", "70 g"]),
    ("back", ["INGREDIENTS: Wheat Flour, Salt, Sugar",
              "Nutrition Information per 100g",
              "Energy 450 kcal", "Protein 8 g",
              "NET WT 70 g", "MRP Rs. 14"]),
    ("image_2", ["MFD 05/2024", "Batch No B12",
                 "FSSAI Lic No 10012043001234"]),
    ("image_3", ["Customer care 18001234567",
                 "Manufactured by Acme Foods Pvt Ltd",
                 "Best before 12 months"]),
    ("image_4", ["Storage: store in a cool dry place",
                 "Marketed by Acme Retail"]),
    ("image_5", ["MRP Rs. 14 (incl. of all taxes)",
                 "NET WT 70 g"]),
]


def _text_png(lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    for line in lines:
        draw.text((20, y), line, fill="black")
        y += 30
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    from app.services.ocr import service as svc

    images = [(_text_png(lines), label) for label, lines in PANELS]
    t0 = time.perf_counter()
    out = svc.extract_label_multi(images)
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    timings = out.get("timings", {})
    call_log = timings.get("provider_calls_detail", [])
    full = sum(1 for e in call_log if e.get("stage") == "full-page")
    n_images = len(images)
    per_image_targeted: dict[str, int] = {}
    per_image_tess: dict[str, int] = {}
    for e in call_log:
        label = e.get("image_id")
        if e.get("provider") == "tesseract":
            per_image_tess[label] = per_image_tess.get(label, 0) + 1
        elif e.get("stage") not in ("full-page", "full-page-rotated"):
            per_image_targeted[label] = per_image_targeted.get(
                label, 0) + 1
    print(f"images={n_images} wall={wall_ms}ms "
          f"full_page_calls={full} provider_calls={len(call_log)}")
    print("stage_ms:", {k: timings.get("stage_timings", {}).get(k)
                        for k in ("preprocessing_ms", "full_page_ms",
                                  "region_detection_ms", "targeted_ms",
                                  "tesseract_ms", "reconciliation_ms")})
    print("roles:", timings.get("image_roles"))
    print("field_counts:", timings.get("field_counts"))
    print("targeted_per_image:", per_image_targeted)
    print("tesseract_per_image:", per_image_tess)
    ok = (full == n_images
          and all(v <= 3 for v in per_image_targeted.values())
          and all(v <= 1 for v in per_image_tess.values()))
    print("BUDGET", "PASS" if ok else "FAIL")
    for field in ("product_name", "quantity", "mrp",
                  "manufacturing_date", "best_before", "batch_lot",
                  "manufacturer", "fssai_license", "consumer_care"):
        hit = (out.get("fields_detailed") or {}).get(field, {})
        print(f"  {field}: {hit.get('value')!r} "
              f"{hit.get('status')} img={hit.get('image')}")
    food = (out.get("food") or {}).get("fields", {})
    ing = food.get("ingredients", {})
    print(f"  ingredients: {str(ing.get('cleaned_text'))[:80]!r} "
          f"{ing.get('detection')}")
    print("No accuracy percentage is claimed: synthetic fixture only.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
