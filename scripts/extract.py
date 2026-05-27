"""Extract country/region/city/landmark tags from travel text.

Pipeline:
  1. preprocess          (text already normalized by caller)
  2. extract_term_tags   (transport/hotel/meal/price/date layer)
  3. extract_candidates  (longest-match scan with alias_index + landmark_index)
  4. apply_disambig      (resolve ambiguous candidates via disambig_rules)
  5. apply_term_precheck (filter product/facility phrases before geo tagging)
  6. apply_role          (filter departure/transit/exclude/metaphor)
  7. apply_landmark      (landmark/airport -> city/country backfill)
  8. compress_tree       (dedup, build country->region->city->spot tree)
  9. emit                (JSON for downstream LLM review, MD for humans)

CLI:
  python extract.py extract --text "<text>"
  python extract.py extract --file path/to/dm.txt
  python extract.py test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

try:
    import ahocorasick as _ac
    _HAS_AC = True
except ImportError:
    _HAS_AC = False

sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_kb import KB, Landmark, RoleKeyword, load_kb  # noqa: E402

# ---------- audit logging (for v0.3 N-gram migration decision) ----------
# Every disambig rule hit/miss + every weak_signal trigger is appended to
# logs/disambig_audit.jsonl. Use scripts/audit_report.py to summarize.
_AUDIT_PATH = Path(__file__).resolve().parent.parent / "logs" / "disambig_audit.jsonl"
_AUDIT_ENABLED = True  # flip to False to silence logging


def _audit(event: str, **fields) -> None:
    if not _AUDIT_ENABLED:
        return
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — audit must never break extraction
        pass


@dataclass
class Candidate:
    text: str               # surface form found in source
    start: int              # char index
    end: int
    location_ids: list[str] = field(default_factory=list)  # possible location_ids
    landmark: Landmark | None = None
    source: str = "alias"   # alias | landmark | canonical
    role: str = "destination"
    role_reason: str = ""
    resolved_id: str | None = None
    confidence: float = 0.7
    disambig_reason: str = ""
    term_category: str = ""
    term_phrase: str = ""

    def to_dict(self) -> dict:
        d = {
            "text": self.text,
            "span": [self.start, self.end],
            "location_ids": self.location_ids,
            "source": self.source,
            "role": self.role,
            "role_reason": self.role_reason,
            "resolved_id": self.resolved_id,
            "confidence": round(self.confidence, 3),
            "disambig_reason": self.disambig_reason,
        }
        if self.term_category:
            d["term_category"] = self.term_category
            d["term_phrase"] = self.term_phrase or self.text
        if self.landmark:
            d["landmark_type"] = self.landmark.type
        return d


# ---------- Step 2: candidate extraction ----------

_ac_cache: dict[int, object] = {}


def _build_ac_automaton(kb: KB):
    """Build two Aho-Corasick automatons: one for CJK, one for non-CJK (lowercased)."""
    cjk_auto = _ac.Automaton()
    latin_auto = _ac.Automaton()

    terms: list[tuple[str, str, object]] = []
    for alias, als in kb.alias_index.items():
        if alias:
            terms.append((alias, "alias", [al.location_id for al in als]))
    for name, lm in kb.landmark_index.items():
        if name:
            terms.append((name, "landmark", lm))

    for term, kind, payload in terms:
        entry = (term, kind, payload)
        if _is_cjk(term):
            cjk_auto.add_word(term, entry)
        else:
            latin_auto.add_word(term.lower(), entry)

    if len(cjk_auto) > 0:
        cjk_auto.make_automaton()
    if len(latin_auto) > 0:
        latin_auto.make_automaton()
    return cjk_auto, latin_auto


def _make_candidate(term: str, kind: str, payload, pos: int) -> Candidate:
    end = pos + len(term)
    cand = Candidate(text=term, start=pos, end=end, source=kind)
    if kind == "alias":
        cand.location_ids = list(dict.fromkeys(payload))
    else:
        cand.landmark = payload
        cand.location_ids = [payload.location_id]
    return cand


def extract_candidates(text: str, kb: KB) -> list[Candidate]:
    """Scan aliases + landmarks. Overlapping matches are KEPT (dedup happens after disambig)."""

    if _HAS_AC:
        return _extract_candidates_ac(text, kb)
    return _extract_candidates_linear(text, kb)


def _extract_candidates_ac(text: str, kb: KB) -> list[Candidate]:
    """Aho-Corasick multi-pattern scan — O(n + matches)."""
    kb_id = id(kb)
    if kb_id not in _ac_cache:
        _ac_cache[kb_id] = _build_ac_automaton(kb)
    cjk_auto, latin_auto = _ac_cache[kb_id]

    found: list[Candidate] = []

    if len(cjk_auto) > 0:
        for end_idx, (term, kind, payload) in cjk_auto.iter(text):
            pos = end_idx - len(term) + 1
            found.append(_make_candidate(term, kind, payload, pos))

    if len(latin_auto) > 0:
        lower_text = text.lower()
        for end_idx, (term, kind, payload) in latin_auto.iter(lower_text):
            pos = end_idx - len(term) + 1
            end = pos + len(term)
            left_ok = pos == 0 or not _is_word_char(text[pos - 1])
            right_ok = end == len(text) or not _is_word_char(text[end])
            if left_ok and right_ok:
                found.append(_make_candidate(term, kind, payload, pos))

    found.sort(key=lambda c: (c.start, -(c.end - c.start)))
    return found


def _extract_candidates_linear(text: str, kb: KB) -> list[Candidate]:
    """Fallback linear scan when pyahocorasick is not installed."""
    all_terms: list[tuple[str, str, object]] = []
    for alias, als in kb.alias_index.items():
        if alias:
            all_terms.append((alias, "alias", [al.location_id for al in als]))
    for name, lm in kb.landmark_index.items():
        if name:
            all_terms.append((name, "landmark", lm))

    found: list[Candidate] = []
    for term, kind, payload in all_terms:
        lower_text = text.lower() if not _is_cjk(term) else text
        search_term = term.lower() if not _is_cjk(term) else term
        start = 0
        while True:
            pos = lower_text.find(search_term, start)
            if pos < 0:
                break
            end = pos + len(term)
            if not _is_cjk(term):
                left_ok = pos == 0 or not _is_word_char(text[pos - 1])
                right_ok = end == len(text) or not _is_word_char(text[end])
                if not (left_ok and right_ok):
                    start = pos + 1
                    continue
            found.append(_make_candidate(term, kind, payload, pos))
            start = pos + 1
    found.sort(key=lambda c: (c.start, -(c.end - c.start)))
    return found


def dedup_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """After disambig, resolve overlapping candidates at each character.

    Rule per char position:
      1. role != 'exclude' beats role == 'exclude' (SKIPped candidates lose to live ones)
      2. has resolved_id beats none
      3. longer span beats shorter
      4. earlier start beats later (stable)

    The winner per position is the candidate whose span covers it AND ranks highest.
    A candidate is kept iff it wins at least one of its own positions.
    """
    if not candidates:
        return []

    def rank_key(c: Candidate) -> tuple:
        return (
            -(c.end - c.start),                         # 1. longer span wins (so 威尼斯 covers 尼斯)
            1 if c.role == "exclude" else 0,            # 2. among same length: exclude wins last
            0 if c.resolved_id else 1,                  # 3. resolved beats unresolved
            c.start,                                    # 4. stable
        )

    sorted_cands = sorted(enumerate(candidates), key=lambda x: rank_key(x[1]))
    keep_ids: set[int] = set()
    pos_taken: dict[int, int] = {}  # char_pos -> kept candidate index
    for idx, cand in sorted_cands:
        # see if this candidate can claim ANY position not yet taken by a higher-ranked one
        claimed_any = False
        for p in range(cand.start, cand.end):
            if p not in pos_taken:
                pos_taken[p] = idx
                claimed_any = True
        if claimed_any:
            keep_ids.add(idx)
    # preserve original order
    return [candidates[i] for i in sorted(keep_ids)]


# CJK detection — covers Basic (一-鿿) + Extension A (㐀-䶿) + Hangul + Kana
_CJK_RANGES = [
    ("㐀", "䶿"),   # CJK Unified Ideographs Extension A
    ("一", "鿿"),   # CJK Unified Ideographs (basic)
    ("぀", "ヿ"),   # Hiragana + Katakana
    ("가", "힯"),   # Hangul Syllables
]


def _is_cjk(s: str) -> bool:
    return any(any(lo <= ch <= hi for lo, hi in _CJK_RANGES) for ch in s)


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


# ---------- Step 3: disambiguation ----------

def apply_disambig(text: str, candidates: list[Candidate], kb: KB) -> None:
    for cand in candidates:
        # find rules keyed by the surface text first, then by each candidate id's display name
        rules = list(kb.rule_index.get(cand.text, []))
        rules.sort(key=lambda r: -r.priority)

        chosen_id: str | None = None
        reason = ""
        skip = False
        rule_attempted = bool(rules)
        rule_matched = False

        for rule in rules:
            window = rule.window_int
            if window is None:
                left = 0
                right = len(text)
            else:
                left = max(0, cand.start - window)
                right = min(len(text), cand.end + window)
            context = text[left:cand.start] + " " + text[cand.end:right]
            if rule.context_regex == ".*" or re.search(rule.context_regex, context):
                rule_matched = True
                _audit("rule_hit",
                       term=rule.ambiguous_term,
                       context_regex=rule.context_regex,
                       resolution=rule.resolution,
                       cand_text=cand.text,
                       priority=rule.priority,
                       text_snippet=text[max(0, cand.start - 20):min(len(text), cand.end + 20)])
                if rule.resolution == "SKIP":
                    skip = True
                    reason = rule.reason or "rule skip"
                    break
                if rule.resolution in kb.locations:
                    chosen_id = rule.resolution
                    reason = rule.reason or f"matched rule -> {rule.resolution}"
                    break

        if skip:
            cand.resolved_id = None
            cand.role = "exclude"
            cand.role_reason = reason
            cand.disambig_reason = reason
            continue

        if chosen_id:
            cand.resolved_id = chosen_id
            cand.confidence = max(cand.confidence, 0.85)
            cand.disambig_reason = reason
            continue

        # no rule fired
        if rule_attempted and not rule_matched:
            _audit("rule_miss",
                   term=cand.text,
                   tried=len(rules),
                   location_ids=cand.location_ids,
                   text_snippet=text[max(0, cand.start - 20):min(len(text), cand.end + 20)])
        if len(cand.location_ids) == 1:
            cand.resolved_id = cand.location_ids[0]
            cand.confidence = max(cand.confidence, 0.8)
        elif len(cand.location_ids) > 1:
            # pick the first; mark needs_review (caller may upgrade with LLM)
            cand.resolved_id = cand.location_ids[0]
            cand.confidence = 0.5
            cand.disambig_reason = f"ambiguous: {cand.location_ids}"
            _audit("ambig_unresolved",
                   term=cand.text,
                   location_ids=cand.location_ids,
                   chose=cand.resolved_id,
                   text_snippet=text[max(0, cand.start - 20):min(len(text), cand.end + 20)])


# ---------- Step 4: term tags + role detection ----------

_CLAUSE_BREAK_RE = re.compile(r"[，。,.；;！？!?\n]")

_TERM_SUFFIX_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("transport", re.compile(r"^(?:小?火車|鐵路|纜車|巴士|客運|遊船|郵輪|渡輪|航班|班機|新幹線|JR|路線)", re.IGNORECASE)),
    ("hotel", re.compile(r"^(?:飯店|酒店|旅館|旅店|民宿|溫泉旅館|住宿|resort|hotel)", re.IGNORECASE)),
    ("meal", re.compile(r"^(?:早餐|午餐|晚餐|餐|餐廳|料理|美食|饗宴)", re.IGNORECASE)),
]

_TERM_LITERAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("date", re.compile(r"(?:\d{1,2}[/-]\d{1,2}|\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d+月(?:出發)?|暑假|連假|出發日)", re.IGNORECASE)),
    ("price", re.compile(r"(?:(?:NT\$|TWD|USD|\$|每人|訂金|尾款)\s*[\d,]+(?:\s*(?:起|含稅|未稅|元|NTD|TWD|USD))?|[\d,]+\s*(?:起|含稅|未稅|元|NTD|TWD|USD))", re.IGNORECASE)),
]

_STRONG_GEO_CONTEXT_RE = re.compile(
    r"(?:日本|韓國|泰國|越南|台灣|中國|香港|澳門|歐洲|北海道|九州|關西|關東|岐阜|飛驒|"
    r"前往|抵達|遊覽|造訪|入住|夜宿|飛往|搭機飛|行程|第[一二三四五六七八九十\d]+天|D\d+)",
    re.IGNORECASE,
)


def _append_term_tag(tags: list[dict], seen: set, *, phrase: str, category: str,
                     start: int | None = None, end: int | None = None,
                     confidence: float = 0.8, evidence: str = "",
                     caution: str = "") -> None:
    span = [start, end] if start is not None and end is not None else None
    key = (phrase, category, start, end)
    if key in seen:
        return
    seen.add(key)
    tag = {
        "phrase": phrase,
        "category": category,
        "confidence": round(confidence, 3),
    }
    if span:
        tag["span"] = span
    if evidence:
        tag["evidence"] = evidence
    if caution:
        tag["caution"] = caution
    tags.append(tag)


def extract_term_tags(text: str) -> tuple[list[dict], set]:
    """Extract non-geo travel terms that are still useful tags."""
    tags: list[dict] = []
    seen: set = set()
    for category, rx in _TERM_LITERAL_PATTERNS:
        for m in rx.finditer(text):
            phrase = m.group(0).strip()
            if not phrase:
                continue
            _append_term_tag(
                tags, seen,
                phrase=phrase,
                category=category,
                start=m.start(),
                end=m.end(),
                confidence=0.85,
                evidence=phrase,
                caution="supports itinerary/product metadata, not destination by itself",
            )
    return tags, seen


def apply_term_precheck(text: str, candidates: list[Candidate],
                        term_tags: list[dict], seen: set) -> None:
    """Reject place-looking tokens that are actually product/facility phrases.

    This folds the useful travel rules from industry-term-composer into the
    extractor itself: a token should not become a geo tag just because it looks
    like a place when the immediate phrase says transport, hotel, meal, price,
    or date. Strong geo context keeps the candidate alive for later role rules.
    """
    for cand in candidates:
        was_excluded = cand.role == "exclude"
        if cand.landmark and cand.landmark.type in {"airport", "station"}:
            continue

        left_ctx, right_ctx = _clause_around(text, cand.start, cand.end, 18)
        phrase_ctx = f"{left_ctx}{cand.text}{right_ctx}"
        has_strong_geo_context = bool(_STRONG_GEO_CONTEXT_RE.search(phrase_ctx))

        for category, rx in _TERM_SUFFIX_PATTERNS:
            match = rx.search(right_ctx)
            if match:
                phrase = f"{cand.text}{match.group(0)}"
                cand.term_category = category
                cand.term_phrase = phrase
                _append_term_tag(
                    term_tags, seen,
                    phrase=phrase,
                    category=category,
                    start=cand.start,
                    end=cand.end + match.end(),
                    confidence=0.85,
                    evidence=phrase,
                    caution="non-geo phrase unless strong destination context appears",
                )
                if not was_excluded and not has_strong_geo_context:
                    cand.role = "exclude"
                    cand.role_reason = f"term_precheck: {category} phrase"
                    cand.disambig_reason = cand.disambig_reason or "non-destination term phrase"
                    cand.confidence = min(cand.confidence, 0.2)
                break
        if cand.role == "exclude":
            continue

        for category, rx in _TERM_LITERAL_PATTERNS:
            if rx.search(phrase_ctx):
                cand.confidence = min(cand.confidence, 0.55)
                cand.disambig_reason = cand.disambig_reason or f"weak geo evidence near {category} phrase"
                break


def _clause_around(text: str, start: int, end: int, window: int) -> tuple[str, str]:
    """Return left/right context strings within the same clause and within `window` chars.

    Stops the left/right window at the nearest clause-break punctuation, so a keyword
    in a different sentence won't bleed into this candidate's context.
    """
    left_raw = text[max(0, start - window):start]
    right_raw = text[end:min(len(text), end + window)]
    parts_l = _CLAUSE_BREAK_RE.split(left_raw)
    parts_r = _CLAUSE_BREAK_RE.split(right_raw)
    return parts_l[-1] if parts_l else "", parts_r[0] if parts_r else ""


_FILTER_ROLES = {"departure", "transit", "exclude", "metaphor", "return"}

# minimum chars by which a filter role must beat destination to override it.
# prevents "東京自由活動，集合於飯店" from flipping 東京 to departure when there's no
# explicit destination keyword nearby — destination_seen=False means we fall back to
# the airport-style override path only.
_FILTER_OVER_DEST_MARGIN = 1


def apply_role(text: str, candidates: list[Candidate], kb: KB) -> None:
    """Closest-keyword-wins role assignment with directional + context guards.

    For each candidate:
      1. Find every role keyword that fires in-clause AND matches its direction
         AND (if `requires_context` set) the surrounding clause also satisfies it.
      2. Score each fired keyword by ABSOLUTE distance from candidate boundary
         (cand.start - keyword_end_in_text, in *original text* indices).
      3. Closest wins, but a filter role (departure/transit/exclude/metaphor/return)
         must beat the closest destination keyword by at least _FILTER_OVER_DEST_MARGIN
         chars to override destination. Otherwise destination is kept.
      4. Airports without any destination keyword in the clause default to transit.
    """
    for cand in candidates:
        if cand.role == "exclude":
            continue

        best_filter: tuple[int, str, str, str] | None = None     # (dist, role, kw, side)
        best_dest: tuple[int, str, str] | None = None             # (dist, kw, side)
        destination_seen = False

        for rk in kb.role_keywords:
            if rk.role == "duration":
                continue

            # build clause-bounded contexts using the *original-text* offsets we will
            # also need to compute absolute distance later.
            left_raw = text[max(0, cand.start - rk.window):cand.start]
            right_raw = text[cand.end:min(len(text), cand.end + rk.window)]
            l_parts = _CLAUSE_BREAK_RE.split(left_raw)
            r_parts = _CLAUSE_BREAK_RE.split(right_raw)
            left_ctx = l_parts[-1] if l_parts else ""
            right_ctx = r_parts[0] if r_parts else ""
            left_offset = len(left_raw) - len(left_ctx)
            # absolute text index where left_ctx starts in `text`
            left_abs_start = max(0, cand.start - rk.window) + left_offset
            right_abs_start = cand.end

            try:
                regex = re.compile(rk.keyword)
            except re.error:
                regex = re.compile(re.escape(rk.keyword))

            # optional context guard
            req_regex = None
            if rk.requires_context:
                try:
                    req_regex = re.compile(rk.requires_context)
                except re.error:
                    req_regex = re.compile(re.escape(rk.requires_context))

            scan_left = rk.direction in ("right", "any")
            scan_right = rk.direction in ("left", "any")

            def _consider(role: str, kw: str, dist: int, side: str) -> None:
                nonlocal best_filter, best_dest, destination_seen
                if role in _FILTER_ROLES:
                    if best_filter is None or dist < best_filter[0]:
                        best_filter = (dist, role, kw, side)
                elif role == "destination":
                    destination_seen = True
                    if best_dest is None or dist < best_dest[0]:
                        best_dest = (dist, kw, side)

            if scan_left:
                for m in regex.finditer(left_ctx):
                    if req_regex and not (req_regex.search(left_ctx) or req_regex.search(right_ctx)):
                        continue
                    keyword_end_abs = left_abs_start + m.end()
                    dist = cand.start - keyword_end_abs  # always >= 0
                    _consider(rk.role, rk.keyword, dist, "left")
            if scan_right:
                for m in regex.finditer(right_ctx):
                    if req_regex and not (req_regex.search(left_ctx) or req_regex.search(right_ctx)):
                        continue
                    keyword_start_abs = right_abs_start + m.start()
                    dist = keyword_start_abs - cand.end  # always >= 0
                    _consider(rk.role, rk.keyword, dist, "right")

        # decision
        if best_filter and (best_dest is None or
                            best_filter[0] + _FILTER_OVER_DEST_MARGIN <= best_dest[0]):
            _, role, kw, side = best_filter
            cand.role = role
            cand.role_reason = f"{role}: {kw} ({side})"
        elif best_dest:
            cand.confidence = min(1.0, cand.confidence + 0.15)

        # airports default to transit unless destination keyword fired in clause
        if (cand.landmark and cand.landmark.type == "airport"
                and cand.role == "destination" and not destination_seen):
            cand.role = "transit"
            cand.role_reason = "airport defaults to transit"


# ---------- Step 5: landmark backfill ----------

def apply_landmark_backfill(candidates: list[Candidate], kb: KB) -> list[dict]:
    """For each landmark candidate, build the parent chain so downstream tree compression sees it."""
    extras: list[dict] = []
    for cand in candidates:
        if not cand.landmark or cand.role != "destination" or not cand.resolved_id:
            continue
        chain = kb.parent_chain(cand.resolved_id)
        extras.append({
            "from_landmark": cand.text,
            "location_id": cand.resolved_id,
            "chain": [{"id": n.id, "name": n.name, "type": n.type} for n in chain],
        })
    return extras


# ---------- Step 6: tree compression ----------

def compress_tree(candidates: list[Candidate], kb: KB) -> dict:
    """Group destination candidates into country -> region -> city tree."""
    tree: dict[str, dict] = {}
    for cand in candidates:
        if cand.role != "destination" or not cand.resolved_id:
            continue
        country = kb.country_of(cand.resolved_id)
        region = kb.region_of(cand.resolved_id)
        city = kb.city_of(cand.resolved_id)
        spot_name = cand.landmark.landmark if cand.landmark else None

        if not country:
            continue
        c_key = country.id
        country_node = tree.setdefault(c_key, {
            "country_id": country.id,
            "country": country.name,
            "regions": {},
            "cities": {},
            "evidence": [],
        })
        country_node["evidence"].append(cand.text)

        if region:
            r_node = country_node["regions"].setdefault(region.id, {
                "region_id": region.id,
                "region": region.name,
                "cities": {},
            })
            if city:
                city_node = r_node["cities"].setdefault(city.id, {
                    "city_id": city.id,
                    "city": city.name,
                    "spots": [],
                    "confidence": cand.confidence,
                })
                if spot_name and spot_name not in city_node["spots"]:
                    city_node["spots"].append(spot_name)
                city_node["confidence"] = max(city_node["confidence"], cand.confidence)
        elif city:
            city_node = country_node["cities"].setdefault(city.id, {
                "city_id": city.id,
                "city": city.name,
                "spots": [],
                "confidence": cand.confidence,
            })
            if spot_name and spot_name not in city_node["spots"]:
                city_node["spots"].append(spot_name)
            city_node["confidence"] = max(city_node["confidence"], cand.confidence)

    # flatten to list view for easier downstream consumption
    out_tags = []
    for c in tree.values():
        # country-only candidates (no region, no city) — still emit so weak_signals
        # know about country-level anchoring
        if not c["regions"] and not c["cities"]:
            out_tags.append({
                "country": c["country"],
                "region": None,
                "city": None,
                "spots": [],
                "confidence": 0.6,
            })
        # cities directly under country (no region)
        for ci in c["cities"].values():
            out_tags.append({
                "country": c["country"],
                "region": None,
                "city": ci["city"],
                "spots": ci["spots"],
                "confidence": round(ci["confidence"], 3),
            })
        for r in c["regions"].values():
            if not r["cities"]:
                out_tags.append({
                    "country": c["country"],
                    "region": r["region"],
                    "city": None,
                    "spots": [],
                    "confidence": 0.7,
                })
            for ci in r["cities"].values():
                out_tags.append({
                    "country": c["country"],
                    "region": r["region"],
                    "city": ci["city"],
                    "spots": ci["spots"],
                    "confidence": round(ci["confidence"], 3),
                })
    return {"tree": tree, "tags": out_tags}


# ---------- Step 6.5: weak-signal scan ----------

def _has_strong_anchor(tags: list[dict], country: str) -> bool:
    """Country is anchored only if at least one tag for it is detailed to city or spots."""
    for t in tags:
        if t.get("country") != country:
            continue
        if t.get("city"):
            return True
        if t.get("spots"):
            return True
    return False


def scan_weak_signals(text: str, tags: list[dict], kb: KB) -> list[dict]:
    """Find weak-signal hits whose naive country is not strongly anchored.

    A warning fires when:
      - the weak-signal substring is present in `text`
      - AND `min_context` (if set) also matches `text` — guards short common words
        like 「企鵝/夜市/古城/北角」from firing on unrelated docs
      - AND none of `possible_countries` has a STRONG anchor in `tags`
        ("strong" = a tag for that country that has city or spots, not just
        country-level. So 「日本」alone doesn't anchor "迪士尼"; 「日本/東京」does.)
    """
    warnings: list[dict] = []
    for ws in kb.weak_signals:
        sig = ws.weak_signal
        if not sig or sig not in text:
            continue
        if ws.min_context:
            try:
                if not re.search(ws.min_context, text):
                    continue
            except re.error:
                if ws.min_context not in text:
                    continue
        anchored = any(_has_strong_anchor(tags, c) for c in ws.possible_countries)
        if anchored:
            continue
        warnings.append({
            "weak_signal": sig,
            "naive_country": ws.naive_country,
            "possible_countries": ws.possible_countries,
            "required_strong": ws.required_strong,
            "guidance": ws.guidance,
        })
        _audit("weak_signal",
               sig=sig,
               possible=ws.possible_countries,
               had_country_tag=any(t.get("country") in ws.possible_countries for t in tags),
               text_snippet=text[max(0, text.find(sig) - 15):text.find(sig) + len(sig) + 15])
    return warnings


# ---------- Step 6: LLM review (optional) ----------

_LLM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "llm_disambig.md"


def _build_llm_prompt(text: str, rule_layer: dict) -> str:
    """Build the full prompt for LLM review from template + rule layer output."""
    template = ""
    if _LLM_PROMPT_PATH.exists():
        template = _LLM_PROMPT_PATH.read_text(encoding="utf-8")
    return (
        f"{template}\n\n---\n\n"
        f"## 原文\n\n{text}\n\n"
        f"## 規則層輸出\n\n```json\n{json.dumps(rule_layer, ensure_ascii=False, indent=2)}\n```\n\n"
        "請依照上方任務 1-4 輸出嚴格 JSON。"
    )


def _call_codex_llm(prompt: str, *, codex_command: str = "codex", timeout: int = 120) -> dict | None:
    """Call Codex for text-only LLM review (no image)."""
    import shutil
    import subprocess
    import uuid

    resolved = shutil.which(codex_command) or codex_command
    tmp_dir = Path(__file__).resolve().parent.parent / ".cache" / "codex-llm-review"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path = tmp_dir / f"{uuid.uuid4().hex}.json"

    try:
        completed = subprocess.run(
            [resolved, "exec", "--output-last-message", str(output_path),
             "--sandbox", "read-only", prompt],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        if completed.returncode != 0 or not output_path.exists():
            return None
        raw = output_path.read_text(encoding="utf-8").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(raw)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    finally:
        output_path.unlink(missing_ok=True)


LlmCallback = Callable[[str, dict], dict | None]


# ---------- main pipeline ----------

def extract(
    text: str,
    kb: KB | None = None,
    llm_review: LlmCallback | None = None,
) -> dict:
    """Extract geo tags from travel text.

    Args:
        text: OCR or plain text input.
        kb: Knowledge base (loaded from SQLite if None).
        llm_review: Optional callback(prompt, rule_layer) -> LLM JSON result.
            Pass ``codex_llm_review`` for Codex integration, or any custom
            function that accepts (prompt_str, rule_layer_dict) and returns
            the LLM JSON output dict (or None to skip).
    """
    kb = kb or load_kb()
    term_tags, seen = extract_term_tags(text)
    raw_candidates = extract_candidates(text, kb)
    apply_disambig(text, raw_candidates, kb)
    candidates = dedup_candidates(raw_candidates)
    apply_term_precheck(text, candidates, term_tags, seen)
    apply_role(text, candidates, kb)
    backfill = apply_landmark_backfill(candidates, kb)
    compressed = compress_tree(candidates, kb)
    weak_warnings = scan_weak_signals(text, compressed["tags"], kb)

    filtered = [c.to_dict() for c in candidates if c.role != "destination"]
    needs_review = [c.to_dict() for c in candidates if 0.0 < c.confidence < 0.6 and c.role == "destination"]

    result = {
        "tags": compressed["tags"],
        "candidates_raw": [c.to_dict() for c in candidates],
        "filtered": filtered,
        "needs_review": needs_review,
        "term_tags": term_tags,
        "weak_warnings": weak_warnings,
        "landmark_backfill": backfill,
        "input_chars": len(text),
    }

    if llm_review and (needs_review or weak_warnings):
        rule_layer = {
            "tags": result["tags"],
            "filtered": filtered,
            "needs_review": needs_review,
            "term_tags": term_tags,
            "weak_warnings": weak_warnings,
        }
        prompt = _build_llm_prompt(text, rule_layer)
        llm_result = llm_review(prompt, rule_layer)
        if llm_result:
            result["llm_review"] = llm_result

    return result


def codex_llm_review(prompt: str, _rule_layer: dict) -> dict | None:
    """Ready-made LLM callback using Codex CLI."""
    return _call_codex_llm(prompt)


# ---------- output helpers ----------

def render_markdown(result: dict) -> str:
    lines = ["# 旅遊地名抽取結果", ""]
    if not result["tags"]:
        lines.append("（未抽到任何目的地）")
    for tag in result["tags"]:
        chain = " / ".join(filter(None, [tag["country"], tag.get("region"), tag.get("city")]))
        spots = ", ".join(tag.get("spots") or [])
        spot_text = f" — 景點：{spots}" if spots else ""
        lines.append(f"- **{chain}**{spot_text}  (信心度 {tag['confidence']})")

    if result["filtered"]:
        lines.append("")
        lines.append("## 已濾除（角色非目的地）")
        for f in result["filtered"]:
            lines.append(f"- `{f['text']}` → {f['role']} ({f.get('role_reason') or f.get('disambig_reason')})")

    if result["needs_review"]:
        lines.append("")
        lines.append("## 待人工/LLM 複核")
        for f in result["needs_review"]:
            lines.append(f"- `{f['text']}` → {f['location_ids']} {f.get('disambig_reason', '')}")

    if result.get("term_tags"):
        lines.append("")
        lines.append("## 非地理旅遊標籤")
        for t in result["term_tags"]:
            lines.append(f"- `{t['phrase']}` → {t['category']} (信心度 {t['confidence']})")

    if result.get("weak_warnings"):
        lines.append("")
        lines.append("## 弱信號警告（只命中行銷/跨國共用詞，未有強信號錨定）")
        for w in result["weak_warnings"]:
            possible = "、".join(w.get("possible_countries", []))
            lines.append(f"- `{w['weak_signal']}` ⚠️ 可能是: {possible}")
            if w.get("guidance"):
                lines.append(f"    指引: {w['guidance']}")

    return "\n".join(lines)


# ---------- CLI ----------

def _cmd_extract(args: argparse.Namespace) -> None:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    kb = load_kb()
    result = extract(text, kb)
    if args.format == "md":
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_test(args: argparse.Namespace) -> None:
    kb = load_kb()
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    if not fixtures_dir.exists():
        print(f"No fixtures at {fixtures_dir}")
        return
    failed = 0
    for case_file in sorted(fixtures_dir.glob("case_*.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        result = extract(case["input"], kb)
        actual_pairs = sorted({(t["country"], t.get("region"), t.get("city")) for t in result["tags"]})
        expected_pairs = sorted({(e["country"], e.get("region"), e.get("city")) for e in case["expected_tags"]})

        ok = actual_pairs == expected_pairs
        diag: list[str] = []
        if not ok:
            diag.append(f"  tags expected: {expected_pairs}")
            diag.append(f"  tags actual:   {actual_pairs}")

        # optional: weak warning expectation — require expected list be a subset of actual
        if "expected_weak_warnings" in case:
            actual_warnings = {w["weak_signal"] for w in result.get("weak_warnings", [])}
            expected_warnings = set(case["expected_weak_warnings"])
            missing = expected_warnings - actual_warnings
            if missing:
                ok = False
                diag.append(f"  missing weak warnings: {sorted(missing)}")
                diag.append(f"  actual weak warnings:  {sorted(actual_warnings)}")

        if "expected_filtered" in case:
            actual_filtered = {f["text"] for f in result.get("filtered", [])}
            expected_filtered = set(case["expected_filtered"])
            missing_filtered = expected_filtered - actual_filtered
            unexpected_filtered = actual_filtered - expected_filtered
            if missing_filtered or unexpected_filtered:
                ok = False
                if missing_filtered:
                    diag.append(f"  missing filtered: {sorted(missing_filtered)}")
                if unexpected_filtered:
                    diag.append(f"  unexpected filtered: {sorted(unexpected_filtered)}")
                diag.append(f"  actual filtered: {sorted(actual_filtered)}")

        if "expected_term_tags" in case:
            actual_terms = {(t["phrase"], t["category"]) for t in result.get("term_tags", [])}
            expected_terms = {(t["phrase"], t["category"]) for t in case["expected_term_tags"]}
            missing_terms = expected_terms - actual_terms
            if missing_terms:
                ok = False
                diag.append(f"  missing term tags: {sorted(missing_terms)}")
                diag.append(f"  actual term tags:  {sorted(actual_terms)}")

        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {case_file.name}: {case['description']}")
        if not ok:
            failed += 1
            for line in diag:
                print(line)
    if failed:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract travel geo tags from text")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ext = sub.add_parser("extract", help="run extraction on text or file")
    p_ext.add_argument("--text", help="raw text to process")
    p_ext.add_argument("--file", help="path to text file")
    p_ext.add_argument("--format", choices=["json", "md"], default="json")
    p_ext.set_defaults(func=_cmd_extract)

    p_test = sub.add_parser("test", help="run fixture-based regression")
    p_test.set_defaults(func=_cmd_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
