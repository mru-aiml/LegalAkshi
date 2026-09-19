"""Hybrid ingredient OCR: RapidOCR primary, Tesseract targeted fallback.

Flow: RapidOCR runs the existing first-class ingredient pipeline first.
Only when its result is weak (see TESS_FALLBACK_CONFIG) does the service
run ONE Tesseract call on the same ingredient crop, then deterministically
reconcile the two readings.

Hard boundaries (never relaxed):
- Tesseract never runs on full frames, every image, every crop,
  nutrition, MRP, or consumer-care. Ingredient crops only.
- Nothing here decides compliance. Uncertain merges surface as
  NEEDS_REVIEW via the existing food-layer detection; the rule engine's
  confidence/review mechanism is untouched.
- Reconciliation never invents text: every emitted token comes verbatim
  from one of the two engine readings (modulo joining punctuation).
"""
from __future__ import annotations

import re
from typing import Any

# Centralised fallback tuning. All gates read these values; tests may
# override per-call via the ``config`` parameter.
TESS_FALLBACK_CONFIG: dict[str, Any] = {
    "enabled": True,
    "coherence_floor": 0.70,   # gate A: rapid coherence below this
    "min_text_chars": 40,       # gate B: rapid text shorter than this...
    "min_region_rows": 3,       # ...while the region suggests >= this many
    "psm": 6,                   # production fallback: single uniform-block call
}

# Gate C: characters that never belong in an ingredient declaration once
# legitimate uses are stripped (digit+% percentages, digit-&-digit lists).
_SUSPICIOUS_RE = re.compile(r"[@#$^*_~`|\\&¬]")

# Gate D: INS/E-number shapes that are malformed or inconsistent.
# The first pattern fires when INS is NOT followed by a clean 1-4 digit
# number, catching confusions like "INS 5O1" (digit/letter mix) as well
# as letters in the number slot.
_MALFORMED_INS_RES = (
    re.compile(r"\bINS\b(?![\s]*\d{1,4}\b)"),      # no clean INS number
    re.compile(r"\bE\s*[A-Za-z]+\b"),              # E-number with letters
    re.compile(r"\bE\d{1,2}\b"),                   # too few digits for E-number
    re.compile(r"\bE\d{5,}\b"),                    # too many digits
)

# Well-formed additive tokens: the only forms reconciliation may prefer.
_VALID_INS_RE = re.compile(r"^INS\s*\d{1,4}(\([ivxlcdm]+\))?$", re.I)
_VALID_E_RE = re.compile(r"^E\d{3,4}$", re.I)


def _strip_legitimate_punct(text: str) -> str:
    """Remove the legitimate uses of % and & before suspicious-char scan."""
    text = re.sub(r"\d\s*%", "", text)          # "68%" percentages
    text = re.sub(r"\d\s*&\s*\d", "", text)     # "500 & 501" lists
    return text


def _ins_like_tokens(text: str) -> list[str]:
    """Candidate additive tokens (well-formed or malformed) in a segment."""
    from app.services.ocr.food import _E_TOKEN, _INS_TOKEN

    found: list[str] = []
    for rx in (_INS_TOKEN, _E_TOKEN):
        for m in rx.finditer(text or ""):
            found.append(m.group(0))
    for m in re.finditer(r"\bINS\s*\S+", text or "", re.I):
        cand = m.group(0).rstrip(",;.")
        if cand not in found:
            found.append(cand)
    return found


def _is_valid_additive(token: str) -> bool:
    token = token.strip().rstrip(",;.")
    return bool(_VALID_INS_RE.match(token) or _VALID_E_RE.match(token))


