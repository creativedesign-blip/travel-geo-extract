"""Load knowledge base from SQLite.

The knowledge base lives in `../data/geo_kb.db`.  Build it from CSVs
with `python scripts/build_db.py` before first use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Location:
    id: str
    name: str
    en_name: str
    ja_name: str
    type: str  # country | region | city
    parent_id: str
    sort_order: int
    active: int
    notes: str


@dataclass
class Alias:
    alias: str
    location_id: str
    alias_type: str
    confidence: float
    notes: str = ""


@dataclass
class DisambigRule:
    ambiguous_term: str
    context_regex: str
    window: str           # int as string or "full"
    resolution: str       # SKIP or location_id
    priority: int
    reason: str = ""

    @property
    def window_int(self) -> int | None:
        if self.window == "full":
            return None
        try:
            return int(self.window)
        except (ValueError, TypeError):
            return 30


@dataclass
class Landmark:
    landmark: str
    aliases: list[str]    # parsed from pipe-separated alias column
    location_id: str
    type: str             # airport | building | district | mountain | ...
    country_id: str
    notes: str = ""


@dataclass
class RoleKeyword:
    keyword: str
    role: str             # destination | departure | transit | exclude | metaphor | return | duration
    window: int
    direction: str        # left | right — which side the candidate must sit on relative to the keyword
    weight: float
    requires_context: str = ""  # optional regex; if set, keyword fires only when this also matches within the same clause window
    notes: str = ""


@dataclass
class WeakSignal:
    """Cross-country marketing words that look like a place but aren't reliable alone.

    Example: "威尼斯" -> naive_country=義大利 but also possible_countries=澳門/越南/仿景.
    Triggered only when:
      - the substring is in the text
      - AND `min_context` regex (if non-empty) also matches the text
      - AND no possible_country has a strong (city/spot-level) tag anchoring it
    """
    weak_signal: str
    naive_country: str
    possible_countries: list[str]  # parsed from 、 separated
    required_strong: str
    guidance: str
    min_context: str = ""  # optional regex; weak signal fires only when this matches the doc
    source: str = "curated"


@dataclass
class KB:
    locations: dict[str, Location] = field(default_factory=dict)
    aliases: list[Alias] = field(default_factory=list)
    alias_index: dict[str, list[Alias]] = field(default_factory=dict)   # alias -> list of Alias
    rules: list[DisambigRule] = field(default_factory=list)
    rule_index: dict[str, list[DisambigRule]] = field(default_factory=dict)  # term -> rules
    landmarks: list[Landmark] = field(default_factory=list)
    landmark_index: dict[str, Landmark] = field(default_factory=dict)
    role_keywords: list[RoleKeyword] = field(default_factory=list)
    weak_signals: list[WeakSignal] = field(default_factory=list)

    def parent_chain(self, location_id: str) -> list[Location]:
        """Walk up parent_id pointers, root first."""
        chain: list[Location] = []
        current = self.locations.get(location_id)
        while current:
            chain.append(current)
            if not current.parent_id:
                break
            current = self.locations.get(current.parent_id)
        return list(reversed(chain))

    def country_of(self, location_id: str) -> Location | None:
        for node in self.parent_chain(location_id):
            if node.type == "country":
                return node
        return None

    def region_of(self, location_id: str) -> Location | None:
        for node in self.parent_chain(location_id):
            if node.type == "region":
                return node
        return None

    def city_of(self, location_id: str) -> Location | None:
        for node in self.parent_chain(location_id):
            if node.type == "city":
                return node
        return None


def _load_from_sqlite(db_path: Path) -> KB:
    """Load KB from a pre-built SQLite database."""
    conn = sqlite3.connect(str(db_path))
    try:
        return _load_from_connection(conn)
    finally:
        conn.close()


def _load_from_connection(conn: sqlite3.Connection) -> KB:
    conn.row_factory = sqlite3.Row
    kb = KB()

    for row in conn.execute("SELECT * FROM locations WHERE active = 1"):
        loc = Location(
            id=row["id"],
            name=row["name"],
            en_name=row["en_name"] or "",
            ja_name=row["ja_name"] or "",
            type=row["type"],
            parent_id=row["parent_id"] or "",
            sort_order=row["sort_order"] or 0,
            active=row["active"],
            notes=row["notes"] or "",
        )
        kb.locations[loc.id] = loc

    for row in conn.execute("SELECT * FROM aliases"):
        loc_id = row["location_id"]
        a = Alias(
            alias=row["alias"],
            location_id=loc_id,
            alias_type=row["alias_type"] or "formal",
            confidence=row["confidence"] if row["confidence"] is not None else 1.0,
            notes=row["notes"] or "",
        )
        kb.aliases.append(a)
        kb.alias_index.setdefault(a.alias, []).append(a)
        if a.alias != a.alias.lower():
            kb.alias_index.setdefault(a.alias.lower(), []).append(a)

    for loc in kb.locations.values():
        for name in (loc.name, loc.en_name, loc.ja_name):
            if name and name not in kb.alias_index:
                kb.alias_index.setdefault(name, []).append(
                    Alias(alias=name, location_id=loc.id, alias_type="canonical", confidence=1.0)
                )

    for row in conn.execute("SELECT * FROM disambig_rules ORDER BY priority DESC"):
        rule = DisambigRule(
            ambiguous_term=row["ambiguous_term"],
            context_regex=row["context_regex"],
            window=row["window"],
            resolution=row["resolution"],
            priority=row["priority"],
            reason=row["reason"] or "",
        )
        kb.rules.append(rule)
        kb.rule_index.setdefault(rule.ambiguous_term, []).append(rule)

    for row in conn.execute("SELECT * FROM landmarks"):
        alias_field = row["alias"] or ""
        aliases = [a.strip() for a in alias_field.split("|") if a.strip()] if alias_field else []
        lm = Landmark(
            landmark=row["landmark"],
            aliases=aliases,
            location_id=row["location_id"],
            type=row["type"],
            country_id=row["country_id"],
            notes=row["notes"] or "",
        )
        kb.landmarks.append(lm)
        kb.landmark_index[lm.landmark] = lm
        for alias in aliases:
            kb.landmark_index.setdefault(alias, lm)

    for row in conn.execute("SELECT * FROM role_keywords"):
        rk = RoleKeyword(
            keyword=row["keyword"],
            role=row["role"],
            window=row["window"],
            direction=row["direction"] or "any",
            weight=row["weight"] if row["weight"] is not None else 1.0,
            requires_context=row["requires_context"] or "",
            notes=row["notes"] or "",
        )
        kb.role_keywords.append(rk)

    try:
        for row in conn.execute("SELECT * FROM weak_signals"):
            possible_raw = row["possible_countries"] or ""
            possible = [c.strip() for c in possible_raw.replace(",", "、").split("、") if c.strip()]
            ws = WeakSignal(
                weak_signal=row["weak_signal"],
                naive_country=row["naive_country"] or "",
                possible_countries=possible,
                required_strong=row["required_strong"] or "",
                guidance=row["guidance"] or "",
                min_context=row["min_context"] or "",
                source=row["source"] or "curated",
            )
            kb.weak_signals.append(ws)
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise

    return kb


def load_kb(data_dir: Path | str | None = None) -> KB:
    base = Path(data_dir) if data_dir else DATA_DIR
    db_path = base / "geo_kb.db"
    if not db_path.exists():
        raise FileNotFoundError(
            f"Knowledge base not found: {db_path}\n"
            "Run 'python scripts/build_db.py' to build it from CSVs."
        )
    return _load_from_sqlite(db_path)


def stats(kb: KB) -> dict[str, int]:
    return {
        "locations_total": len(kb.locations),
        "countries": sum(1 for l in kb.locations.values() if l.type == "country"),
        "regions": sum(1 for l in kb.locations.values() if l.type == "region"),
        "cities": sum(1 for l in kb.locations.values() if l.type == "city"),
        "aliases": len(kb.aliases),
        "rules": len(kb.rules),
        "landmarks": len(kb.landmarks),
        "role_keywords": len(kb.role_keywords),
        "weak_signals": len(kb.weak_signals),
    }


if __name__ == "__main__":
    kb = load_kb()
    s = stats(kb)
    print("Knowledge base loaded from SQLite:")
    for k, v in s.items():
        print(f"  {k}: {v}")
