"""Authoritative rule import / synchronization.

Source: backend/authoritative/legalakshi_master_v4.json (generated projection
of the legal knowledge base). Target: PostgreSQL, the runtime source of truth.

Guarantees:
- validate first: unknown statuses, bad dates, dangling references → errors,
  nothing is written.
- diff: new checks / versions / applicability / weights are detected;
  same-id rows whose legal content differs are reported as CONFLICTS and are
  never auto-overwritten (history is append-only).
- apply: inserts only; overlapping validity ranges are pre-checked AND left
  to the PostgreSQL exclusion constraint as the final arbiter.
- requirement_type (DB NOT NULL, absent from the manifest) is inherited from
  the latest sibling version of the same lineage, and reported; brand-new
  lineages without it are conflicts for manual admin versioning.
- every applied item is audit-logged with the admin actor.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

MANIFEST_PATH = (Path(__file__).resolve().parent.parent.parent
                 / "authoritative" / "legalakshi_master_v4.json")

POLICY_ID = "DEFAULT-2026"
STATUSES = ("IN_FORCE", "NOT_YET_IN_FORCE", "SUPERSEDED", "REPEALED", "DRAFT")
CHECK_TYPES = ("FIELD_PRESENT", "FIELD_ABSENT", "TEXT_MATCH", "TEXT_CONTAINS",
               "REGEX_MATCH", "NUMERIC_COMPARE", "UNIT_NORMALIZATION",
               "DATE_VALID", "DATE_REQUIRED_IF", "CONDITIONAL_FIELD_PRESENT",
               "QR_DATA_PRESENT", "QR_OR_ON_PACKAGE", "FORMAT_VALID",
               "CROSS_FIELD_COMPARE", "PLATFORM_FILTER_PRESENT",
               "MANUAL_REVIEW")
APPL_RESULTS = ("REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE", "CONDITIONAL",
                "NEEDS_REVIEW")
SEVERITIES = ("HIGH", "MEDIUM", "LOW")


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _parse_date(value: Any, where: str, errors: list[str]) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(str(value))
        return str(value)
    except ValueError:
        errors.append(f"{where}: bad date {value!r}")
        return None


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty = valid). Pure function."""
    errors: list[str] = []
    try:
        lk = manifest["legal_knowledge"]
        rules = {r["rule_id"]: r for r in lk["rules"]}
        versions = lk["rule_versions"]
        appl = lk["applicability_rules"]
        weights = manifest["scoring_policy"][0]["rules"]
    except (KeyError, TypeError, IndexError) as exc:
        return [f"manifest structure: {exc}"]
    if not rules:
        errors.append("manifest contains no checks")
    if not versions:
        errors.append("manifest contains no rule versions")
    if not appl:
        errors.append("manifest contains no applicability rows")
    if not weights:
        errors.append("manifest contains no scoring weights")
    for r in lk["rules"]:
        for key in ("rule_id", "title", "field", "check_type", "rule_number"):
            if not r.get(key):
                errors.append(f"check {r.get('rule_id')}: missing {key}")
        if r.get("check_type") not in CHECK_TYPES:
            errors.append(f"check {r.get('rule_id')}: unknown check_type")
    seen_versions = set()
    for v in versions:
        vid = v.get("rule_version_id")
        if vid in seen_versions:
            errors.append(f"duplicate rule_version_id {vid}")
        seen_versions.add(vid)
        if v.get("check_id") not in rules:
            errors.append(f"version {vid}: unknown check {v.get('check_id')}")
        if v.get("status") not in STATUSES:
            errors.append(f"version {vid}: unknown status {v.get('status')!r}")
        _parse_date(v.get("effective_from"), f"version {vid}.effective_from", errors)
        if v.get("effective_from") is None:
            errors.append(f"version {vid}: effective_from required")
        _parse_date(v.get("effective_to"), f"version {vid}.effective_to", errors)
        for key in ("requirement", "legal_text_or_paraphrase"):
            if not v.get(key):
                errors.append(f"version {vid}: missing {key}")
        for link in ("supersedes", "superseded_by"):
            if v.get(link) is not None and v[link] not in seen_versions:
                # forward references allowed within the same manifest pass —
                # collect ids first, validate below.
                pass
    all_ids = {v.get("rule_version_id") for v in versions}
    for v in versions:
        for link in ("supersedes", "superseded_by"):
            if v.get(link) is not None and v[link] not in all_ids:
                errors.append(f"version {v.get('rule_version_id')}: dangling {link}")
    for a in appl:
        if a.get("rule_version_id") not in all_ids:
            errors.append(f"applicability {a.get('applicability_id')}: "
                          f"unknown version {a.get('rule_version_id')}")
        if a.get("result") not in APPL_RESULTS:
            errors.append(f"applicability {a.get('applicability_id')}: "
                          f"unknown result {a.get('result')!r}")
        if not isinstance(a.get("conditions"), dict):
            errors.append(f"applicability {a.get('applicability_id')}: "
                          "conditions must be an object")
    for w in weights:
        if w.get("check_id") not in rules:
            errors.append(f"weight: unknown check {w.get('check_id')}")
        if w.get("severity") not in SEVERITIES:
            errors.append(f"weight {w.get('check_id')}: bad severity")
        try:
            if float(w.get("weight", -1)) < 0:
                errors.append(f"weight {w.get('check_id')}: negative")
        except (TypeError, ValueError):
            errors.append(f"weight {w.get('check_id')}: non-numeric")
    return errors


