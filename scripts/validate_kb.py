"""Validate knowledge base SQLite for integrity errors.

Checks foreign keys, regex compilability, enum values, numeric ranges,
duplicate entries, and hierarchy correctness.

Usage:
    python scripts/validate_kb.py [--db path]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_kb import ensure_utf8_stdout  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = DATA_DIR / "geo_kb.db"

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


def _try_compile_regex(pattern: str) -> str | None:
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return str(e)


def validate_db(db_path: Path) -> list[Issue]:
    if not db_path.exists():
        return [Issue("error", "geo_kb.db", 0, "-", f"database not found: {db_path}")]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    issues: list[Issue] = []

    # Build location id sets
    all_ids: set[str] = set()
    active_ids: set[str] = set()
    loc_type_map: dict[str, str] = {}
    loc_parent_map: dict[str, str] = {}

    for row in conn.execute("SELECT id, type, parent_id, active FROM locations"):
        all_ids.add(row["id"])
        loc_type_map[row["id"]] = row["type"]
        loc_parent_map[row["id"]] = row["parent_id"] or ""
        if row["active"]:
            active_ids.add(row["id"])

    # --- locations ---
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM locations WHERE active = 1"), 1):
        loc_id = row["id"]
        loc_type = row["type"]
        parent_id = row["parent_id"] or ""

        if loc_type not in VALID_LOCATION_TYPES:
            issues.append(Issue("error", "locations", i, "type",
                                f"invalid type '{loc_type}'"))

        if parent_id and parent_id not in all_ids:
            issues.append(Issue("error", "locations", i, "parent_id",
                                f"parent_id '{parent_id}' not found"))

        if loc_type == "country" and parent_id:
            issues.append(Issue("warning", "locations", i, "parent_id",
                                f"country '{loc_id}' has parent_id '{parent_id}'"))
        if loc_type == "region" and parent_id:
            pt = loc_type_map.get(parent_id, "")
            if pt and pt != "country":
                issues.append(Issue("error", "locations", i, "parent_id",
                                    f"region '{loc_id}' parent is '{pt}', expected 'country'"))
        if loc_type == "city" and parent_id:
            pt = loc_type_map.get(parent_id, "")
            if pt and pt not in ("country", "region"):
                issues.append(Issue("error", "locations", i, "parent_id",
                                    f"city '{loc_id}' parent is '{pt}', expected 'country' or 'region'"))

    # cycle detection
    for loc_id in active_ids:
        visited: set[str] = set()
        current = loc_id
        while current:
            if current in visited:
                issues.append(Issue("error", "locations", 0, "parent_id",
                                    f"cycle detected involving '{loc_id}'"))
                break
            visited.add(current)
            current = loc_parent_map.get(current, "")

    # --- aliases ---
    seen_aliases: set[tuple[str, str]] = set()
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM aliases"), 1):
        loc_id = row["location_id"]
        alias_type = row["alias_type"] or "formal"

        if loc_id and loc_id not in all_ids:
            issues.append(Issue("warning", "aliases", i, "location_id",
                                f"location_id '{loc_id}' not in locations"))

        if alias_type not in VALID_ALIAS_TYPES:
            issues.append(Issue("error", "aliases", i, "alias_type",
                                f"invalid alias_type '{alias_type}'"))

        conf = row["confidence"]
        if conf is not None and not 0.0 <= conf <= 1.0:
            issues.append(Issue("error", "aliases", i, "confidence",
                                f"confidence {conf} out of range [0.0, 1.0]"))

        key = ((row["alias"] or "").lower(), loc_id)
        if key in seen_aliases:
            issues.append(Issue("warning", "aliases", i, "alias",
                                f"duplicate alias '{row['alias']}' → '{loc_id}'"))
        seen_aliases.add(key)

    # --- disambig_rules ---
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM disambig_rules"), 1):
        resolution = row["resolution"] or ""
        if resolution and resolution != "SKIP" and resolution not in all_ids:
            issues.append(Issue("error", "disambig_rules", i, "resolution",
                                f"resolution '{resolution}' not in locations and not 'SKIP'"))

        ctx = row["context_regex"] or ""
        if ctx:
            err = _try_compile_regex(ctx)
            if err:
                issues.append(Issue("error", "disambig_rules", i, "context_regex",
                                    f"invalid regex: {err}"))

        priority = row["priority"]
        if priority is not None and not 0 <= priority <= 100:
            issues.append(Issue("error", "disambig_rules", i, "priority",
                                f"priority {priority} out of range [0, 100]"))

    # --- landmarks ---
    seen_landmarks: set[str] = set()
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM landmarks"), 1):
        loc_id = row["location_id"]
        country_id = row["country_id"]
        landmark = row["landmark"]

        if loc_id and loc_id not in all_ids:
            issues.append(Issue("error", "landmarks", i, "location_id",
                                f"location_id '{loc_id}' not in locations"))
        if country_id and country_id not in all_ids:
            issues.append(Issue("error", "landmarks", i, "country_id",
                                f"country_id '{country_id}' not in locations"))
        if landmark in seen_landmarks:
            issues.append(Issue("warning", "landmarks", i, "landmark",
                                f"duplicate landmark '{landmark}'"))
        seen_landmarks.add(landmark)

    # --- role_keywords ---
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM role_keywords"), 1):
        role = row["role"] or ""
        direction = row["direction"] or "any"
        keyword = row["keyword"] or ""
        req_ctx = row["requires_context"] or ""

        if role not in VALID_ROLES:
            issues.append(Issue("error", "role_keywords", i, "role",
                                f"invalid role '{role}'"))
        if direction not in VALID_DIRECTIONS:
            issues.append(Issue("error", "role_keywords", i, "direction",
                                f"invalid direction '{direction}'"))
        if keyword:
            err = _try_compile_regex(keyword)
            if err:
                issues.append(Issue("error", "role_keywords", i, "keyword",
                                    f"invalid regex: {err}"))
        if req_ctx:
            err = _try_compile_regex(req_ctx)
            if err:
                issues.append(Issue("error", "role_keywords", i, "requires_context",
                                    f"invalid regex: {err}"))

        weight = row["weight"]
        if weight is not None and not 0.0 <= weight <= 1.0:
            issues.append(Issue("error", "role_keywords", i, "weight",
                                f"weight {weight} out of range [0.0, 1.0]"))
        window = row["window"]
        if window is not None and window <= 0:
            issues.append(Issue("error", "role_keywords", i, "window",
                                f"window {window} must be > 0"))

    # --- weak_signals ---
    for i, row in enumerate(conn.execute("SELECT rowid, * FROM weak_signals"), 1):
        min_ctx = row["min_context"] or ""
        if min_ctx:
            err = _try_compile_regex(min_ctx)
            if err:
                issues.append(Issue("error", "weak_signals", i, "min_context",
                                    f"invalid regex: {err}"))

    conn.close()
    return issues


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Validate travel-geo-extract knowledge base (SQLite).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)

    issues = validate_db(args.db)

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    for issue in issues:
        print(issue, file=sys.stderr)

    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
