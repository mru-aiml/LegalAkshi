"""Stage 2B real-package diagnostic (§17).

Runs the REAL POST /ocr/extract path (RapidOCR stand-in + configured
vision provider when enabled, OCR-only otherwise) and reports
field-by-field raw results:

  OCR result | Vision result | Final reconciled result | Status | Reason

No real package images are shipped in this repository (see
backend/tests/golden/README.md), so this script exercises the pipeline
shape with a synthetic fixture + scripted providers. It creates NO
ground truth and claims NO accuracy percentages.
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

from fastapi.testclient import TestClient  # noqa: E402

from app.factory import create_app  # noqa: E402
from app.repositories.memory import MemoryRepo  # noqa: E402
from app.services.ocr.base import OcrLine, OcrOutput  # noqa: E402
from app.services.vision.mock_provider import MockVisionProvider  # noqa: E402
from app.services.vision import provider as provider_mod  # noqa: E402

FIELDS = ["product", "quantity", "mrp", "manufacturing_date",
          "best_before", "batch_lot", "manufacturer", "fssai_license",
          "consumer_care", "ingredients", "allergens", "nutrition",
          "veg_nonveg"]


def _png() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (240, 240), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    from app.services.ocr import rapidocr_provider as rapid_mod

    class _OCR:
        name = "diagnostic-ocr"

        def extract(self, image_np, image_side):
            # Fixture simulates a real weak read: MRP low-confidence
            # (conflict probe) and quantity unread (vision-only probe).
            lines = [("NESTLE INDIA LIMITED", 0.9),
                     ("MRP Rs. 108", 0.5),
                     ("MFD 05/2024", 0.85),
                     ("Best before 12 months", 0.8),
                     ("Batch No B12", 0.8),
                     ("FSSAI Lic No 10012043001234", 0.9),
                     ("Customer care 18001234567", 0.87),
                     ("INGREDIENTS: Sugar, Salt, Wheat Flour", 0.85)]
            return OcrOutput(
                lines=[OcrLine(text=t, confidence=c, box=None,
                               image=image_side) for t, c in lines],
                engine=self.name)

    rapid_mod.RapidOCRProvider = lambda: _OCR()
    provider_mod.get_vision_provider = lambda explicit=None: \
        MockVisionProvider(script={
            "quantity": {"field": "quantity", "value": "270",
                         "unit": "g", "status": "DETECTED",
                         "confidence": 0.96,
                         "evidence_text": "NET QUANTITY 270 g"},
            "mrp": {"field": "mrp", "value": "180", "unit": "INR",
                    "status": "DETECTED", "confidence": 0.9,
                    "evidence_text": "MRP Rs. 180"}})

    client = TestClient(create_app(MemoryRepo()))
    t0 = time.perf_counter()
    resp = client.post(
        "/api/v1/ocr/extract?food=true",
        files={"front_image": ("f.png", _png(), "image/png"),
               "back_image": ("b.png", _png(), "image/png")},
        headers={"X-LegalAkshi-Role": "officer",
                 "X-LegalAkshi-User": "diag"})
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    out = resp.json()
    vision = out.get("vision", {})
    rec = (out.get("reconciliation") or {}).get("fields", {})
    print(f"HTTP {resp.status_code} | wall {wall_ms}ms | "
          f"ocr_calls={vision.get('ocr_calls')} "
          f"vision_calls={vision.get('vision_calls')} "
          f"vision_enabled={vision.get('vision_enabled')} "
          f"detail={vision.get('vision_status_detail')}")
    for entry in (vision.get("calls_log") or []):
        print(f"  call group={entry.get('group')} "
              f"image={entry.get('image_id')} "
              f"region={entry.get('region')} crop={entry.get('crop')} "
              f"bytes={entry.get('image_bytes_size')} "
              f"mime={entry.get('mime_type')} "
              f"provider={entry.get('provider')} "
              f"latency={entry.get('latency_ms')}ms "
              f"status={entry.get('status')}")
    print(f"{'field':<18}{'OCR':<14}{'Vision':<14}{'Final':<14}"
          f"{'Status':<14}Reason")
    for field in FIELDS:
        ocr_v = ((out.get("fields_detailed") or {}).get(field) or {}
                 ).get("value")
        hit = rec.get(field) or {}
        vision_v = None
        for cand in hit.get("candidates", []) or []:
            if cand.get("source") == "vision":
                vision_v = cand.get("value")
        print(f"{field:<18}{str(ocr_v):<14}{str(vision_v):<14}"
              f"{str(hit.get('final_value')):<14}"
              f"{str(hit.get('status') or 'OCR-ONLY'):<14}"
              f"{hit.get('needs_review_reason', '')}")
    print("\nNo ground truth was created; no accuracy is claimed. "
          "Real-package images are not present in this repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