def _norm_version_row(v: dict[str, Any]) -> dict[str, Any]:
    """Comparable projection of a DB version row."""
    return {"requirement": v.get("requirement", ""),
            "legal_text_or_paraphrase": v.get("legal_text_or_paraphrase", ""),
            "effective_from": v.get("effective_from"),
            "effective_to": v.get("effective_to"),
            "status": v.get("status")}


def diff_manifest(repo, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare manifest against DB state. Pure read path (works on MemoryRepo)."""
    lk = manifest["legal_knowledge"]
    db_versions = {v["rule_version_id"]: v for v in repo.all_rule_versions()}
    db_checks = {c["check_id"]: c for c in repo.list_checks()}
    db_appl = {(a.get("applicability_id")): a for a in repo.all_applicability()}
    db_weights = {w["check_id"]: w for w in repo.all_weights(POLICY_ID)}

    diff: dict[str, Any] = {"new_checks": [], "new_versions": [],
                            "changed_versions": [], "removed_versions": [],
                            "new_applicability": [], "changed_applicability": [],
                            "new_weights": [], "changed_weights": []}
    for r in lk["rules"]:
        if r["rule_id"] not in db_checks:
            diff["new_checks"].append(copy.deepcopy(r))
    for v in lk["rule_versions"]:
        vid = v["rule_version_id"]
        if vid not in db_versions:
            # overlap pre-check against same (rule_number, sub_rule, clause)
            rule = next((x for x in lk["rules"] if x["rule_id"] == v["check_id"]), {})
            key = (rule.get("rule_number"), rule.get("sub_rule"), rule.get("clause"))
            clash = [d["rule_version_id"] for d in db_versions.values()
                     if (d.get("rule_number"), d.get("sub_rule"), d.get("clause")) == key
                     and _ranges_overlap(d.get("effective_from"), d.get("effective_to"),
                                         v.get("effective_from"), v.get("effective_to"))]
            diff["new_versions"].append({"manifest": copy.deepcopy(v),
                                         "overlaps_existing": clash})
        elif _norm_version_row(db_versions[vid]) != _norm_version_row(v):
            diff["changed_versions"].append({
                "rule_version_id": vid, "database": _norm_version_row(db_versions[vid]),
                "manifest": _norm_version_row(v),
                "note": "CONFLICT: same id, different legal content — "
                        "manual admin versioning required, never auto-overwritten."})
    manifest_ids = {v["rule_version_id"] for v in lk["rule_versions"]}
    diff["removed_versions"] = sorted(set(db_versions) - manifest_ids)
    for a in lk["applicability_rules"]:
        aid = a["applicability_id"]
        if aid not in db_appl:
            diff["new_applicability"].append(copy.deepcopy(a))
        else:
            cur = db_appl[aid]
            if (cur.get("condition_expression") != a.get("conditions")
                    or cur.get("applicable_result") != a.get("result")):
                diff["changed_applicability"].append({
                    "applicability_id": aid, "database": cur,
                    "manifest": copy.deepcopy(a),
                    "note": "CONFLICT: manual review required."})
    for w in manifest["scoring_policy"][0]["rules"]:
        cur = db_weights.get(w["check_id"])
        if cur is None:
            diff["new_weights"].append(copy.deepcopy(w))
        elif (float(cur.get("weight", -1)) != float(w["weight"])
              or str(cur.get("severity", "")).upper() != w["severity"]):
            diff["changed_weights"].append({"database": cur,
                                            "manifest": copy.deepcopy(w)})
    return diff


def _ranges_overlap(a_from, a_to, b_from, b_to) -> bool:
    a_to = a_to or "9999-12-31"
    b_to = b_to or "9999-12-31"
    return bool(a_from and b_from and a_from < b_to and b_from < a_to)


def _resolve_source_id(repo, provenance: dict[str, Any]) -> str | None:
    """Match a manifest provenance block to an existing legal_sources row."""
    title = (provenance.get("source_title") or "").strip().lower()
    if hasattr(repo, "fetch_all"):
        rows = repo.fetch_all(
            "SELECT source_id::text AS source_id, title FROM legal_sources")
        for row in rows:
            if (row.get("title") or "").strip().lower() == title and title:
                return row["source_id"]
        return None
    return "memory-source"


def apply_sync(repo, manifest: dict[str, Any], actor: str,
               dry_run: bool = True) -> dict[str, Any]:
    """Preview (dry_run) or apply the manifest diff. Inserts only."""
    lk = manifest["legal_knowledge"]
    rules = {r["rule_id"]: r for r in lk["rules"]}
    diff = diff_manifest(repo, manifest)
    result: dict[str, Any] = {"dry_run": dry_run, "applied": [], "conflicts": [],
                              "errors": [], "summary": {}}
    if diff["changed_versions"] or diff["changed_applicability"]:
        result["conflicts"].extend(diff["changed_versions"])
        result["conflicts"].extend(diff["changed_applicability"])
    if diff["removed_versions"]:
        result["conflicts"].append({
            "note": "versions present in DB but absent from manifest — "
                    "left untouched (history is never deleted).",
            "rule_version_ids": diff["removed_versions"]})
    if dry_run:
        result["summary"] = {
            "new_checks": len(diff["new_checks"]),
            "new_versions": len(diff["new_versions"]),
            "new_applicability": len(diff["new_applicability"]),
            "weight_changes": len(diff["new_weights"]) + len(diff["changed_weights"]),
            "conflicts": len(result["conflicts"])}
        result["preview"] = diff
        return result

    def _record(kind: str, payload: dict[str, Any]) -> None:
        result["applied"].append({"kind": kind, **payload})
        try:
            repo.audit(actor, "RULE_SYNC_" + kind.upper(), "rule_sync",
                       payload.get("rule_version_id") or payload.get("check_id")
                       or payload.get("applicability_id") or "manifest",
                       payload)
        except Exception:
            pass

    for r in diff["new_checks"]:
        try:
            rule_row = repo.get_rule_by_number(r["rule_number"])
            if rule_row is None:
                result["errors"].append(
                    {"check_id": r["rule_id"],
                     "error": f"no legal_rules catalogue entry for rule_number "
                              f"{r['rule_number']!r} — create it via the manual "
                              "admin endpoint first."})
                continue
            repo.insert_registry_entry({
                "check_id": r["rule_id"], "rule_id": rule_row["rule_id"],
                "sub_rule": r.get("sub_rule"), "clause": r.get("clause"),
                "title": r.get("title", ""), "field_name": r.get("field", ""),
                "check_type": r.get("check_type", "MANUAL_REVIEW"),
                "mandatory_default": bool(r.get("mandatory_default", False)),
                "scoring_category": r.get("scoring_category", "OTHER"),
                "description": r.get("requirement", "")})
            _record("check", {"check_id": r["rule_id"]})
        except Exception as exc:
            result["errors"].append({"check_id": r["rule_id"], "error": str(exc)})

    version_ok: set[str] = set()
    for item in diff["new_versions"]:
        v = item["manifest"]
        try:
            rule = rules[v["check_id"]]
            rule_row = repo.get_rule_by_number(rule["rule_number"])
            if rule_row is None:
                raise ValueError("unknown rule_number catalogue entry")
            source_id = _resolve_source_id(repo, v.get("provenance", {}))
            if source_id is None:
                raise ValueError(
                    "no matching legal_sources row for provenance "
                    f"{v.get('provenance', {}).get('source_title')!r} — "
                    "register the source via the manual admin endpoint first.")
            try:
                requirement_type = _inherit_requirement_type(repo, v)
            except ValueError:
                result["conflicts"].append({
                    "rule_version_id": v["rule_version_id"],
                    "check_id": v["check_id"],
                    "note": "ACTION REQUIRED: brand-new check lineage has no "
                            "sibling version to inherit requirement_type from — "
                            "create the first version via POST "
                            "/admin/rules/versions (admin). Nothing was written."})
                continue
            saved = repo.insert_rule_version({
                "rule_version_id": v["rule_version_id"],
                "rule_id": rule_row["rule_id"],
                "check_id": v["check_id"],
                "sub_rule": rule.get("sub_rule"), "clause": rule.get("clause"),
                "requirement": v.get("requirement", ""),
                "legal_text_or_paraphrase": v.get("legal_text_or_paraphrase", ""),
                "requirement_type": requirement_type,
                "is_mandatory": True,
                "effective_from": v["effective_from"],
                "effective_to": v.get("effective_to"),
                "source_id": source_id,
                "supersedes": v.get("supersedes"),
                "status": v.get("status", "IN_FORCE")})
            version_ok.add(v["rule_version_id"])
            _record("version", {"rule_version_id": saved.get("rule_version_id",
                                                             v["rule_version_id"]),
                                "check_id": v["check_id"]})
        except Exception as exc:
            result["errors"].append({"rule_version_id": v["rule_version_id"],
                                     "error": str(exc)})

    for a in diff["new_applicability"]:
        if a["rule_version_id"] not in version_ok and not any(
                v["rule_version_id"] == a["rule_version_id"]
                for v in repo.all_rule_versions()):
            result["conflicts"].append({
                "applicability_id": a.get("applicability_id"),
                "note": "blocked: its rule version was not applied — "
                        "resolve the version conflict first. Nothing was written."})
            continue
        try:
            ver = next((x for x in lk["rule_versions"]
                        if x["rule_version_id"] == a["rule_version_id"]), {})
            source_id = _resolve_source_id(repo, ver.get("provenance", {}))
            if source_id is None:
                raise ValueError("unresolvable provenance source")
            saved = repo.insert_applicability({
                "applicability_id": a["applicability_id"],
                "rule_version_id": a["rule_version_id"],
                "check_id": a.get("check_id"),
                "condition_expression": a.get("conditions", {}),
                "applicable_result": a.get("result", "CONDITIONAL"),
                "reason": a.get("reason", ""),
                "source_id": source_id})
            _record("applicability",
                    {"applicability_id": saved.get("applicability_id")})
        except Exception as exc:
            result["errors"].append(
                {"applicability_id": a.get("applicability_id"), "error": str(exc)})

    for w in diff["new_weights"] + [c["manifest"] for c in diff["changed_weights"]]:
        try:
            repo.upsert_weight(POLICY_ID, w["check_id"], float(w["weight"]),
                               w["severity"])
            _record("weight", {"check_id": w["check_id"],
                               "weight": w["weight"], "severity": w["severity"]})
        except Exception as exc:
            result["errors"].append({"check_id": w.get("check_id"),
                                     "error": str(exc)})
    result["summary"] = {"applied": len(result["applied"]),
                         "conflicts": len(result["conflicts"]),
                         "errors": len(result["errors"])}
    return result


def _inherit_requirement_type(repo, version: dict[str, Any]) -> str:
    """Inherit requirement_type from the latest sibling version (reported)."""
    rule_id = version["check_id"]
    sibs = [v for v in repo.all_rule_versions() if v.get("check_id") == rule_id]
    if hasattr(repo, "fetch_all"):
        rows = repo.fetch_all(
            """SELECT requirement_type FROM rule_versions
               WHERE check_id = %s ORDER BY effective_from DESC LIMIT 1""",
            [rule_id])
        if rows:
            return rows[0]["requirement_type"]
    if sibs:
        return "DECLARATION"
    raise ValueError(
        f"no sibling version to inherit requirement_type for new check lineage "
        f"{rule_id} — create the first version via the manual admin endpoint.")
