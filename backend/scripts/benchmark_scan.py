"""Real-engine Scan & Inspect benchmark (Part X).

Runs the actual staged pipeline (real RapidOCR, no Tesseract binary
here so the hybrid path degrades gracefully) over four synthetic but
realistic package panels — front, back, ingredient-heavy, nutrition —
then repeats with the OpenCV layer degraded to the legacy path, and
prints preprocessing/OCR/call/field numbers for both.

No real package photos exist in this environment, so panels are
rendered with PIL at realistic sizes, including tilt/blur stress
variants. Nothing here claims production accuracy — it measures the
pipeline comparatively on identical inputs.
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _panel(lines, w=1280, h=900, tilt=0.0, blur=0.0):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = _font(30)
    y = 60
    for ln in lines:
        d.text((60, y), ln, fill="black", font=font)
        y += 48
    if tilt:
        img = img.rotate(tilt, expand=True, fillcolor="white")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


FRONT = ["DEMO Crunchy Biscuits", "Choco Flavour  Net WT 100 g"]
BACK = ["INGREDIENTS: Refined Wheat Flour (Maida) 62%, Sugar, Palm Oil,",
        "Salt, Cocoa Solids 4%, Raising Agents INS 500(ii), Emulsifier E322.",
        "TO A CLEAN AIRTIGHT CONTAINER ONCE OPENED.",
        "MRP Rs. 50 (Incl. of all taxes)   MFD 05/2024",
        "FSSAI Lic No. 10012043001234",
        "Mfd. by Demo Foods Ltd., Moga, Punjab 142001",
        "Customer Care 1800-103-1947  customercare@demo.in"]
INGREDIENT = ["INGREDIENTS: Refined Wheat Flour (Maida) 62%, Sugar,",
              "Palm Oil, Salt, Cocoa Solids 4%,",
              "Raising Agents (INS 500(ii)), Emulsifier (E322).",
              "STORE IN A COOL DRY PLACE. KEEP AWAY FROM SUNLIGHT."]
NUTRITION = ["Nutrition Information per 100g",
             "Energy 450 kcal   Protein 8 g",
             "Carbohydrate 60 g   Total Sugars 25 g",
             "Total Fat 15 g   Saturated Fat 7 g   Sodium 300 mg"]


def _run(panels, label):
    from app.services.ocr import service as svc
    from app.services.ocr.rapidocr_provider import RapidOCRProvider

    RapidOCRProvider._engine = None  # fresh engine per leg, fair timing
    provider = RapidOCRProvider()
    t0 = time.perf_counter()
    out = svc.extract_label_multi(
        [(png, name) for name, png in panels], provider=provider)
    total = (time.perf_counter() - t0) * 1000
    t = out["timings"]
    fields = out.get("fields_detailed", {})
    food = (out.get("food") or {}).get("fields", {})
    ing = food.get("ingredients", {})
    rows = []
    for name in ("product_name", "quantity", "mrp", "manufacturing_date",
                 "best_before", "fssai_license", "manufacturer",
                 "consumer_care"):
        hit = fields.get(name, {})
        rows.append((name, hit.get("value"), hit.get("status")))
    cleaned = (ing.get("cleaned_text") or "")
    contaminated = any(s in cleaned.upper() for s in (
        "AIRTIGHT", "ONCE OPENED", "STORE IN", "CUSTOMER CARE",
        "1800-103", "@DEMO"))
    return {
        "label": label,
        "total_ms": round(total, 1),
        "provider_calls": t.get("provider_calls"),
        "tesseract_calls": t.get("tesseract_calls"),
        "per_image_ms": t.get("per_image_ms"),
        "fields": rows,
        "ingredients_status": ing.get("detection"),
        "ingredients_contaminated": contaminated,
        "cleaned_len": len(cleaned),
    }


def main() -> int:
    panels = [("front", _panel(FRONT, tilt=2.0)),
              ("back", _panel(BACK)),
              ("ingredients", _panel(INGREDIENT, tilt=3.0)),
              ("nutrition", _panel(NUTRITION, blur=0.6))]
    print("== leg 1: OpenCV layer enabled ==")
    with_cv2 = _run(panels, "opencv-on")
    print("== leg 2: OpenCV layer degraded (legacy path) ==")
    from app.services.ocr import opencv_preprocessor as ocv

    real_cv2, real_avail = ocv._cv2, ocv.cv2_available
    ocv._cv2 = lambda: None  # type: ignore[assignment]
    ocv.cv2_available = lambda: False  # type: ignore[assignment]
    try:
        without_cv2 = _run(panels, "opencv-off")
    finally:
        ocv._cv2, ocv.cv2_available = real_cv2, real_avail
    for leg in (with_cv2, without_cv2):
        print(f"\n--- {leg['label']} ---")
        print(f"total_ms={leg['total_ms']} provider_calls={leg['provider_calls']} "
              f"tesseract_calls={leg['tesseract_calls']}")
        print(f"per_image_ms={leg['per_image_ms']}")
        for name, value, status in leg["fields"]:
            print(f"  {name:18s} {str(value)[:34]:34s} {status}")
        print(f"  ingredients: {leg['ingredients_status']} "
              f"contaminated={leg['ingredients_contaminated']} "
              f"cleaned_len={leg['cleaned_len']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
