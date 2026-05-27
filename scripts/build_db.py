"""Build SQLite database from knowledge base CSVs.

Reads the 6 CSV files, runs validate_kb, and writes a single geo_kb.db
that load_kb.py can use as the runtime store.

Usage:
    python scripts/build_db.py [--data-dir path] [--output path]
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = DATA_DIR / "geo_kb.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS locations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    en_name     TEXT DEFAULT '',
    ja_name     TEXT DEFAULT '',
    type        TEXT NOT NULL CHECK(type IN ('country','region','city')),
    parent_id   TEXT REFERENCES locations(id),
    sort_order  INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1 CHECK(active IN (0,1)),
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS aliases (
    alias       TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(id),
    alias_type  TEXT NOT NULL DEFAULT 'formal',
    confidence  REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
    notes       TEXT DEFAULT '',
    PRIMARY KEY (alias, location_id)
);

CREATE TABLE IF NOT EXISTS disambig_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ambiguous_term  TEXT NOT NULL,
    context_regex   TEXT NOT NULL,
    window          TEXT NOT NULL DEFAULT '30',
    resolution      TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 50 CHECK(priority BETWEEN 0 AND 100),
    reason          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS landmarks (
    landmark    TEXT PRIMARY KEY,
    alias       TEXT DEFAULT '',
    location_id TEXT NOT NULL REFERENCES locations(id),
    type        TEXT NOT NULL,
    country_id  TEXT NOT NULL REFERENCES locations(id),
    notes       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_keywords (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword          TEXT NOT NULL,
    role             TEXT NOT NULL CHECK(role IN ('destination','departure','transit','exclude','metaphor','return','duration')),
    window           INTEGER NOT NULL DEFAULT 10 CHECK(window > 0),
    direction        TEXT NOT NULL DEFAULT 'any' CHECK(direction IN ('left','right','any')),
    weight           REAL NOT NULL DEFAULT 1.0 CHECK(weight BETWEEN 0 AND 1),
    requires_context TEXT DEFAULT '',
    notes            TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS weak_signals (
    weak_signal        TEXT PRIMARY KEY,
    naive_country      TEXT DEFAULT '',
    possible_countries TEXT DEFAULT '',
    required_strong    TEXT DEFAULT '',
    guidance           TEXT DEFAULT '',
    min_context        TEXT DEFAULT '',
    source             TEXT DEFAULT 'curated'
);

CREATE INDEX IF NOT EXISTS idx_aliases_location ON aliases(location_id);
CREATE INDEX IF NOT EXISTS idx_disambig_term ON disambig_rules(ambiguous_term);
CREATE INDEX IF NOT EXISTS idx_landmarks_location ON landmarks(location_id);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id);
CREATE INDEX IF NOT EXISTS idx_locations_type ON locations(type);
"""


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def build_db(data_dir: Path, db_path: Path) -> None:
    from validate_kb import validate_kb

    issues = validate_kb(data_dir)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        raise RuntimeError(f"Validation failed with {len(errors)} error(s); database not built.")

    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)

    # --- locations (insert parents before children) ---
    rows = _read_csv(data_dir / "locations.csv")
    inserted: set[str] = set()
    pending = rows[:]

    # Simple topological insert: keep looping until all inserted
    max_passes = len(pending) + 1
    for _ in range(max_passes):
        still_pending = []
        for row in pending:
            loc_id = row["id"].strip()
            parent_id = (row.get("parent_id") or "").strip()
            if parent_id and parent_id not in inserted:
                still_pending.append(row)
                continue
            conn.execute(
                "INSERT INTO locations (id, name, en_name, ja_name, type, parent_id, sort_order, active, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    loc_id,
                    row["name"].strip(),
                    (row.get("en_name") or "").strip(),
                    (row.get("ja_name") or "").strip(),
                    row["type"].strip(),
                    parent_id or None,
                    int((row.get("sort_order") or "0").strip() or 0),
                    int((row.get("active") or "1").strip() or 1),
                    (row.get("notes") or "").strip(),
                ),
            )
            inserted.add(loc_id)
        pending = still_pending
        if not pending:
            break

    if pending:
        names = [r["id"].strip() for r in pending[:5]]
        raise RuntimeError(f"{len(pending)} location(s) could not be inserted (missing parent): {names}")

    # --- aliases ---
    for row in _read_csv(data_dir / "aliases.csv"):
        alias = row["alias"].strip()
        loc_id = row["location_id"].strip()
        if loc_id not in inserted:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO aliases (alias, location_id, alias_type, confidence, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                alias,
                loc_id,
                (row.get("alias_type") or "formal").strip(),
                float((row.get("confidence") or "1.0").strip() or 1.0),
                (row.get("notes") or "").strip(),
            ),
        )

    # --- disambig_rules ---
    for row in _read_csv(data_dir / "disambig_rules.csv"):
        conn.execute(
            "INSERT INTO disambig_rules (ambiguous_term, context_regex, window, resolution, priority, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["ambiguous_term"].strip(),
                row["context_regex"].strip(),
                row["window"].strip(),
                row["resolution"].strip(),
                int((row.get("priority") or "50").strip() or 50),
                (row.get("reason") or "").strip(),
            ),
        )

    # --- landmarks ---
    for row in _read_csv(data_dir / "landmark_index.csv"):
        loc_id = row["location_id"].strip()
        country_id = row["country_id"].strip()
        if loc_id not in inserted or country_id not in inserted:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO landmarks (landmark, alias, location_id, type, country_id, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                row["landmark"].strip(),
                (row.get("alias") or "").strip(),
                loc_id,
                row["type"].strip(),
                country_id,
                (row.get("notes") or "").strip(),
            ),
        )

    # --- role_keywords ---
    for row in _read_csv(data_dir / "role_keywords.csv"):
        conn.execute(
            "INSERT INTO role_keywords (keyword, role, window, direction, weight, requires_context, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["keyword"].strip(),
                row["role"].strip(),
                int((row.get("window") or "10").strip() or 10),
                (row.get("direction") or "any").strip() or "any",
                float((row.get("weight") or "1.0").strip() or 1.0),
                (row.get("requires_context") or "").strip(),
                (row.get("notes") or "").strip(),
            ),
        )

    # --- weak_signals ---
    for row in _read_csv(data_dir / "weak_signals.csv"):
        sig = (row.get("weak_signal") or "").strip()
        if not sig:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO weak_signals "
            "(weak_signal, naive_country, possible_countries, required_strong, guidance, min_context, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sig,
                (row.get("naive_country") or "").strip(),
                (row.get("possible_countries") or "").strip(),
                (row.get("required_strong") or "").strip(),
                (row.get("guidance") or "").strip(),
                (row.get("min_context") or "").strip(),
                (row.get("source") or "curated").strip(),
            ),
        )

    conn.commit()

    # Print summary
    counts = {}
    for table in ("locations", "aliases", "disambig_rules", "landmarks", "role_keywords", "weak_signals"):
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        counts[table] = count
    conn.close()

    print(f"Built {db_path}")
    for table, count in counts.items():
        print(f"  {table}: {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SQLite DB from knowledge base CSVs.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)

    try:
        build_db(args.data_dir, args.output)
    except RuntimeError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
