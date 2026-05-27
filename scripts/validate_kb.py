"""Validate knowledge base CSVs for integrity errors.

Checks foreign keys, regex compilability, enum values, numeric ranges,
duplicate entries, and hierarchy correctness.  Returns a list of Issue
objects; errors block build_db, warnings are informational.

Usage:
    python scripts/validate_kb.py [--data-dir path]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

VALID_LOCATION_TYPES = {"country", "region", "city"}
VALID_ALIAS_TYPES = {
    "formal", "en", "ja", "ko", "th", "canonical",
    "nickname", "historic", "abbr", "landmark", "ambig",
}
VALID_ROLES = {
    "destination", "departure", "transit", "exclude",
    "metaphor", "return", "duration",
}
VALID_DIRECTIONS = {"left", "right", "any"}


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    table: str
    row: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.table}:{self.row} ({self.column}) {self.message}"


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def _try_compile_regex(pattern: str) -> str | None:
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return str(e)


def validate_kb(data_dir: Path | str | None = None) -> list[Issue]:
    base = Path(data_dir) if data_dir else DATA_DIR
    issues: list[Issue] = []

    locations_rows = _read_csv(base / "locations.csv")
    aliases_rows = _read_csv(base / "aliases.csv")
    rules_rows = _read_csv(base / "disambig_rules.csv")
    landmarks_rows = _read_csv(base / "landmark_index.csv")
    role_kw_rows = _read_csv(base / "role_keywords.csv")
    weak_rows = _read_csv(base / "weak_signals.csv")

    # Build active location id set
    active_ids: set[str] = set()
    all_ids: set[str] = set()
    for i, row in enumerate(locations_rows, 2):
        loc_id = (row.get("id") or "").strip()
        if loc_id:
            all_ids.add(loc_id)
            active = (row.get("active") or "1").strip()
            if active != "0":
                active_ids.add(loc_id)

    # --- locations.csv ---
    for i, row in enumerate(locations_rows, 2):
        loc_id = (row.get("id") or "").strip()
        loc_type = (row.get("type") or "").strip()
        parent_id = (row.get("parent_id") or "").strip()
        active = (row.get("active") or "1").strip()

        if active == "0":
            continue

        # #10 enum: type
        if loc_type not in VALID_LOCATION_TYPES:
            issues.append(Issue("error", "locations.csv", i, "type",
                                f"invalid type '{loc_type}', expected {VALID_LOCATION_TYPES}"))

        # #5 FK: parent_id
        if parent_id and parent_id not in all_ids:
            issues.append(Issue("error", "locations.csv", i, "parent_id",
                                f"parent_id '{parent_id}' not found in locations"))

        # #20 hierarchy correctness
        if loc_type == "country" and parent_id:
            issues.append(Issue("warning", "locations.csv", i, "parent_id",
                                f"country '{loc_id}' has parent_id '{parent_id}' (expected empty)"))
        if loc_type == "region" and parent_id:
            parent_type = ""
            for r in locations_rows:
                if (r.get("id") or "").strip() == parent_id:
                    parent_type = (r.get("type") or "").strip()
                    break
            if parent_type and parent_type != "country":
                issues.append(Issue("error", "locations.csv", i, "parent_id",
                                    f"region '{loc_id}' parent '{parent_id}' is '{parent_type}', expected 'country'"))
        if loc_type == "city" and parent_id:
            parent_type = ""
            for r in locations_rows:
                if (r.get("id") or "").strip() == parent_id:
                    parent_type = (r.get("type") or "").strip()
                    break
            if parent_type and parent_type not in ("country", "region"):
                issues.append(Issue("error", "locations.csv", i, "parent_id",
                                    f"city '{loc_id}' parent '{parent_id}' is '{parent_type}', expected 'country' or 'region'"))

    # #19 cycle detection
    for loc_id in active_ids:
        visited: set[str] = set()
        current = loc_id
        while current:
            if current in visited:
                issues.append(Issue("error", "locations.csv", 0, "parent_id",
                                    f"cycle detected involving '{loc_id}'"))
                break
            visited.add(current)
            parent = ""
            for r in locations_rows:
                if (r.get("id") or "").strip() == current:
                    parent = (r.get("parent_id") or "").strip()
                    break
            current = parent

    # --- aliases.csv ---
    seen_aliases: set[tuple[str, str]] = set()
    for i, row in enumerate(aliases_rows, 2):
        alias = (row.get("alias") or "").strip()
        loc_id = (row.get("location_id") or "").strip()
        alias_type = (row.get("alias_type") or "formal").strip()
        conf_str = (row.get("confidence") or "1.0").strip() or "1.0"

        # #1 FK: location_id
        if loc_id and loc_id not in all_ids:
            issues.append(Issue("warning", "aliases.csv", i, "location_id",
                                f"location_id '{loc_id}' not in locations (may be out-of-scope)"))

        # #11 enum: alias_type
        if alias_type not in VALID_ALIAS_TYPES:
            issues.append(Issue("error", "aliases.csv", i, "alias_type",
                                f"invalid alias_type '{alias_type}', expected {VALID_ALIAS_TYPES}"))

        # #14 range: confidence
        try:
            conf = float(conf_str)
            if not 0.0 <= conf <= 1.0:
                issues.append(Issue("error", "aliases.csv", i, "confidence",
                                    f"confidence {conf} out of range [0.0, 1.0]"))
        except ValueError:
            issues.append(Issue("error", "aliases.csv", i, "confidence",
                                f"non-numeric confidence '{conf_str}'"))

        # #17 duplicate
        key = (alias.lower(), loc_id)
        if key in seen_aliases:
            issues.append(Issue("warning", "aliases.csv", i, "alias",
                                f"duplicate alias '{alias}' → '{loc_id}'"))
        seen_aliases.add(key)

    # --- disambig_rules.csv ---
    for i, row in enumerate(rules_rows, 2):
        resolution = (row.get("resolution") or "").strip()
        context_regex = (row.get("context_regex") or "").strip()
        priority_str = (row.get("priority") or "50").strip() or "50"

        # #2 FK: resolution
        if resolution and resolution != "SKIP" and resolution not in all_ids:
            issues.append(Issue("error", "disambig_rules.csv", i, "resolution",
                                f"resolution '{resolution}' not in locations and not 'SKIP'"))

        # #6 regex
        if context_regex:
            err = _try_compile_regex(context_regex)
            if err:
                issues.append(Issue("error", "disambig_rules.csv", i, "context_regex",
                                    f"invalid regex: {err}"))

        # #15 range: priority
        try:
            priority = int(priority_str)
            if not 0 <= priority <= 100:
                issues.append(Issue("error", "disambig_rules.csv", i, "priority",
                                    f"priority {priority} out of range [0, 100]"))
        except ValueError:
            issues.append(Issue("error", "disambig_rules.csv", i, "priority",
                                f"non-numeric priority '{priority_str}'"))

    # --- landmark_index.csv ---
    seen_landmarks: set[str] = set()
    for i, row in enumerate(landmarks_rows, 2):
        landmark = (row.get("landmark") or "").strip()
        loc_id = (row.get("location_id") or "").strip()
        country_id = (row.get("country_id") or "").strip()

        # #3 FK: location_id
        if loc_id and loc_id not in all_ids:
            issues.append(Issue("error", "landmark_index.csv", i, "location_id",
                                f"location_id '{loc_id}' not in locations"))

        # #4 FK: country_id
        if country_id and country_id not in all_ids:
            issues.append(Issue("error", "landmark_index.csv", i, "country_id",
                                f"country_id '{country_id}' not in locations"))

        # #18 duplicate
        if landmark in seen_landmarks:
            issues.append(Issue("warning", "landmark_index.csv", i, "landmark",
                                f"duplicate landmark '{landmark}'"))
        seen_landmarks.add(landmark)

    # --- role_keywords.csv ---
    for i, row in enumerate(role_kw_rows, 2):
        keyword = (row.get("keyword") or "").strip()
        role = (row.get("role") or "").strip()
        direction = (row.get("direction") or "any").strip() or "any"
        window_str = (row.get("window") or "10").strip() or "10"
        weight_str = (row.get("weight") or "1.0").strip() or "1.0"
        req_ctx = (row.get("requires_context") or "").strip()

        # #12 enum: role
        if role not in VALID_ROLES:
            issues.append(Issue("error", "role_keywords.csv", i, "role",
                                f"invalid role '{role}', expected {VALID_ROLES}"))

        # #13 enum: direction
        if direction not in VALID_DIRECTIONS:
            issues.append(Issue("error", "role_keywords.csv", i, "direction",
                                f"invalid direction '{direction}', expected {VALID_DIRECTIONS}"))

        # #7 regex: keyword
        if keyword:
            err = _try_compile_regex(keyword)
            if err:
                issues.append(Issue("error", "role_keywords.csv", i, "keyword",
                                    f"invalid regex: {err}"))

        # #8 regex: requires_context
        if req_ctx:
            err = _try_compile_regex(req_ctx)
            if err:
                issues.append(Issue("error", "role_keywords.csv", i, "requires_context",
                                    f"invalid regex: {err}"))

        # #16 range: weight, window
        try:
            weight = float(weight_str)
            if not 0.0 <= weight <= 1.0:
                issues.append(Issue("error", "role_keywords.csv", i, "weight",
                                    f"weight {weight} out of range [0.0, 1.0]"))
        except ValueError:
            issues.append(Issue("error", "role_keywords.csv", i, "weight",
                                f"non-numeric weight '{weight_str}'"))

        try:
            window = int(window_str)
            if window <= 0:
                issues.append(Issue("error", "role_keywords.csv", i, "window",
                                    f"window {window} must be > 0"))
        except ValueError:
            issues.append(Issue("error", "role_keywords.csv", i, "window",
                                f"non-numeric window '{window_str}'"))

    # --- weak_signals.csv ---
    for i, row in enumerate(weak_rows, 2):
        min_ctx = (row.get("min_context") or "").strip()

        # #9 regex: min_context
        if min_ctx:
            err = _try_compile_regex(min_ctx)
            if err:
                issues.append(Issue("error", "weak_signals.csv", i, "min_context",
                                    f"invalid regex: {err}"))

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate travel-geo-extract knowledge base CSVs.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    issues = validate_kb(args.data_dir)

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    for issue in issues:
        print(issue, file=sys.stderr)

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
