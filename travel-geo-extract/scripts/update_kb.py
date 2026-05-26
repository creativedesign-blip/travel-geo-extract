"""Interactive CLI to maintain the knowledge base CSVs.

Subcommands:
  list           list locations / aliases / rules / landmarks
  find           search by name / alias / id
  add-location   append a new country/region/city node
  add-alias      append a new alias mapping
  add-rule       append a new disambiguation rule
  add-landmark   append a new landmark / airport
  add-rolekw     append a new role keyword
  sync           pull/push CSV between skill dir and an external editor folder

All writes go to ../data/ relative to this script. Each write:
  1. Checks for duplicates (same id, or alias+location pair)
  2. Appends row in CSV-safe form (quotes commas, escapes quotes)
  3. Prints a 1-line diff summary

Examples:
  python update_kb.py list --type country
  python update_kb.py find --term 高山
  python update_kb.py add-location --id JP-HKD-WKK --name 稚內 --parent JP-HKD --type city
  python update_kb.py add-alias --alias 東瀛之都 --location JP-KTO-TYO --type nickname
  python update_kb.py add-rule --term 神戶 --context "日本|港口" --resolution JP-KSI-KOB --priority 90 --window 30
  python update_kb.py add-landmark --name 哲學之道 --city JP-KSI-KYT --type district
  python update_kb.py sync --from "C:/Users/User/Desktop/2d3d/旅遊表/data"
  python update_kb.py sync --to   "C:/Users/User/Desktop/2d3d/旅遊表/data"
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_kb import load_kb  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _csv_path(name: str) -> Path:
    return DATA_DIR / name


def _read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return fieldnames, rows


def _append_row(path: Path, row: dict, fieldnames: list[str]) -> None:
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


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
    else:
        print(f"unknown type: {args.type}", file=sys.stderr)
        sys.exit(2)


# ---------- find ----------

def cmd_find(args: argparse.Namespace) -> None:
    kb = load_kb()
    term = args.term
    print(f"### locations matching '{term}':")
    for loc in kb.locations.values():
        if term in loc.name or term in loc.en_name or term in loc.id or term in loc.ja_name:
            print(f"  {loc.id}\t{loc.name}\t{loc.type}\t({kb.country_of(loc.id).name if kb.country_of(loc.id) else '-'})")
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
    path = _csv_path("locations.csv")
    fieldnames, rows = _read_rows(path)
    if any(r.get("id") == args.id for r in rows):
        print(f"ERR: id {args.id} already exists", file=sys.stderr)
        sys.exit(1)
    row = {
        "id": args.id,
        "name": args.name,
        "en_name": args.en_name or "",
        "ja_name": args.ja_name or "",
        "type": args.type,
        "parent_id": args.parent or "",
        "sort_order": str(args.sort_order),
        "active": "1",
        "notes": args.notes or "",
    }
    _append_row(path, row, fieldnames)
    print(f"+ locations.csv  {args.id}  {args.name}  ({args.type})")
    print("  ⚠️  記得貼回 Excel 主檔，否則下次業務編輯 Excel 會看不到這條而誤刪")


def cmd_add_alias(args: argparse.Namespace) -> None:
    path = _csv_path("aliases.csv")
    fieldnames, rows = _read_rows(path)
    if any(r.get("alias") == args.alias and r.get("location_id") == args.location for r in rows):
        print(f"ERR: alias '{args.alias}' -> {args.location} already exists", file=sys.stderr)
        sys.exit(1)
    row = {
        "alias": args.alias,
        "location_id": args.location,
        "alias_type": args.type,
        "confidence": str(args.confidence),
        "notes": args.notes or "",
    }
    _append_row(path, row, fieldnames)
    print(f"+ aliases.csv  {args.alias} -> {args.location}  ({args.type})")
    print("  ⚠️  記得貼回 Excel 主檔（國家強關鍵字欄）以保持一致")


def cmd_add_rule(args: argparse.Namespace) -> None:
    path = _csv_path("disambig_rules.csv")
    fieldnames, rows = _read_rows(path)
    if any(r.get("ambiguous_term") == args.term
           and r.get("context_regex") == args.context
           and r.get("resolution") == args.resolution
           for r in rows):
        print("ERR: identical rule already exists", file=sys.stderr)
        sys.exit(1)
    row = {
        "ambiguous_term": args.term,
        "context_regex": args.context,
        "window": str(args.window),
        "resolution": args.resolution,
        "priority": str(args.priority),
        "reason": args.reason or "",
    }
    _append_row(path, row, fieldnames)
    print(f"+ disambig_rules.csv  '{args.term}' [ctx: {args.context}] -> {args.resolution}  (p={args.priority})")


def cmd_add_landmark(args: argparse.Namespace) -> None:
    path = _csv_path("landmark_index.csv")
    fieldnames, rows = _read_rows(path)
    if any(r.get("landmark") == args.name for r in rows):
        print(f"ERR: landmark '{args.name}' already exists", file=sys.stderr)
        sys.exit(1)
    # derive country from city id
    country_id = args.city.split("-")[0]
    row = {
        "landmark": args.name,
        "alias": args.alias or "",
        "location_id": args.city,
        "type": args.type,
        "country_id": country_id,
        "notes": args.notes or "",
    }
    _append_row(path, row, fieldnames)
    print(f"+ landmark_index.csv  '{args.name}' -> {args.city}  ({args.type})")


def cmd_add_rolekw(args: argparse.Namespace) -> None:
    path = _csv_path("role_keywords.csv")
    fieldnames, rows = _read_rows(path)
    if any(r.get("keyword") == args.keyword and r.get("role") == args.role for r in rows):
        print(f"ERR: role keyword '{args.keyword}'/{args.role} already exists", file=sys.stderr)
        sys.exit(1)
    row = {
        "keyword": args.keyword,
        "role": args.role,
        "window": str(args.window),
        "direction": args.direction,
        "weight": str(args.weight),
        "notes": args.notes or "",
    }
    _append_row(path, row, fieldnames)
    print(f"+ role_keywords.csv  '{args.keyword}' -> {args.role}  (dir={args.direction})")


# ---------- sync ----------

CSV_FILES = [
    "locations.csv", "aliases.csv", "disambig_rules.csv",
    "landmark_index.csv", "role_keywords.csv", "weak_signals.csv",
]


def cmd_sync(args: argparse.Namespace) -> None:
    src = Path(args.from_) if args.from_ else DATA_DIR
    dst = Path(args.to) if args.to else DATA_DIR
    if src == dst:
        print("ERR: --from and --to must differ; specify exactly one", file=sys.stderr)
        sys.exit(2)
    if not src.exists():
        print(f"ERR: source not found: {src}", file=sys.stderr)
        sys.exit(2)
    dst.mkdir(parents=True, exist_ok=True)
    for name in CSV_FILES:
        s = src / name
        d = dst / name
        if not s.exists():
            print(f"skip {name} (not in source)")
            continue
        shutil.copy2(s, d)
        print(f"  {s} -> {d}")
    print("sync complete")


# ---------- main ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain travel-geo-extract knowledge base")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list rows by table")
    p_list.add_argument("--type", required=True,
                        choices=["country", "region", "city", "alias", "landmark", "rule", "role"])
    p_list.set_defaults(func=cmd_list)

    p_find = sub.add_parser("find", help="search across tables")
    p_find.add_argument("--term", required=True)
    p_find.set_defaults(func=cmd_find)

    p_loc = sub.add_parser("add-location", help="append country/region/city")
    p_loc.add_argument("--id", required=True)
    p_loc.add_argument("--name", required=True)
    p_loc.add_argument("--en-name", default="")
    p_loc.add_argument("--ja-name", default="")
    p_loc.add_argument("--type", required=True, choices=["country", "region", "city"])
    p_loc.add_argument("--parent", default="")
    p_loc.add_argument("--sort-order", type=int, default=99)
    p_loc.add_argument("--notes", default="")
    p_loc.set_defaults(func=cmd_add_location)

    p_al = sub.add_parser("add-alias", help="append alias")
    p_al.add_argument("--alias", required=True)
    p_al.add_argument("--location", required=True)
    p_al.add_argument("--type", default="nickname",
                      choices=["formal", "en", "ja", "ko", "th", "iata", "nickname",
                               "historic", "abbr", "ambig", "canonical", "landmark"])
    p_al.add_argument("--confidence", type=float, default=1.0)
    p_al.add_argument("--notes", default="")
    p_al.set_defaults(func=cmd_add_alias)

    p_r = sub.add_parser("add-rule", help="append disambig rule")
    p_r.add_argument("--term", required=True)
    p_r.add_argument("--context", required=True, help="regex pattern")
    p_r.add_argument("--window", default="30", help="±N chars or 'full'")
    p_r.add_argument("--resolution", required=True, help="SKIP or location_id")
    p_r.add_argument("--priority", type=int, default=50)
    p_r.add_argument("--reason", default="")
    p_r.set_defaults(func=cmd_add_rule)

    p_lm = sub.add_parser("add-landmark", help="append landmark / airport")
    p_lm.add_argument("--name", required=True)
    p_lm.add_argument("--alias", default="")
    p_lm.add_argument("--city", required=True, help="location_id of city")
    p_lm.add_argument("--type", default="landmark")
    p_lm.add_argument("--notes", default="")
    p_lm.set_defaults(func=cmd_add_landmark)

    p_rk = sub.add_parser("add-rolekw", help="append role keyword")
    p_rk.add_argument("--keyword", required=True)
    p_rk.add_argument("--role", required=True,
                      choices=["destination", "departure", "transit", "exclude",
                               "metaphor", "return", "duration"])
    p_rk.add_argument("--window", type=int, default=10)
    p_rk.add_argument("--direction", default="any", choices=["left", "right", "any"])
    p_rk.add_argument("--weight", type=float, default=1.0)
    p_rk.add_argument("--notes", default="")
    p_rk.set_defaults(func=cmd_add_rolekw)

    p_sync = sub.add_parser("sync", help="sync CSVs to/from an external editor folder")
    p_sync.add_argument("--from", dest="from_", help="copy FROM this dir into skill data/")
    p_sync.add_argument("--to", help="copy skill data/ TO this dir")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