def needs_tesseract_fallback(*, rapid_text: str | None,
                             rapid_coherence: dict[str, Any] | None = None,
                             region_height_px: float | None = None,
                             line_height_px: float | None = None,
                             variant_texts: list[tuple[str, str, float]]
                             | None = None,
                             config: dict[str, Any] | None = None
                             ) -> tuple[bool, list[str]]:
    """Decide whether the Tesseract ingredient fallback must run.

    Returns (fire, reasons). Pure function of its inputs — deterministic,
    no I/O, no package-specific strings. Every reason names the evidence.
    """
    cfg = config or TESS_FALLBACK_CONFIG
    if not cfg.get("enabled", True):
        return False, ["fallback disabled by configuration"]
    text = (rapid_text or "").strip()
    reasons: list[str] = []
    score = 0.0
    if isinstance(rapid_coherence, dict):
        try:
            score = float(rapid_coherence.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
    # Gate A — low coherence.
    if score < float(cfg.get("coherence_floor", 0.70)):
        reasons.append(f"rapid coherence {score:.3f} below "
                       f"{float(cfg.get('coherence_floor', 0.70)):.2f}")
    # Gate B — incomplete relative to the detected region. Without
    # region geometry there is no baseline for "too short" (a 12-char
    # "Salt, Sugar" can be a complete declaration), so the gate stays
    # silent rather than guessing.
    rows = None
    if region_height_px and line_height_px and line_height_px > 0:
        rows = region_height_px / line_height_px
    if (rows is not None and rows >= float(cfg.get("min_region_rows", 3))
            and len(text) < int(cfg.get("min_text_chars", 40))):
        reasons.append(f"rapid text too short ({len(text)} chars for a "
                       f"region suggesting ~{rows:.1f} rows)")
    # Gate C — suspicious characters/tokens.
    scrubbed = _strip_legitimate_punct(text)
    bad = sorted(set(_SUSPICIOUS_RE.findall(scrubbed)))
    if bad:
        reasons.append("suspicious characters: "
                       + ", ".join(repr(b) for b in bad[:4]))
    # Gate D — malformed INS/E-number shapes.
    malformed = sorted({m.group(0) for rx in _MALFORMED_INS_RES
                        for m in rx.finditer(text)} - {""})
    if malformed:
        reasons.append("malformed additive codes: "
                       + ", ".join(malformed[:4]))
    # Gate E — RapidOCR variants disagree on important tokens.
    if variant_texts and len(variant_texts) >= 2:
        token_sets = []
        for _name, vtext, _coh in variant_texts:
            toks = set(_ins_like_tokens(vtext))
            toks.update(f"pct:{m.group(0)}" for m in
                        re.finditer(r"\d+(?:\.\d+)?\s*%", vtext or ""))
            token_sets.append(toks)
        union = set().union(*token_sets)
        differing = sorted(t for t in union
                           if any(t not in s for s in token_sets))
        if differing:
            reasons.append("rapid variants disagree on: "
                           + ", ".join(differing[:4]))
    return (bool(reasons), reasons)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def reconcile_ingredient_texts(rapid_text: str | None,
                               tess_text: str | None, *,
                               rapid_conf: float | None = None,
                               tess_conf: float | None = None
                               ) -> dict[str, Any]:
    """Deterministically merge two readings of the same ingredient crop.

    Priority, in order: preserve ordering, percentages, exactly-recognised
    INS/E codes and parentheses; never invent. Any genuine two-sided
    disagreement is flagged uncertain (caller routes to NEEDS_REVIEW);
    single-source wins and full agreement are not.

    Tiebreaks, weakest-first: (1) well-formed additive token on exactly
    one side wins on pattern evidence; (2) otherwise the higher-confidence
    reading wins (margin 0.05) — never silently swapping toward weaker
    evidence; (3) exact ties keep RapidOCR (primary).

    Returns {text, ocr_source, uncertain, uncertain_spans, notes} where
    ocr_source is one of "rapidocr" | "tesseract" | "hybrid".
    """
    from app.services.ocr.food import _split_top_level

    notes: list[str] = []
    rtext, ttext = (rapid_text or "").strip(), (tess_text or "").strip()
    if not rtext and not ttext:
        return {"text": "", "ocr_source": "rapidocr", "uncertain": True,
                "uncertain_spans": [],
                "notes": ["both engines returned no text"]}
    if not rtext:
        return {"text": ttext, "ocr_source": "tesseract", "uncertain": False,
                "uncertain_spans": [],
                "notes": ["rapid produced nothing usable; tesseract kept"]}
    if not ttext:
        return {"text": rtext, "ocr_source": "rapidocr", "uncertain": False,
                "uncertain_spans": [],
                "notes": ["tesseract produced nothing usable; rapid kept"]}
    if _norm(rtext) == _norm(ttext):
        return {"text": rtext, "ocr_source": "hybrid", "uncertain": False,
                "uncertain_spans": [],
                "notes": ["both engines agree; rapid text kept"]}
    def _balanced(text: str) -> bool:
        return text.count("(") == text.count(")")
    # Comparable segmentation: paren-aware splitting on one side and
    # plain comma splitting on the other produce structurally different
    # segment lists that opcode alignment cannot pair (duplicating the
    # additive segment in the merge). When balance differs, split both
    # sides plainly so the same content aligns.
    _plain = lambda text: [s.strip(" ,;") for s in re.split(r"[;,]", text)
                           if s.strip(" ,;")]
    if _balanced(rtext) != _balanced(ttext):
        rsegs = _plain(rtext)
        tsegs = _plain(ttext)
        notes.append("paren balance differs between readings; aligned on "
                     "plain comma splits")
    else:
        rsegs = _split_top_level(rtext)
        tsegs = _split_top_level(ttext)
    try:
        r_mean = float(rapid_conf) if rapid_conf is not None else None
    except (TypeError, ValueError):
        r_mean = None
    try:
        t_mean = float(tess_conf) if tess_conf is not None else None
    except (TypeError, ValueError):
        t_mean = None

    def _resolve_pair(rseg: str, tseg: str, label: str) -> None:
        """Reconcile one aligned segment pair (appends to merged)."""
        nonlocal used_tess
        if _norm(rseg) == _norm(tseg):
            merged.append(rseg)
            return
        r_toks = _ins_like_tokens(rseg)
        t_toks = _ins_like_tokens(tseg)
        r_valid = [t for t in r_toks if _is_valid_additive(t)]
        t_valid = [t for t in t_toks if _is_valid_additive(t)]
        # (1) Validity evidence: well-formed additive on exactly one side.
        if t_valid and not r_valid:
            merged.append(tseg)
            used_tess = True
            notes.append(f"segment {label}: tesseract additive form kept "
                         f"({t_valid[0]})")
        elif r_valid and not t_valid:
            merged.append(rseg)
            notes.append(f"segment {label}: rapid additive form kept "
                         f"({r_valid[0]})")
        # (2) Confidence evidence (margin 0.05): change only toward the
        # stronger reading — e.g. tess "(INS 500(ii))" over rapid
        # "(INS 500(i))" when tess read the crop more confidently.
        elif (t_mean is not None and r_mean is not None
                and t_mean > r_mean + 0.05):
            merged.append(tseg)
            used_tess = True
            notes.append(f"segment {label}: tesseract reading kept on "
                         f"higher confidence ({t_mean:.2f} vs {r_mean:.2f})")
        elif (r_mean is not None and t_mean is not None
                and r_mean > t_mean + 0.05):
            merged.append(rseg)
            notes.append(f"segment {label}: rapid reading kept on higher "
                         f"confidence ({r_mean:.2f} vs {t_mean:.2f})")
        # (3) Exact tie / unknown confidence: primary wins, flagged.
        else:
            merged.append(rseg)
            uncertain_spans.append(rseg[:60])
            notes.append(f"segment {label}: engines disagree with no "
                         f"decisive evidence; rapid kept + flagged uncertain")

    import difflib as _difflib
    from collections import Counter as _Counter

    def _pair_blocks(rblock: list[str], tblock: list[str]
                     ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        """Greedily pair segments across a replace block by similarity.

        Returns (pairs, rapid_only_idx, tess_only_idx). Threshold 0.5:
        below it two segments are different content, not two readings of
        the same content. Deterministic (ties break by index order).
        """
        scored = []
        for ri, rseg in enumerate(rblock):
            for tj, tseg in enumerate(tblock):
                ratio = _difflib.SequenceMatcher(
                    None, _norm(rseg), _norm(tseg), autojunk=False).ratio()
                scored.append((ratio, ri, tj))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        used_r, used_t, pairs = set(), set(), []
        for ratio, ri, tj in scored:
            if ratio < 0.5 or ri in used_r or tj in used_t:
                continue
            used_r.add(ri)
            used_t.add(tj)
            pairs.append((ri, tj))
        pairs.sort()
        return (pairs,
                [ri for ri in range(len(rblock)) if ri not in used_r],
                [tj for tj in range(len(tblock)) if tj not in used_t])

    merged: list[str] = []
    uncertain_spans: list[str] = []
    used_tess = False
    # Pure permutation: same items, different order. Keep the primary
    # order (descending-weight order is legally significant) without
    # duplicating anything, flagged uncertain.
    if (len(rsegs) == len(tsegs)
            and _Counter(_norm(s) for s in rsegs)
            == _Counter(_norm(s) for s in tsegs)):
        merged = list(rsegs)
        uncertain_spans = [s[:60] for s in rsegs]
        notes.append("same items in different order; rapid order kept + "
                     "flagged uncertain")
        text = ", ".join(s for s in merged).strip()
        return {"text": text, "ocr_source": "rapidocr", "uncertain": True,
                "uncertain_spans": uncertain_spans, "notes": notes}
    # Opcode alignment (not positional): when the engines split the same
    # paragraph differently, positional pairing duplicates segments. The
    # matcher aligns equal blocks first; only genuinely extra blocks are
    # appended once, flagged uncertain.
    matcher = _difflib.SequenceMatcher(
        None, [_norm(s) for s in rsegs], [_norm(s) for s in tsegs],
        autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(rsegs[i1:i2])
        elif tag == "replace":
            pairs, r_only, t_only = _pair_blocks(rsegs[i1:i2],
                                                 tsegs[j1:j2])
            # Emit in an order-preserving pass: rapid-origin slots keep
            # rapid order; tess-only slots slot in after the nearest
            # preceding aligned pair (segment order is legally
            # significant — descending-weight declarations).
            paired_ri = {ri for ri, _ in pairs}
            anchor_of = {}
            for ri, tj in pairs:
                anchor_of[tj] = ri
            slots: list[tuple[tuple[float, int], str, str]] = []
            for k, (ri, tj) in enumerate(pairs):
                before = len(merged)
                _resolve_pair(rsegs[i1 + ri], tsegs[j1 + tj],
                              f"{i1 + ri}/{j1 + tj}")
                slots.extend(((float(ri), 0), m, "pair")
                             for m in merged[before:])
                del merged[before:]
            for ri in r_only:
                slots.append(((float(ri), 0), rsegs[i1 + ri], "rapid"))
                uncertain_spans.append(rsegs[i1 + ri][:60])
                notes.append(f"segment {i1 + ri}: only in rapid; kept + "
                             f"flagged uncertain")
            for tj in t_only:
                prior = [anchor_of[x] for x in anchor_of if x < tj]
                anchor = max(prior) if prior else -1
                slots.append(((float(anchor) + 0.5, 1),
                              tsegs[j1 + tj], "tess"))
                used_tess = True
                uncertain_spans.append(tsegs[j1 + tj][:60])
                notes.append(f"segment {j1 + tj}: only in tesseract; kept "
                             f"at aligned position + flagged uncertain")
            slots.sort(key=lambda s: s[0])
            merged.extend(text for _, text, _ in slots)
        elif tag == "delete":
            for ri in range(i1, i2):
                merged.append(rsegs[ri])
                uncertain_spans.append(rsegs[ri][:60])
            notes.append(f"segments {i1}-{i2}: only in rapid; kept + "
                         f"flagged uncertain")
        else:  # insert
            for tj in range(j1, j2):
                merged.append(tsegs[tj])
                used_tess = True
                uncertain_spans.append(tsegs[tj][:60])
            notes.append(f"segments {j1}-{j2}: only in tesseract; kept + "
                         f"flagged uncertain")
    text = ", ".join(s for s in merged).strip()
    if ", ".join(rsegs) != rtext or ", ".join(tsegs) != ttext:
        notes.append("segments rejoined with ', ' (original separators "
                     "normalised)")
    # Genuine two-sided disagreement always stays reviewable, even when
    # the tiebreak picked a side: the officer confirms additive identity.
    uncertain = True
    if _norm(text) == _norm(rtext) and not used_tess \
            and not uncertain_spans:
        uncertain = False
    if _norm(text) == _norm(rtext):
        source = "rapidocr" if not used_tess else "hybrid"
    elif _norm(text) == _norm(ttext):
        source = "tesseract"
    else:
        source = "hybrid"
    return {"text": text, "ocr_source": source, "uncertain": uncertain,
            "uncertain_spans": uncertain_spans, "notes": notes}
