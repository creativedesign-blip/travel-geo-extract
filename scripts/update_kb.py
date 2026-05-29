"""CLI to maintain the knowledge base directly in SQLite.

Subcommands:
  list           list locations / aliases / rules / landmarks / role / weak
  find           search by name / alias / id
  add-location   insert a new country/region/city node
  add-alias      insert a new alias mapping
  add-rule       insert a new disambiguation rule
  add-landmark   insert a new landmark / airport
  add-rolekw     insert a new role keyword
  add-weak       insert a new weak signal
  remove         delete a row by table + primary key

Examples:
  python update_kb.py list --type country
  python update_kb.py find --term 高山
  python update_kb.py add-location --id JP-HKD-WKK --name 稚內 --parent JP-HKD --type city
  python update_kb.py add-alias --alias 東瀛之都 --location JP-KTO-TYO --type nickname
  python update_kb.py add-rule --term 神戶 --context "日本|港口" --resolution JP-KSI-KOB --priority 90
  python update_kb.py add-landmark --name 哲學之道 --city JP-KSI-KYT --type district
  python update_kb.py remove --table aliases --key "東瀛之都,JP-KTO-TYO"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_kb import ensure_utf8_stdout, load_kb  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "geo_kb.db"


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERR: database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# ---------- list ----------

def cmd_list(args: argparse.Namespace) -> None:
    kb = load_kb()
    if args.type == "country":
        for loc in sorted(kb.locations.values(), key=lambda l: l.sort_order):
            if loc.type == "country":
                print(f"{loc.id}\t{loc.name}\t{loc.en_name}")
    elif args.type == "region":
        for loc in kb.locations.values():
            if loc.type == "region":
                parent = kb.locations.get(loc.parent_id)
                print(f"{loc.id}\t{loc.name}\t(parent: {parent.name if parent else '?'})")
    elif args.type == "city":
        for loc in kb.locations.values():
            if loc.type == "city":
                country = kb.country_of(loc.id)
                print(f"{loc.id}\t{loc.name}\t({country.name if country else '?'})")
    elif args.type == "alias":
        for a in kb.aliases:
            print(f"{a.alias}\t-> {a.location_id}\t({a.alias_type})")
    elif args.type == "landmark":
        for lm in kb.landmarks:
            print(f"{lm.landmark}\t-> {lm.location_id}\t({lm.type})")
    elif args.type == "rule":
        for r in kb.rules:
            print(f"{r.ambiguous_term} | {r.context_regex} | win={r.window} | -> {r.resolution} | p={r.priority} | {r.reason}")
    elif args.type == "role":
        for rk in kb.role_keywords:
            print(f"{rk.keyword}\t{rk.role}\twin={rk.window}\tdir={rk.direction}\tw={rk.weight}")
    elif args.type == "weak":
        for ws in kb.weak_signals:
            possible = "、".join(ws.possible_countries)
            print(f"{ws.weak_signal}\t({ws.naive_country})\t可能: {possible}")


# ---------- find ----------

def cmd_find(args: argparse.Namespace) -> None:
    kb = load_kb()
    term = args.term
    print(f"### locations matching '{term}':")
    for loc in kb.locations.values():
        if term in loc.name or term in loc.en_name or term in loc.id or term in loc.ja_name:
            country = kb.country_of(loc.id)
            print(f"  {loc.id}\t{loc.name}\t{loc.type}\t({country.name if country else '-'})")
    print(f"### aliases matching '{term}':")
    for a in kb.aliases:
        if term in a.alias or term == a.location_id:
            print(f"  {a.alias}\t-> {a.location_id}\t({a.alias_type})")
    print(f"### landmarks matching '{term}':")
    for lm in kb.landmarks:
        if term in lm.landmark or any(term in al for al in lm.aliases) or term == lm.location_id:
            aliases = "|".join(lm.aliases)
            print(f"  {lm.landmark}\t[{aliases}]\t-> {lm.location_id}\t({lm.type})")
    print(f"### disambig rules matching '{term}':")
    for r in kb.rules:
        if term in r.ambiguous_term:
            print(f"  {r.ambiguous_term} | ctx={r.context_regex} | -> {r.resolution} | p={r.priority}")


# ---------- add-* ----------

def cmd_add_location(args: argparse.Namespace) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO locations (id, name, en_name, ja_name, type, parent_id, sort_order, active, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (args.id, args.name, args.en_name or "", args.ja_name or "",
             args.type, args.parent or None, args.sort_order, args.notes or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ locations  {args.id}  {args.name}  ({args.type})")


def cmd_add_alias(args: argparse.Namespace) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO aliases (alias, location_id, alias_type, confidence, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (args.alias, args.location, args.type, args.confidence, args.notes or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ aliases  {args.alias} -> {args.location}  ({args.type})")


def cmd_add_rule(args: argparse.Namespace) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO disambig_rules (ambiguous_term, context_regex, window, resolution, priority, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.term, args.context, args.window, args.resolution, args.priority, args.reason or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ disambig_rules  '{args.term}' [ctx: {args.context}] -> {args.resolution}  (p={args.priority})")


def cmd_add_landmark(args: argparse.Namespace) -> None:
    country_id = args.city.split("-")[0]
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO landmarks (landmark, alias, location_id, type, country_id, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.name, args.alias or "", args.city, args.type, country_id, args.notes or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ landmarks  '{args.name}' -> {args.city}  ({args.type})")


def cmd_add_rolekw(args: argparse.Namespace) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO role_keywords (keyword, role, window, direction, weight, requires_context, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (args.keyword, args.role, args.window, args.direction,
             args.weight, args.requires_context or "", args.notes or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ role_keywords  '{args.keyword}' -> {args.role}")


def cmd_add_weak(args: argparse.Namespace) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO weak_signals (weak_signal, naive_country, possible_countries, "
            "required_strong, guidance, min_context, source) VALUES (?, ?, ?, ?, ?, ?, 'curated')",
            (args.signal, args.naive or "", args.possible or "",
             args.strong or "", args.guidance or "", args.min_context or ""),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    print(f"+ weak_signals  '{args.signal}'")


# ---------- remove ----------

def cmd_remove(args: argparse.Namespace) -> None:
    table = args.table
    key = args.key

    table_pk = {
        "locations": ("id", [key]),
        "aliases": ("alias, location_id", key.split(",", 1)),
        "disambig_rules": ("id", [int(key)]),
        "landmarks": ("landmark", [key]),
        "role_keywords": ("id", [int(key)]),
        "weak_signals": ("weak_signal", [key]),
    }
    if table not in table_pk:
        print(f"ERR: unknown table '{table}'", file=sys.stderr)
        sys.exit(2)

    pk_cols, pk_vals = table_pk[table]
    where = " AND ".join(f"{col.strip()} = ?" for col in pk_cols.split(","))

    conn = _connect()
    cursor = conn.execute(f"DELETE FROM {table} WHERE {where}", pk_vals)
    conn.commit()
    conn.close()

    if cursor.rowcount:
        print(f"- {table}  deleted {cursor.rowcount} row(s)")
    else:
        print(f"  {table}  no matching row found", file=sys.stderr)


# ---------- main ----------

def main() -> None:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Maintain travel-geo-extract knowledge base (SQLite)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list rows by table")
    p_list.add_argument("--type", required=True,
                        choices=["country", "region", "city", "alias", "landmark", "rule", "role", "weak"])
    p_list.set_defaults(func=cmd_list)

    p_find = sub.add_parser("find", help="search across tables")
    p_find.add_argument("--term", required=True)
    p_find.set_defaults(func=cmd_find)

    p_loc = sub.add_parser("add-location", help="insert country/region/city")
    p_loc.add_argument("--id", required=True)
    p_loc.add_argument("--name", required=True)
    p_loc.add_argument("--en-name", default="")
    p_loc.add_argument("--ja-name", default="")
    p_loc.add_argument("--type", required=True, choices=["country", "region", "city"])
    p_loc.add_argument("--parent", default="")
    p_loc.add_argument("--sort-order", type=int, default=99)
    p_loc.add_argument("--notes", default="")
    p_loc.set_defaults(func=cmd_add_location)

    p_al = sub.add_parser("add-alias", help="insert alias")
    p_al.add_argument("--alias", required=True)
    p_al.add_argument("--location", required=True)
    p_al.add_argument("--type", default="nickname",
                      choices=["formal", "en", "ja", "ko", "th", "nickname",
                               "historic", "abbr", "ambig", "canonical", "landmark"])
    p_al.add_argument("--confidence", type=float, default=1.0)
    p_al.add_argument("--notes", default="")
    p_al.set_defaults(func=cmd_add_alias)

    p_r = sub.add_parser("add-rule", help="insert disambig rule")
    p_r.add_argument("--term", required=True)
    p_r.add_argument("--context", required=True, help="regex pattern")
    p_r.add_argument("--window", default="30", help="±N chars or 'full'")
    p_r.add_argument("--resolution", required=True, help="SKIP or location_id")
    p_r.add_argument("--priority", type=int, default=50)
    p_r.add_argument("--reason", default="")
    p_r.set_defaults(func=cmd_add_rule)

    p_lm = sub.add_parser("add-landmark", help="insert landmark / airport")
    p_lm.add_argument("--name", required=True)
    p_lm.add_argument("--alias", default="")
    p_lm.add_argument("--city", required=True, help="location_id of city")
    p_lm.add_argument("--type", default="landmark")
    p_lm.add_argument("--notes", default="")
    p_lm.set_defaults(func=cmd_add_landmark)

    p_rk = sub.add_parser("add-rolekw", help="insert role keyword")
    p_rk.add_argument("--keyword", required=True)
    p_rk.add_argument("--role", required=True,
                      choices=["destination", "departure", "transit", "exclude",
                               "metaphor", "return", "duration"])
    p_rk.add_argument("--window", type=int, default=10)
    p_rk.add_argument("--direction", default="any", choices=["left", "right", "any"])
    p_rk.add_argument("--weight", type=float, default=1.0)
    p_rk.add_argument("--requires-context", default="")
    p_rk.add_argument("--notes", default="")
    p_rk.set_defaults(func=cmd_add_rolekw)

    p_ws = sub.add_parser("add-weak", help="insert weak signal")
    p_ws.add_argument("--signal", required=True)
    p_ws.add_argument("--naive", default="", help="naive country guess")
    p_ws.add_argument("--possible", default="", help="pipe-separated possible countries")
    p_ws.add_argument("--strong", default="", help="required strong anchors")
    p_ws.add_argument("--guidance", default="")
    p_ws.add_argument("--min-context", default="", help="regex guard")
    p_ws.set_defaults(func=cmd_add_weak)

    p_rm = sub.add_parser("remove", help="delete a row")
    p_rm.add_argument("--table", required=True,
                      choices=["locations", "aliases", "disambig_rules", "landmarks", "role_keywords", "weak_signals"])
    p_rm.add_argument("--key", required=True, help="primary key value (comma-separated for composite)")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
