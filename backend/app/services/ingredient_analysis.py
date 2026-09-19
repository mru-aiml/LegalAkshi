"""Configuration-driven ingredient compliance analysis.

Rule-driven, never an LLM verdict: each extracted ingredient is looked up
in the authoritative ingredient registry
(backend/authoritative/ingredient_registry_v1.json). Possible statuses:

  ALLOWED | RESTRICTED | PROHIBITED | CONDITIONAL | UNKNOWN | NEEDS_REVIEW

An ingredient is PROHIBITED only when a registry entry explicitly says so.
Anything without a configured rule is UNKNOWN with reason
"No configured rule found" — never illegal. Additive-class declarations
(INS numbers, preservatives, colours, flavours, sweeteners, ...) are
NEEDS_REVIEW pending verification against the applicable food
additive/use-condition rule.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REGISTRY_PATH = (Path(__file__).resolve().parent.parent.parent
                 / "authoritative" / "ingredient_registry_v1.json")

_ADDITIVE_RE = re.compile(
    r"\bins\b|e\s?\d{3}|preservative|colour|color|flavour|flavor|"
    r"sweetener|emulsifier|stabiliz|antioxidant|acidity\s+regulator|"
    r"raising\s+agent|thickener|humectant", re.I)

_VALID = {"ALLOWED", "RESTRICTED", "PROHIBITED", "CONDITIONAL", "UNKNOWN",
          "NEEDS_REVIEW"}


def _edit_distance(a: str, b: str) -> int:
    """Tiny Levenshtein (no dependency) for OCR-variant normalisation."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _typo_match(norm: str, entries: dict[str, dict]) -> dict | None:
    """Map an OCR-mangled name to a configured entry — or nothing.

    Strictly gated: alphabetic, length >= 4, edit distance <= 2, and a
    UNIQUE best match. This normalises obvious variants ("Palm oii" ->
    "palm oil") at capped confidence; anything ambiguous stays UNKNOWN.
    The registry is never used to invent an ingredient.
    """
    if len(norm) < 4 or not re.fullmatch(r"[a-z][a-z .()%-]*", norm):
        return None
    scored = []
    for key, row in entries.items():
        if not key or abs(len(key) - len(norm)) > 2:
            continue
        dist = _edit_distance(norm, key)
        if dist <= 2:
            scored.append((dist, key, row))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous between two entries: do not guess
    return {"row": scored[0][2], "matched": scored[0][1],
            "distance": scored[0][0]}


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or REGISTRY_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = {}
    for row in data.get("entries", []):
        name = str(row.get("name", "")).strip().lower()
        if name:
            entries[name] = row
    return {"meta": {k: v for k, v in data.items() if k != "entries"},
            "entries": entries}


def classify_ingredient(name: str,
                        registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify one ingredient name against the configured registry."""
    registry = registry if registry is not None else load_registry()
    entries: dict[str, dict] = registry.get("entries", {})
    meta = registry.get("meta", {})
    norm = re.sub(r"\s+", " ", str(name or "")).strip().lower()
    if not norm:
        return {"ingredient": name, "status": "UNKNOWN",
                "reason": "No configured rule found.",
                "source": meta.get("registry_id", "ingredient-registry"),
                "version": meta.get("version"),
                "effective_date": meta.get("effective_date"),
                "confidence": None}
    # Exact match first, then configured-entry substring match.
    hit = entries.get(norm)
    if hit is None:
        for key, row in entries.items():
            if key and (key in norm or norm in key):
                hit = row
                break
    if hit is not None and str(hit.get("status", "")).upper() in _VALID:
        return {"ingredient": name,
                "status": str(hit["status"]).upper(),
                "reason": hit.get("reason", ""),
                "source": hit.get("source", ""),
                "version": hit.get("version"),
                "effective_date": hit.get("effective_date"),
                "confidence": 0.85}
    # OCR-variant normalisation: obvious manglings map to the configured
    # entry at capped confidence with the mapping disclosed; the verdict
    # stays the entry's own. Ambiguous or distant strings stay UNKNOWN.
    typo = _typo_match(norm, entries)
    if typo is not None and str(typo["row"].get("status", "")).upper() in _VALID:
        return {"ingredient": name,
                "status": str(typo["row"]["status"]).upper(),
                "reason": (f"Normalised OCR variant of configured "
                           f"'{typo['matched']}' (edit distance "
                           f"{typo['distance']}) — verify visually. "
                           f"{typo['row'].get('reason', '')}"),
                "source": typo["row"].get("source", ""),
                "version": typo["row"].get("version"),
                "effective_date": typo["row"].get("effective_date"),
                "normalised_from": typo["matched"],
                "confidence": 0.6}
    if _ADDITIVE_RE.search(norm):
        return {"ingredient": name, "status": "NEEDS_REVIEW",
                "reason": "Ingredient requires verification against the "
                          "applicable food additive/use-condition rule.",
                "source": "LegalAkshi ingredient registry v1 "
                          "(additive class rule)",
                "version": meta.get("version"),
                "effective_date": meta.get("effective_date"),
                "confidence": 0.7}
    return {"ingredient": name, "status": "UNKNOWN",
            "reason": "No configured rule found.",
            "source": meta.get("registry_id", "ingredient-registry"),
            "version": meta.get("version"),
            "effective_date": meta.get("effective_date"),
            "confidence": 0.5}


def analyze_ingredients(parsed_items: list[dict[str, Any]] | list[str],
                        registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analyse a parsed ingredient list; never invents a prohibition."""
    registry = registry if registry is not None else load_registry()
    results = []
    for item in parsed_items or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("ingredient")
        else:
            name = item
        row = classify_ingredient(str(name or ""), registry)
        if isinstance(item, dict):
            row["percentage"] = item.get("percentage")
            row["ins_number"] = item.get("ins_number")
            if item.get("category"):
                row["category"] = item.get("category")
            if item.get("raw_segment"):
                row["raw_segment"] = item.get("raw_segment")
        results.append(row)
    summary = {s: 0 for s in _VALID}
    for row in results:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"ingredients": results, "summary": summary,
            "registry_version": registry.get("meta", {}).get("version"),
            "note": "Automated ingredient screening only; potential flags "
                    "require inspector review against the applicable food "
                    "law. UNKNOWN means no configured rule was found — "
                    "not an offence."}
