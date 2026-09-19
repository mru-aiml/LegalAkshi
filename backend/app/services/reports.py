"""Unified backend report generation — HTML, PDF, and JSON from the SAME
persisted analysis data. No report logic lives in the frontend.

- JSON: existing FinalReport shape (API representation).
- HTML: printable browser preview of the same data.
- PDF: official downloadable report (fpdf2, pure Python — reliable on
  Windows with no system dependencies). fpdf2 core fonts are latin-1 only,
  so text is sanitized (Rs. for ₹, straight quotes); HTML keeps full unicode.
  Nothing is fabricated: every section renders persisted rows, and empty
  sections are labeled as such.

Layout (government-inspection style):
  1. Cover: product, inspection metadata, overall status, score, executive
     summary counts, critical findings.
  2. Package information: each declaration with Detected / Needs review /
     Missing badge + confidence/provenance.
  3. Food label analysis: ingredients, nutrition, veg symbol, FSSAI,
     warnings (from persisted declarations when present).
  4+. Legal compliance findings grouped PASS / FAIL / NEEDS REVIEW /
     NOT APPLICABLE with requirement, observed value, reason, applicable
     law, rule version, effective date, evidence/provenance.
  5. Online listing compliance: separate section; NOT CHECKED when no
     listing evidence was supplied (package photos cannot establish it).
  6. Inspector action: automated findings are potential findings only.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

# Checks that concern the ONLINE listing (Rule 6(10)/6(10A)) — rendered in
# the separate online-listing section, never mixed into package findings.
ONLINE_CHECK_IDS = {"CHK-ECOMMERCE-DECL", "CHK-ECOMMERCE-COO-FILTER"}

# Declaration field -> (display label, section).
PACKAGE_ROWS: list[tuple[str, str]] = [
    ("brand", "Brand"), ("product_name", "Brand"),
    ("common_generic_name", "Common / generic name"),
    ("quantity", "Net quantity"), ("net_quantity", "Net quantity"),
    ("quantity_unit", "Quantity unit"),
    ("mrp", "MRP"),
    ("unit_sale_price", "Unit sale price"),
    ("manufacturer", "Manufacturer / packer / importer"),
    ("packer", "Packer"), ("importer", "Importer"),
    ("fssai_license", "FSSAI licence"),
    ("batch_lot", "Batch / lot"), ("batch", "Batch / lot"),
    ("manufacturing_date", "Manufacture / packing date"),
    ("mfg_month_year", "Manufacture / packing date"),
    ("best_before", "Best before"), ("use_by", "Use by / expiry"),
    ("consumer_care", "Consumer care"),
    ("country_of_origin", "Country of origin"),
    ("dimensions", "Dimensions"),
]

FOOD_ROWS: list[tuple[str, str]] = [
    ("ingredients_raw", "Ingredients"),
    ("ingredients", "Ingredients"),
    ("nutrition_info", "Nutrition information"),
    ("nutrition", "Nutrition information"),
    ("veg_nonveg_dot", "Vegetarian / non-vegetarian symbol"),
    ("veg_nonveg_symbol", "Vegetarian / non-vegetarian symbol"),
    ("allergens", "Allergen declaration"),
    ("allergen_declaration", "Allergen declaration"),
    ("warnings", "Warnings / cautions"),
    ("storage_instructions", "Storage instructions"),
    ("cooking_instructions", "Preparation instructions"),
]


def build_report_data(repo, inspection_id: str) -> dict[str, Any] | None:
    """Assemble the full report from persisted rows. None if nothing analyzed."""
    inspection = repo.get_inspection(inspection_id)
    if not inspection:
        return None
    results = repo.results_for(inspection_id)
    if not results:
        return None
    by_product: dict[str, list] = {}
    for r in results:
        by_product.setdefault(r.get("product_id", ""), []).append(r)
    pid = sorted(by_product)[-1]
    product = repo.get_product(pid) if hasattr(repo, "get_product") else None
    violations = repo.violations_for(inspection_id)
    product_violations = [v for v in violations if v.get("product_id") == pid]
    return {
        "inspection": inspection,
        "product": product or {},
        "product_id": pid,
        "findings": by_product[pid],
        "violations": product_violations,
        "all_violations": violations,
        "evidence": repo.evidence_for(pid),
        "declarations": repo.declarations_for(pid),
        "policy": repo.scoring_policy("DEFAULT-2026"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def finding_score(findings: list[dict], policy: dict | None) -> dict[str, Any]:
    from app.engine import scoring as scoring_mod

    return scoring_mod.score(
        [{"rule_id": f.get("check_id", ""), "status": f.get("result", "")}
         for f in findings], policy)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# ------------------------------------------------------------ shared ---
def _counts(findings: list[dict]) -> dict[str, int]:
    out = {"PASS": 0, "FAIL": 0, "NEEDS_REVIEW": 0, "NOT_APPLICABLE": 0}
    for f in findings:
        out[f.get("result", "")] = out.get(f.get("result", ""), 0) + 1
    out["total"] = len(findings)
    return out


def _decl_value(d: dict) -> Any:
    return d.get("normalized_value") if d.get("normalized_value") is not None \
        else d.get("extracted_value")


def _online_evidence(data: dict[str, Any]) -> tuple[bool, str]:
    """Whether online-listing evidence was supplied for this inspection."""
    product = data.get("product", {}) or {}
    decls = data.get("declarations", []) or []
    for d in decls:
        if d.get("field_name") in ("source_listing_url",
                                   "online_listing_url", "listing_url",
                                   "ecommerce_declarations",
                                   "online_listing_evidence",
                                   "ecommerce_evidence"):
            val = _decl_value(d)
            if val not in (None, "", False, "False", "false"):
                return True, str(val)
    for key in ("source_listing_url", "online_listing_url"):
        if product.get(key):
            return True, str(product[key])
    ctx = str(product.get("inspection_context") or "").upper()
    if ctx in ("ONLINE_LISTING", "PACKAGE_AND_ONLINE_LISTING"):
        return True, ctx
    return False, ""


def _inspection_type_label(data: dict[str, Any]) -> str:
    product = data.get("product", {}) or {}
    ctx = str(product.get("inspection_context") or "").upper()
    if ctx == "PACKAGE_ONLY":
        return "Physical Package Inspection"
    if ctx == "ONLINE_LISTING":
        return "Online Listing Inspection"
    if ctx in ("PACKAGE_AND_ONLINE_LISTING",):
        return "Physical Package + Online Listing Inspection"
    declared = next(
        (d for d in data.get("declarations", [])
         if d.get("field_name") == "inspection_context"
         and _decl_value(d)), None)
    if declared:
        raw = str(_decl_value(declared)).upper()
        return {"PACKAGE_ONLY": "Physical Package Inspection",
                "ONLINE_LISTING": "Online Listing Inspection",
                "PACKAGE_AND_ONLINE_LISTING":
                    "Physical Package + Online Listing Inspection"}.get(
                        raw, "Physical Package Inspection")
    itype = str((data.get("inspection", {}) or {}).get("inspection_type")
                or "").upper()
    if itype in ("PHYSICAL", "PACKAGE_ONLY", ""):
        return "Physical Package Inspection"
    return itype.replace("_", " ").title()


def _split_findings(findings: list[dict]) -> tuple[list, list]:
    package = [f for f in findings
               if f.get("check_id") not in ONLINE_CHECK_IDS]
    online = [f for f in findings
              if f.get("check_id") in ONLINE_CHECK_IDS]
    return package, online


def _conf_suffix(conf: Any, empty: str = "") -> str:
    if conf is None:
        return empty
    return f" \u00b7 confidence {conf}"


def _conf_suffix_manual(conf: Any) -> str:
    if conf is None:
        return " \u00b7 confidence n/a (manual)"
    return f" \u00b7 confidence {conf}"


def _decls_by_field(data: dict[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in data.get("declarations", []) or []:
        out.setdefault(str(d.get("field_name", "")), d)
    return out


def _product_field(data: dict[str, Any], *names: str) -> Any:
    product = data.get("product", {}) or {}
    decls = _decls_by_field(data)
    for name in names:
        if product.get(name) not in (None, ""):
            return product[name]
        if name in decls and _decl_value(decls[name]) not in (None, ""):
            return _decl_value(decls[name])
    return None


def _package_info_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    decls = _decls_by_field(data)
    product = data.get("product", {}) or {}
    rows = []
    seen: set[str] = set()
    for field, label in PACKAGE_ROWS:
        if label in seen:
            continue
        val = None
        conf = None
        origin = "MANUAL"
        if field in decls:
            val = _decl_value(decls[field])
            conf = decls[field].get("confidence")
            eng = str(decls[field].get("ocr_engine") or "")
            origin = "OCR" if eng and "manual" not in eng.lower() else "MANUAL"
        elif product.get(field) not in (None, ""):
            val = product[field]
        else:
            continue
        seen.add(label)
        eng = (decls[field].get("ocr_engine") if field in decls else "") or ""
        source = (f"OCR ({eng})" if origin == "OCR"
                  else "MANUAL entry")
        rows.append({"label": label, "value": val, "confidence": conf,
                     "origin": origin, "source": source})
    # Quantity + unit displayed together when split across columns.
    return rows


def _product_identity(data: dict[str, Any]) -> list[tuple[str, Any]]:
    """Section 2: who the product is, before any declaration verdicts."""
    product = data.get("product", {}) or {}
    return [
        ("Product name", product.get("product_name") or "Unnamed product"),
        ("Brand", product.get("brand") or "—"),
        ("Category", product.get("category") or "—"),
    ]


def _food_rows_by_group(data: dict[str, Any]) -> dict[str, list[dict]]:
    """Split food rows into ingredients / nutrition / symbol groups."""
    groups = {"ingredients": [], "nutrition": [], "symbol": []}
    for r in _food_info_rows(data):
        field = next((f for f, lab in FOOD_ROWS if lab == r["label"]), "")
        if field.startswith("ingredient"):
            groups["ingredients"].append(r)
        elif field.startswith("veg"):
            groups["symbol"].append(r)
        else:
            groups["nutrition"].append(r)
    return groups


def _food_info_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    decls = _decls_by_field(data)
    rows = []
    for field, label in FOOD_ROWS:
        if field in decls and _decl_value(decls[field]) not in (None, ""):
            eng = str(decls[field].get("ocr_engine") or "")
            rows.append({
                "label": label,
                "value": _decl_value(decls[field]),
                "confidence": decls[field].get("confidence"),
                "origin": ("OCR" if eng and "manual" not in eng.lower()
                           else "MANUAL")})
    return rows


# -------------------------------------------------------------- HTML ---
_BADGE = {
    "PASS": ("#e3f7ed", "#08784e"),
    "FAIL": ("#fce6e4", "#b43b37"),
    "NEEDS_REVIEW": ("#fff4cf", "#946b09"),
    "NOT_APPLICABLE": ("#edf1ef", "#53625b"),
    "DETECTED": ("#e3f7ed", "#08784e"),
    "MISSING": ("#fce6e4", "#b43b37"),
    "NOT CHECKED": ("#edf1ef", "#53625b"),
}


def _badge(status: str) -> str:
    bg, fg = _BADGE.get(status, _BADGE["NOT_APPLICABLE"])
    return (f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'font-size:11px;font-weight:800;padding:2px 10px;'
            f'border-radius:999px">{_esc(status.replace("_", " "))}</span>')


def _decl_badge(value: Any, conf: Any) -> str:
    if value in (None, ""):
        return _badge("MISSING")
    try:
        low = conf is not None and float(conf) < 0.6
    except (TypeError, ValueError):
        low = False
    return _badge("NEEDS_REVIEW") if low else _badge("DETECTED")


def _finding_card(f: dict[str, Any]) -> str:
    status = f.get("result", "")
    conf = f.get("confidence")
    return (
        f"<div style='border:1px solid #e8eee9;border-radius:10px;"
        f"padding:12px 14px;margin:8px 0'>"
        f"<div style='display:flex;justify-content:space-between;gap:8px'>"
        f"<b style='font-family:monospace;font-size:12px'>{_esc(f.get('check_id'))}</b>"
        f"{_badge(status)}</div>"
        f"<p style='margin:8px 0 2px;font-size:13px;font-weight:700'>"
        f"{_esc(f.get('requirement'))}</p>"
        f"<p style='margin:2px 0;font-size:12px'><b>Observed:</b> "
        f"{_esc(f.get('detected_value') or '—')}</p>"
        f"<p style='margin:2px 0;font-size:12px'><b>Why:</b> "
        f"{_esc(f.get('explanation'))}</p>"
        f"<p style='margin:6px 0 0;font-size:11px;color:#718078'>"
        f"Applicable law: {_esc(f.get('source_title'))} · "
        f"Rule {_esc(f.get('rule_number'))}({_esc(f.get('sub_rule'))})"
        f"{_esc(f.get('clause') or '')} · "
        f"version {_esc((f.get('rule_version_id') or '')[:8])}… · "
        f"effective {_esc(f.get('effective_from'))} · "
        f"confidence {_esc(conf if conf is not None else 'n/a (manual)')}"
        f"</p></div>")


def render_html(data: dict[str, Any], score: dict[str, Any]) -> str:
    insp, prod = data["inspection"], data["product"]
    counts = _counts(data["findings"])
    package, online = _split_findings(data["findings"])
    has_online, online_ref = _online_evidence(data)
    itype = _inspection_type_label(data)

    def group(status: str, items: list[dict]) -> str:
        cards = "".join(_finding_card(f) for f in items
                        if f.get("result") == status)
        if not cards:
            return ""
        return (f"<h3>{status.replace('_', ' ')} "
                f"({sum(1 for f in items if f.get('result') == status)})</h3>"
                f"{cards}")

    critical = [f for f in package if f.get("result") in ("FAIL",
                                                          "NEEDS_REVIEW")]
    crit_html = "".join(
        f"<li><b>{_esc(f.get('check_id'))}</b> — "
        f"{_esc(f.get('requirement'))} "
        f"({_esc(f.get('result', '').replace('_', ' '))})</li>"
        for f in critical) or "<li>No critical findings.</li>"

    def _decl_status_badge(value: Any, conf: Any) -> str:
        # OCR uncertainty renders as review, never as a violation.
        if value in (None, ""):
            return _badge("NOT CHECKED")
        return _decl_badge(value, conf)

    def _conf_cell(conf: Any) -> str:
        return _esc(conf if conf is not None else "—")

    info_rows = "".join(
        f"<tr><td>{_esc(r['label'])}</td>"
        f"<td><b>{_esc(r['value'])}</b></td>"
        f"<td>{_decl_status_badge(r['value'], r['confidence'])}</td>"
        f"<td>{_conf_cell(r['confidence'])}</td>"
        f"<td><small style='color:#718078'>{_esc(r['source'])}</small></td>"
        f"</tr>"
        for r in _package_info_rows(data)) or \
        "<tr><td colspan=5>No package declarations stored.</td></tr>"

    def _food_row(r: dict) -> str:
        # Garbage guard: an incoherent ingredient blob is never displayed
        # as a valid declaration. It renders as NEEDS REVIEW with the raw
        # OCR evidence and the reason, so the officer sees exactly why.
        if r["label"] == "Ingredients" and r["value"]:
            try:
                from app.services.ocr.food import (
                    score_ingredient_coherence)
                coh = score_ingredient_coherence(str(r["value"]))
                if coh["score"] < 0.35:
                    return (
                        f"<tr><td>{_esc(r['label'])}</td>"
                        f"<td><b>NEEDS REVIEW</b><br><small>"
                        f"Ingredient text was partially detected but did "
                        f"not meet OCR coherence threshold.</small><br>"
                        f"<small style='color:#718078'>OCR evidence: "
                        f"{_esc(str(r['value'])[:400])}</small></td>"
                        f"<td>{_badge('NEEDS_REVIEW')}</td>"
                        f"<td>{_conf_cell(r['confidence'])}</td>"
                        f"<td><small style='color:#718078'>"
                        f"{_esc(r['origin'])}</small></td></tr>")
            except Exception:
                pass  # scoring must never break report rendering
        return (
            f"<tr><td>{_esc(r['label'])}</td>"
            f"<td><b>{_esc(str(r['value'])[:400])}</b></td>"
            f"<td>{_decl_status_badge(r['value'], r['confidence'])}</td>"
            f"<td>{_conf_cell(r['confidence'])}</td>"
            f"<td><small style='color:#718078'>{_esc(r['origin'])}</small></td>"
            f"</tr>")

    def _food_table(rows: list[dict]) -> str:
        if not rows:
            return ("<p class='meta'>No data stored — appears here when the "
                    "extraction layer supplies it.</p>")
        body = "".join(_food_row(r) for r in rows)
        return (f"<table><thead><tr><th>Field</th><th>Value</th><th>Status</th>"
                f"<th>Confidence</th><th>Source</th></tr></thead>"
                f"<tbody>{body}</tbody></table>")

    food_groups = _food_rows_by_group(data)
    identity_html = "".join(
        f"<tr><td>{_esc(label)}</td><td><b>{_esc(value)}</b></td></tr>"
        for label, value in _product_identity(data))
    evidence_rows = data.get("evidence", []) or []
    evidence_html = ("".join(
        f"<li>{_esc(e.get('evidence_type'))} — {_esc(e.get('source'))}: "
        f"{_esc(str(e.get('description'))[:160])}</li>"
        for e in evidence_rows)
        or "<li>No evidence items recorded.</li>")

    online_html = ""
    if online:
        online_html = "".join(_finding_card(f) for f in online)
    if not has_online:
        online_html = (
            "<p><b>Status:</b> NOT CHECKED</p>"
            "<p><b>Reason:</b> No e-commerce listing evidence was provided. "
            "Physical package images cannot establish online listing "
            "compliance.</p>") + online_html

    pkg_groups = "".join(group(s, package) for s in
                         ("FAIL", "NEEDS_REVIEW", "PASS", "NOT_APPLICABLE"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>LegalAkshi compliance report · {_esc(prod.get('product_name'))}</title>
<style>body{{font-family:Inter,Arial,sans-serif;color:#20382b;background:#f7f8f6;margin:0;padding:32px}}
main{{max-width:960px;margin:auto;background:#fff;padding:40px;border:1px solid #dfe9e2;border-radius:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e8eee9;vertical-align:top}}
th{{color:#718078;font-size:11px;text-transform:uppercase}}.meta{{color:#718078}}.score{{font-size:40px;font-weight:800}}
h2{{border-bottom:2px solid #18B978;padding-bottom:6px}}h3{{color:#3d5145}}
footer{{margin-top:28px;color:#87958c;font-size:11px;border-top:1px solid #e8eee9;padding-top:12px}}</style>
</head><body><main>
<p class="meta">LEGALAKSHI · Food Package Compliance Report · generated {_esc(data['generated_at'])}</p>
<h1>{_esc(prod.get('product_name'))}</h1>
<p class="meta">Inspection {_esc(insp.get('inspection_id'))} · business {_esc(insp.get('business_name'))} ·
inspected {_esc(insp.get('inspection_date'))} · inspector {_esc(insp.get('inspector_name'))}</p>
<p class="meta">Inspection type: <b>{_esc(itype)}</b></p>
<p class="score">{score.get('value')}<small style="font-size:14px"> / {score.get('out_of')}</small></p>
<p>Status: <strong>{_esc(score.get('status'))}</strong> ·
{'<strong>NOT finalizable — inspector review required.</strong>' if not score.get('finalizable') else 'Finalizable.'}
· policy {_esc(score.get('policy'))} {_esc(score.get('policy_version'))}</p>
<h2>Executive summary</h2>
<p>{counts['PASS']} passed · {counts['FAIL']} failed · {counts['NEEDS_REVIEW']} need review · {counts['NOT_APPLICABLE']} not applicable (of {counts['total']} checks).</p>
<h2>Critical findings</h2><ul>{crit_html}</ul>
<h2>Product identity</h2>
<table><thead><tr><th>Field</th><th>Value</th></tr></thead>
<tbody>{identity_html}</tbody></table>
<h2>Mandatory package declarations</h2>
<table><thead><tr><th>Field</th><th>Value</th><th>Status</th><th>Confidence</th><th>Source</th></tr></thead>
<tbody>{info_rows}</tbody></table>
<p class="meta">OCR uncertainty is shown as review — never as a violation.</p>
<h2>Food ingredients &amp; additives</h2>{_food_table(food_groups['ingredients'])}
<h2>Nutrition &amp; allergens</h2>{_food_table(food_groups['nutrition'])}
<h2>Vegetarian / non-vegetarian symbol</h2>{_food_table(food_groups['symbol'])}
<h2>Legal compliance findings — physical package</h2>{pkg_groups or '<p>No package findings.</p>'}
<h2>Online listing compliance</h2>{online_html}
<h2>Evidence / provenance</h2><ul>{evidence_html}</ul>
<h2>Potential violations ({len(data['violations'])})</h2>
<p class="meta">Engine FAILs are potential violations only — an inspector decides (FAIL ≠ confirmed offence).</p>
<h2>Final inspector action</h2>
<p>Automated findings are <b>potential compliance findings</b> and require inspector review. Potential violations are not final violations. Keep the officer decision workflow: verify, then CONFIRM / REJECT / REQUIRE REVIEW.</p>
<footer>Generated by the LegalAkshi rule engine from persisted analysis data.
AI extracts. Rules interpret. Evidence supports. Scoring summarizes. Inspector decides.</footer>
</main></body></html>"""


