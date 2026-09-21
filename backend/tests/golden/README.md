# LegalAkshi Golden Dataset (Stage 2, Part R)

Real-package evaluation set for Package Intelligence. **No fabricated
labels**: every example below is a *template* with `expected_value: null`
until a human verifies it against the physical package.

## Adding a real package example

1. Place the photo under `backend/tests/golden/images/` (never commit
   personal data; package fronts/backs only), e.g.
   `golden-masala-200g-front.jpg`.
2. Copy one entry from `examples.template.json`, set:
   - `image`: relative file name in `images/`
   - `field`: Package Intelligence field key (e.g. `mrp`, `quantity`)
   - `expected_value` / `expected_unit`: read off the physical package
   - `evidence_bbox`: `[x0, y0, x1, y1]` in pixels if known, else null
   - `verification_source`: who read it, e.g. `officer <id>, <date>`
   - `package_type`: e.g. `masala-200g-pouch`
3. Set `verified: true` only after a second human confirms the read.
4. Run the golden evaluation (once images exist):

```bash
cd backend
python -m pytest tests/test_stage2_package_intelligence.py -q -k golden
```

## Per-product fixture format (Stage 3A/3B)

A product directory groups every view of one physical package with one
expected-value file (no real images are committed — see limitation):

```
golden/products/<product_id>/
  front.jpg
  back.jpg
  side_1.jpg        # optional extra views (any image extension)
  side_2.jpg
  expected.json
```

`expected.json` (Stage 3C flat format; all values read off the
physical package by a human — `null` means "not declared on this
package"; see `products/_template/expected.json`):

```json
{
  "product_name": "Example Noodles",
  "quantity": "70",
  "unit": "g",
  "mrp": "14",
  "manufacturing_date": "05/2024",
  "best_before": null,
  "fssai_license": null,
  "batch": null,
  "consumer_care": null,
  "ingredients": null,
  "veg_nonveg": "VEG",
  "verified": false,
  "verification_source": null
}
```

Evaluation (`tests/golden/evaluation.py`, no images required for the
loader/metrics unit tests):

- `load_products()` returns verified products only (images + expected
  values); unverified/missing/invalid entries are skipped, never
  measured, never fabricated.
- `evaluate_product(expected, extracted)` scores the twelve Stage 3C
  fields: exact match, normalized match (case/space/punct folded),
  numeric match (mrp/quantity as values), date match (normalized
  tokens), extraction coverage, NEEDS_REVIEW rate.
- Aggregates report counts only — never one "AI accuracy" number, and
  with zero verified products every metric reports n=0.

Only entries with `"verified": true` (confirmed by a second human) may
be used for accuracy measurement. Unverified entries document intent
only. Never fabricate ground truth from model output.

## Current limitation

No real package image files are shipped inside this repository
(`images/` contains only `.gitkeep`), so golden tests assert the
*infrastructure* (template schema validity, null-label discipline,
evaluation harness skips cleanly) rather than measured accuracy.

**Do NOT claim the AI improves real-world accuracy until a labeled
real-package benchmark is actually run against this set.**
