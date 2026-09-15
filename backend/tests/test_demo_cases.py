"""Demo cases A-E (backend/demo/cases.json — DEMO/TEST data, not law)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import engine as engine_mod
from app.engine.facts import ASSUMED
from app.repositories.memory import MemoryRepo

CASES = json.loads((Path(__file__).resolve().parent.parent
                    / "demo" / "cases.json").read_text(encoding="utf-8"))["cases"]


def _run(repo, case):
    insp = repo.create_inspection(dict(case["inspection"]))
    prod = repo.add_product(insp["inspection_id"], dict(case["product"]))
    pid = prod["product_id"]
    decls = list(repo.declarations_for(pid))
    for extra in case.get("ocr_declarations", []):  # measured OCR evidence
        decls.append(extra)
    return engine_mod.analyze(repo, insp, repo.get_product(pid), decls,
                              as_of=case["inspection"]["inspection_date"],
                              default_origin=ASSUMED)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_demo_case(case):
    assert "DEMO" in case.get("title", "") or case["id"].startswith("CASE-")
    repo = MemoryRepo()
    res = _run(repo, case)
    by_check = {f["rule_id"]: f["status"] for f in res["findings"]}
    for check_id, expected in case["expect"].items():
        if check_id in ("violations", "violation_status", "finalizable"):
            continue
        assert by_check.get(check_id) == expected, \
            f"{case['id']} {check_id}: {by_check.get(check_id)}"
    if "violations" in case["expect"]:
        assert len(res["violation_ids"]) == case["expect"]["violations"]
    if "violation_status" in case["expect"]:
        violations = repo.violations_for(res["inspection_id"])
        assert violations
        assert all(v["inspector_status"] == case["expect"]["violation_status"]
                   for v in violations)
    if "finalizable" in case["expect"]:
        assert res["score"]["finalizable"] is case["expect"]["finalizable"]
