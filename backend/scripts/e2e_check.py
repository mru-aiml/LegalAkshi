"""End-to-end flow check against a RUNNING backend (needs PostgreSQL).

Runs the full Phase-3 §5 flow and prints persistence cross-checks:
inspection -> product -> analyze -> compliance -> violations -> verify ->
report. Exits non-zero on any mismatch. Manual declarations are used
(no OCR claimed).

Usage (backend/ with API on :8000):
    python scripts/e2e_check.py [BASE_URL]
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
failures: list[str] = []


# Local-dev officer identity (production uses Clerk JWT; see app/core/auth.py).
# When the backend verifies Clerk JWTs (CLERK_JWKS_URL set), dev headers are
# ignored: export E2E_BEARER_TOKEN with a real officer session JWT instead.
import os as _os

_E2E_BEARER = _os.environ.get("E2E_BEARER_TOKEN", "")
OFFICER_HEADERS = ({"Authorization": f"Bearer {_E2E_BEARER}"} if _E2E_BEARER
                   else {"X-LegalAkshi-Role": "officer",
                         "X-LegalAkshi-User": "e2e-officer"})


def call(method: str, path: str, body: dict | None = None,
         headers: dict | None = None):
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=merged)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode()[:300]}


def expect(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name + (" " + detail if detail else ""))


def main() -> int:
    print(f"backend: {BASE}")
    status, health = call("GET", "/healthz")
    expect("healthz ok", status == 200 and health.get("status") == "ok", str(health))
    if failures:
        return 1

    status, insp = call("POST", "/api/v1/inspections", {
        "inspector_id": "E2E-1", "inspector_name": "E2E Officer",
        "business_name": "E2E Store", "inspection_date": "2026-09-15"})
    expect("create inspection 201", status == 201, str(insp))
    iid = insp.get("inspection_id")
    if not iid:
        return 1

    status, prod = call("POST", f"/api/v1/inspections/{iid}/products", {
        "product_name": "E2E Atta", "category": "GENERAL", "is_prepackaged": True,
        "manufacturer": "E2E Foods", "quantity": 5, "quantity_unit": "kg",
        "quantity_type": "weight", "manufacturing_date": "2024-05-01",
        "consumer_care": "1800-000", "unit_sale_price": "Rs.50 per kg"})
    expect("add product 201 (no MRP -> FAIL expected)", status == 201, str(prod))
    pid = prod.get("product_id")

    status, analysis = call("POST", f"/api/v1/inspections/{iid}/analyze",
                            {"product_id": pid})
    expect("analyze 200", status == 200, str(analysis)[:200])
    findings = analysis.get("findings", [])
    mrp = next((f for f in findings if f.get("rule_id") == "CHK-MRP"), {})
    expect("CHK-MRP FAIL", mrp.get("status") == "FAIL", str(mrp.get("status")))
    vids = analysis.get("violation_ids", [])
    expect("violation created", len(vids) == 1, str(vids))

    status, comp = call("GET", f"/api/v1/inspections/{iid}/products/{pid}/compliance")
    expect("compliance persisted", status == 200 and len(comp) == len(findings),
           f"{len(comp) if isinstance(comp, list) else comp} vs {len(findings)}")

    status, viols = call("GET", f"/api/v1/inspections/{iid}/violations")
    items = viols.get("items", []) if isinstance(viols, dict) else []
    expect("violation PENDING", status == 200 and items
           and items[0]["inspector_status"] == "PENDING", str(viols)[:200])

    status, anon = call("POST", f"/api/v1/violations/{vids[0]}/verify",
                          {"decision": "CONFIRMED", "inspector_id": "E2E-1"})
    expect("verify without role -> 401", status == 401, str(anon)[:200])

    status, ver = call("POST", f"/api/v1/violations/{vids[0]}/verify",
                       {"decision": "CONFIRMED", "inspector_id": "E2E-1",
                        "verification_notes": "re-checked shelf"},
                       headers=OFFICER_HEADERS)
    if status == 401 and not _E2E_BEARER:
        print("SKIP verify CONFIRMED: backend verifies Clerk JWTs; re-run with "
              "E2E_BEARER_TOKEN=<officer session JWT> to exercise this step.")
    else:
        expect("verify CONFIRMED (officer)", status == 200
               and ver.get("inspector_status") == "CONFIRMED", str(ver)[:200])

    status, rep = call("GET", f"/api/v1/reports/{iid}")
    expect("report ok", status == 200 and rep.get("report_id") == iid,
           str(rep)[:200])

    print("FAILURES:", failures if failures else "none")
    return 1 if failures else 0


if __name__ == "__main__":
    import urllib.error  # noqa: E402

    raise SystemExit(main())
