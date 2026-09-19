"""FOOD-specific rule applicability — config-driven, never hard-coded law.

The seeded LMPC Rule 6 registry (legalakshi_master_v4.json) is immutable
legal history: the cosmetic CHK-VEG-NONVEG rule keeps its meaning. Food
products use SEPARATE checks (CHK-VEG-NONVEG-FOOD, CHK-FSSAI-LIC,
CHK-INGREDIENT-SCREEN) specified in
backend/authoritative/food_rules_v1.json and added through the EXISTING
authoritative path (repo.insert_registry_entry / insert_rule_version /
insert_applicability — the same path /admin/rules/sync uses).

Nothing here auto-mutates the default seed, so existing scoring and
regression suites are unaffected. ``ensure_food_rules`` is opt-in (admin
action / explicit test setup) and is idempotent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SPECS_PATH = (Path(__file__).resolve().parent.parent.parent
              / "authoritative" / "food_rules_v1.json")


def load_food_specs(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or SPECS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def ensure_food_rules(repo: Any,
                      specs: dict[str, Any] | None = None) -> list[str]:
    """Insert FOOD-specific checks into a repo via the authoritative path.

    Returns the check_ids that are now present. Idempotent: existing
    check_ids are skipped, never overwritten.
    """
    specs = specs if specs is not None else load_food_specs()
    ensured: list[str] = []
    existing = {c.get("check_id") for c in repo.list_checks()}
    for spec in specs.get("checks", []):
        check_id = spec["check_id"]
        if check_id in existing:
            ensured.append(check_id)
            continue
        repo.insert_registry_entry({
            "check_id": check_id,
            "title": spec.get("title", check_id),
            "field_name": spec.get("field_name", ""),
            "check_type": spec.get("check_type", "MANUAL_REVIEW"),
            "mandatory_default": bool(spec.get("mandatory_default", False)),
            "scoring_category": spec.get("scoring_category", "OTHER"),
            "description": spec.get("requirement", ""),
            "rule_number": spec.get("rule_number", "6"),
            "sub_rule": spec.get("sub_rule"),
            "clause": spec.get("clause"),
        })
        version = repo.insert_rule_version({
            "check_id": check_id,
            "requirement": spec.get("requirement", ""),
            "legal_text_or_paraphrase": spec.get("requirement", ""),
            "effective_from": specs.get("effective_date", "2026-09-16"),
            "effective_to": None,
            "status": "IN_FORCE",
            "provenance": {
                "source_title": "LegalAkshi food rules v1 (config-driven)",
                "source_url": None,
                "authenticity_status": "VERIFICATION_REQUIRED"},
        })
        if spec.get("when") is not None:
            repo.insert_applicability({
                "rule_version_id": version["rule_version_id"],
                "check_id": check_id,
                "condition_expression": spec["when"],
                "applicable_result": "REQUIRED",
                "reason": (f"Food product rule ({check_id}); applies when "
                           f"the product is classified as food."),
            })
        if hasattr(repo, "upsert_weight"):
            try:
                repo.upsert_weight("DEFAULT-2026", check_id,
                                   float(spec.get("weight", 0)),
                                   str(spec.get("severity", "LOW")))
            except Exception:
                pass
        ensured.append(check_id)
    return ensured