# --------------------------------------------------------------- PDF ---
def _latin(value: Any) -> str:
    text = "" if value is None else str(value)
    return (text.replace("₹", "Rs.").replace("“", '"').replace("”", '"')
            .replace("‘", "'").replace("’", "'").replace("—", "-")
            .replace("–", "-").replace("…", "...")
            .encode("latin-1", "replace").decode("latin-1"))


def _short_id(value: Any) -> str:
    """Display form for UUIDs (fpdf2 cannot wrap 36-char tokens; ASCII only
    because core PDF fonts are latin-1)."""
    text = "" if value is None else str(value)
    return text[:8] + "..." if len(text) > 12 else text


_BADGE_PDF = {"PASS": "[PASS]", "FAIL": "[FAIL]",
              "NEEDS_REVIEW": "[NEEDS REVIEW]",
              "NOT_APPLICABLE": "[N/A]"}


def render_pdf(data: dict[str, Any], score: dict[str, Any]) -> bytes:
    from fpdf import FPDF

    insp, prod = data["inspection"], data["product"]
    counts = _counts(data["findings"])
    package, online = _split_findings(data["findings"])
    has_online, _ = _online_evidence(data)
    itype = _inspection_type_label(data)

    class Report(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 11)
            self.set_text_color(24, 185, 120)
            self.cell(0, 8, "LegalAkshi  |  Official compliance report", new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(220, 230, 223)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(4)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(135, 149, 140)
            self.cell(0, 10, _latin(f"Page {self.page_no()}/{{nb}}  |  persisted analysis "
                             f"{_short_id(insp.get('inspection_id'))}"), align="C")

        def section(self, title: str, size: int = 14):
            self.set_text_color(23, 58, 42)
            self.set_font("Helvetica", "B", size)
            self.cell(0, 10, _latin(title), new_x="LMARGIN", new_y="NEXT")

        def body(self, text: str, size: int = 10, style: str = ""):
            self.set_font("Helvetica", style, size)
            self.set_text_color(32, 56, 43)
            self.multi_cell(0, 6, _latin(text), new_x="LMARGIN", new_y="NEXT")

        def muted(self, text: str, size: int = 9):
            self.set_font("Helvetica", "", size)
            self.set_text_color(113, 120, 114)
            self.multi_cell(0, 5.5, _latin(text), new_x="LMARGIN", new_y="NEXT")

    pdf = Report()
    pdf.alias_nb_pages("{nb}")
    pdf.set_compression(False)  # keep text greppable for tests/audit
    pdf.set_auto_page_break(True, margin=20)

    # ---- page 1: cover + executive summary ----
    pdf.add_page()
    pdf.muted("LEGALAKSHI  |  Food Package Compliance Report  |  "
              f"Generated {data['generated_at']}")
    pdf.set_text_color(23, 58, 42)
    pdf.set_font("Helvetica", "B", 22)
    pdf.multi_cell(0, 11, _latin(prod.get("product_name") or "Unnamed product"),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.muted(
        f"Inspection {_short_id(insp.get('inspection_id'))} | "
        f"{insp.get('business_name')} | {insp.get('inspection_date')} | "
        f"Inspector {insp.get('inspector_name')}")
    pdf.body(f"Inspection type: {itype}", style="B")
    pdf.set_text_color(23, 58, 42)
    pdf.set_font("Helvetica", "B", 28)
    pdf.cell(0, 14, _latin(f"Score: {score.get('value')} / {score.get('out_of')}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.body(
        f"Overall status: {score.get('status')} | "
        f"{'NOT finalizable - inspector review required.' if not score.get('finalizable') else 'Finalizable.'} | "
        f"Policy {score.get('policy')} {score.get('policy_version')}")
    pdf.section("Executive summary")
    pdf.body(f"{counts['PASS']} checks passed, {counts['FAIL']} failed, "
             f"{counts['NEEDS_REVIEW']} need review, "
             f"{counts['NOT_APPLICABLE']} not applicable "
             f"(of {counts['total']} checks).")
    pdf.section("Critical findings")
    critical = [f for f in package if f.get("result") in ("FAIL",
                                                          "NEEDS_REVIEW")]
    if not critical:
        pdf.body("No critical findings.")
    for f in critical:
        badge = _BADGE_PDF.get(f.get("result", ""), "[?]")
        pdf.body(f"{badge} {f.get('check_id')} - {f.get('requirement')}",
                 style="B")
        pdf.muted(f"Why: {f.get('explanation')}")

    # ---- product identity + mandatory declarations ----
    pdf.add_page()
    pdf.section("Product identity")
    for label, value in _product_identity(data):
        pdf.body(f"{label}: {value}", style="B")
    pdf.section("Mandatory package declarations")
    pdf.muted("Field | Value | Status | Confidence | Source. OCR uncertainty "
              "is shown as review - never as a violation.")
    rows = _package_info_rows(data)
    if not rows:
        pdf.body("No package declarations stored.")
    for r in rows:
        try:
            low = r["confidence"] is not None and float(r["confidence"]) < 0.6
        except (TypeError, ValueError):
            low = False
        mark = "[not checked]" if r["value"] in (None, "") else \
            ("[needs review]" if low else "[detected]")
        conf = (r["confidence"] if r["confidence"] is not None else "-")
        pdf.body(f"{r['label']}: {r['value'] if r['value'] not in (None, '') else '-'} "
                 f"{mark} | confidence: {conf} | source: {r['source']}",
                 style="B")

    # ---- food label analysis, split per section ----
    pdf.add_page()
    food_groups = _food_rows_by_group(data)
    for title, key in (("Food ingredients & additives", "ingredients"),
                       ("Nutrition & allergens", "nutrition"),
                       ("Vegetarian / non-vegetarian symbol", "symbol")):
        pdf.section(title)
        if not food_groups[key]:
            pdf.body("No data stored - appears here when the extraction "
                     "layer supplies it.")
        for r in food_groups[key]:
            # Garbage guard (mirrors the HTML view): incoherent ingredient
            # blobs render as NEEDS REVIEW with raw evidence, never as a
            # valid declaration.
            if r["label"] == "Ingredients" and r["value"]:
                try:
                    from app.services.ocr.food import (
                        score_ingredient_coherence)
                    if (score_ingredient_coherence(str(r["value"]))
                            ["score"] < 0.35):
                        pdf.body("Ingredients: NEEDS REVIEW", style="B")
                        pdf.body("Reason: Ingredient text was partially "
                                 "detected but did not meet OCR coherence "
                                 "threshold.")
                        pdf.muted(f"OCR evidence: {str(r['value'])[:300]}")
                        continue
                except Exception:
                    pass
            pdf.body(f"{r['label']}: {str(r['value'])[:500]}", style="B")
            pdf.muted(f"provenance: {r['origin']}")

    # ---- pages 4+: legal compliance findings, grouped ----
    pdf.add_page()
    pdf.section("Legal compliance findings - physical package")
    for status in ("FAIL", "NEEDS_REVIEW", "PASS", "NOT_APPLICABLE"):
        group = [f for f in package if f.get("result") == status]
        if not group:
            continue
        label = status.replace("_", " ")
        pdf.section(f"{label} ({len(group)})", size=12)
        for f in group:
            badge = _BADGE_PDF.get(status, "[?]")
            pdf.body(f"{badge} {f.get('check_id')}", style="B")
            pdf.body(f"Requirement: {f.get('requirement')}")
            if status in ("FAIL", "NEEDS_REVIEW"):
                pdf.body(f"Observed value: {f.get('detected_value') or '-'}")
                pdf.body(f"Why: {f.get('explanation')}")
                pdf.muted(
                    f"Applicable law: {f.get('source_title')} | "
                    f"Rule {f.get('rule_number')}({f.get('sub_rule')})"
                    f"{f.get('clause') or ''} | "
                    f"version {_short_id(f.get('rule_version_id'))} effective "
                    f"{f.get('effective_from')} | confidence: "
                    f"{f.get('confidence') if f.get('confidence') is not None else 'n/a (manual)'}")
            else:
                pdf.muted(f"Detected: {f.get('detected_value') or '-'} | "
                          f"{f.get('explanation')}")
            pdf.ln(2)

    # ---- online listing compliance (always separate) ----
    pdf.section("Online listing compliance")
    if not has_online:
        pdf.body("Status: NOT CHECKED", style="B")
        pdf.body("Reason: No e-commerce listing evidence was provided. "
                 "Physical package images cannot establish online listing "
                 "compliance.")
    for f in online:
        badge = _BADGE_PDF.get(f.get("result", ""), "[?]")
        pdf.body(f"{badge} {f.get('check_id')} - {f.get('requirement')}",
                 style="B")
        pdf.body(f"Detected: {f.get('detected_value') or '-'} | "
                 f"Confidence: {f.get('confidence') if f.get('confidence') is not None else 'n/a (manual)'}")
        pdf.body(f"Why: {f.get('explanation')}")
        pdf.muted(f"Legal: Rule {f.get('rule_number')}({f.get('sub_rule')})"
                  f"{f.get('clause') or ''} | "
                  f"version {_short_id(f.get('rule_version_id'))} effective "
                  f"{f.get('effective_from')} | {f.get('source_title')}")

    # ---- evidence / provenance ----
    pdf.section("Evidence / provenance")
    evidence_rows = data.get("evidence", []) or []
    if not evidence_rows:
        pdf.body("No evidence items recorded.")
    for e in evidence_rows:
        pdf.body(f"{e.get('evidence_type')} - {e.get('source')}", style="B")
        pdf.muted(str(e.get("description"))[:200])

    # ---- potential violations + inspector action ----
    pdf.section(f"Potential violations ({len(data['violations'])})")
    pdf.body("Engine FAILs are potential violations only - an inspector decides.")
    for v in data["violations"]:
        pdf.body(_latin(
            f"- {v.get('description')} [{v.get('inspector_status')}]"
            f"{' by ' + str(v.get('inspector_id')) if v.get('inspector_id') else ''}"))
    if not data["violations"]:
        pdf.body("- None raised.")
    pdf.ln(2)
    pdf.section("Final inspector action")
    pdf.body("Automated findings are potential compliance findings and "
             "require inspector review. Potential violations are not final "
             "violations. Verify, then CONFIRM / REJECT / REQUIRE REVIEW in "
             "the officer decision workflow.", style="B")
    out = pdf.output()
    return bytes(out)
