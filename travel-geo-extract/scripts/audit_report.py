"""Summarize logs/disambig_audit.jsonl into a monthly report.

Use this to decide whether to migrate disambig_rules -> N-gram table.

Watch for these signals:
  - rule count > 80 (was 41 at v0.2 launch)
  - same `cand_text` + `text_snippet` shape appearing many times in `rule_miss`
    (= business is asking for a rule that doesn't exist yet)
  - `weak_signal` events firing on the same `sig` repeatedly across many docs
    (= add anchor or refine min_context)

Usage:
    python audit_report.py                     # all-time
    python audit_report.py --days 30           # last 30 days
    python audit_report.py --event rule_hit    # filter to one event type
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "disambig_audit.jsonl"


def load(days: int | None) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    cutoff = None
    if days:
        cutoff = dt.datetime.now() - dt.timedelta(days=days)
    rows = []
    with LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff:
                try:
                    if dt.datetime.fromisoformat(r["ts"]) < cutoff:
                        continue
                except (KeyError, ValueError):
                    continue
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None, help="window (days from now)")
    ap.add_argument("--event", default=None, help="filter to one event type")
    ap.add_argument("--top", type=int, default=20, help="how many top entries to show")
    args = ap.parse_args()

    rows = load(args.days)
    if args.event:
        rows = [r for r in rows if r.get("event") == args.event]
    if not rows:
        print("(no audit data yet — run extract.py on some real DMs first)")
        return

    span = f"last {args.days}d" if args.days else "all time"
    print(f"=== audit report ({span}, {len(rows)} events) ===")
    print()

    by_event = Counter(r["event"] for r in rows)
    print("Event breakdown:")
    for ev, n in by_event.most_common():
        print(f"  {ev}: {n}")
    print()

    # rule_hit: which rules fire most?
    hits = [r for r in rows if r["event"] == "rule_hit"]
    if hits:
        rule_counter: Counter = Counter()
        for r in hits:
            key = f"{r.get('term')} | ctx={r.get('context_regex')[:30]}... | -> {r.get('resolution')}"
            rule_counter[key] += 1
        print(f"Top {args.top} most-hit disambig rules:")
        for k, n in rule_counter.most_common(args.top):
            print(f"  [{n:>4}] {k}")
        print()

    # rule_miss: terms with rules that never fired — candidates for new N-gram patterns
    misses = [r for r in rows if r["event"] == "rule_miss"]
    if misses:
        miss_counter: Counter = Counter(r.get("term", "") for r in misses)
        print(f"Top {args.top} rule-miss terms (rules exist but didn't match — consider adding patterns):")
        for term, n in miss_counter.most_common(args.top):
            samples = [r["text_snippet"] for r in misses if r.get("term") == term][:3]
            print(f"  [{n:>4}] {term}")
            for s in samples:
                print(f"         e.g. \"{s}\"")
        print()

    # weak signal: which weak signals fire most & how often anchored
    wsigs = [r for r in rows if r["event"] == "weak_signal"]
    if wsigs:
        ws_counter: Counter = Counter(r.get("sig", "") for r in wsigs)
        ws_anchored: dict[str, int] = defaultdict(int)
        for r in wsigs:
            if r.get("had_country_tag"):
                ws_anchored[r.get("sig", "")] += 1
        print(f"Top {args.top} weak signals (anchored = country tag exists but no city/spot detail):")
        for sig, n in ws_counter.most_common(args.top):
            anchored = ws_anchored.get(sig, 0)
            print(f"  [{n:>4}, of which {anchored} country-anchored] {sig}")
        print()

    # ambig_unresolved: rules without a matching context — strong sign N-gram needed
    ambigs = [r for r in rows if r["event"] == "ambig_unresolved"]
    if ambigs:
        amb_counter: Counter = Counter(r.get("term", "") for r in ambigs)
        print(f"Top {args.top} unresolved ambiguous terms (forced first-of-list pick):")
        for term, n in amb_counter.most_common(args.top):
            print(f"  [{n:>4}] {term}")
        print()

    # migration trigger summary
    print("=== N-gram migration triggers ===")
    rule_count_now = len({(r["term"], r["context_regex"], r["resolution"]) for r in hits})
    print(f"  Distinct rules fired in window: {rule_count_now}")
    print(f"  Disambig rule misses: {len(misses)} ({len({r['term'] for r in misses})} distinct terms)")
    print(f"  Weak signal alerts: {len(wsigs)}")
    print(f"  Ambig unresolved: {len(ambigs)}")
    print()
    advice = []
    if rule_count_now > 80:
        advice.append("⚠️  rule count > 80 — consider N-gram migration for maintainability")
    if len({r['term'] for r in misses}) > 10:
        advice.append("⚠️  many distinct terms missed rules — N-gram catalog easier to fill gaps")
    if len(ambigs) > 50:
        advice.append("⚠️  many forced ambig choices — add explicit disambig rules or N-gram patterns")
    if not advice:
        print("  ✅ No migration triggers hit. regex disambig still healthy.")
    else:
        for a in advice:
            print(" ", a)


if __name__ == "__main__":
    main()
