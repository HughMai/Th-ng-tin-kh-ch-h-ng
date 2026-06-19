#!/usr/bin/env python3
"""
Turn clients/prospects.csv into one Missed-Call Audit one-pager per prospect.

Reads the scraped/enriched CSV, computes the "dollars left on the table" leak
model, and writes a print-ready markdown leave-behind to clients/onepagers/.
The live-test lines (call / form mystery-shop) are left as fill-in blanks --
that's the evidence Hughie adds after dialling.

Run: python tools/firecrawl/make_onepagers.py
"""

import csv
import re
from datetime import date
from pathlib import Path

# ---- leak model defaults (grounded in references/, edit per call) ----------
PRICE_MO = 300            # $/mo AUD -- DECIDED 2026-06-17, pricing-ops-policy.md
MISSED_CALLS_WK = 5       # solo sparky benchmark
NO_CALLBACK = 0.85        # share of missed callers who never call back
CLOSE_RATE = 0.40
AVG_JOB = 500             # >$500 typical
WEEKS_PER_MO = 4.3

ROOT = Path(__file__).resolve().parents[2]
CSV_IN = ROOT / "clients" / "prospects.csv"
OUT_DIR = ROOT / "clients" / "onepagers"


def leak() -> tuple[float, int]:
    lost_jobs = MISSED_CALLS_WK * WEEKS_PER_MO * NO_CALLBACK * CLOSE_RATE
    return round(lost_jobs, 1), round(lost_jobs * AVG_JOB)


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "prospect"


def snapshot(r: dict) -> str:
    lines = []
    if r["google_rating"]:
        lines.append(f"- Google: {r['google_rating']}★, {r['google_reviews']} reviews")
    else:
        lines.append("- Google: not auto-matched — check their GBP manually")
    lines.append(f"- Website contact form: {r['has_contact_form'] or '?'} · "
                 f"claims 24/7: {r['mentions_24_7'] or '?'}")
    if r["response_promise"].strip():
        lines.append(f'- Their stated promise: "{r["response_promise"].strip()}" '
                     f"← hold them to this")
    if r["responsiveness_complaints"].strip():
        lines.append(f"- Review complaint: {r['responsiveness_complaints'][:200]}")
    return "\n".join(lines)


def page(r: dict, lost_jobs: float, lost_dollars: int) -> str:
    name = r["business_name"] or "This business"
    return f"""# {name} — Missed-Call Audit · {date.today():%d %b %Y}

## Your public snapshot
{snapshot(r)}

## What I found (live test)   ← fill in after your mystery-shop
- Called [day/time] → [voicemail / no answer / answered]
- Submitted your website form [day/time] → [reply in __ / no reply in 24h]

## The leak (your numbers — adjustable on the call)
~{MISSED_CALLS_WK} missed calls/wk → ~{lost_jobs} lost jobs/mo → **~A${lost_dollars:,}/mo leaking**
({int(NO_CALLBACK*100)}% of missed callers never call back · {int(CLOSE_RATE*100)}% close · A${AVG_JOB} avg job)

## The fix
Every missed call → instant text in <60s → booked job. Live in 48h. You do nothing.

## The math
**A${PRICE_MO}/mo** vs **~A${lost_dollars:,}/mo** leaking. Pays for itself on under one job.

---
*Contact: {r['phone'] or '—'}{' · ' + r['email'] if r['email'] else ''} · {r['website']}*
"""


def main() -> None:
    rows = list(csv.DictReader(CSV_IN.open(encoding="utf-8")))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lost_jobs, lost_dollars = leak()
    for r in rows:
        path = OUT_DIR / f"{slug(r['business_name'] or r['website'])}.md"
        path.write_text(page(r, lost_jobs, lost_dollars), encoding="utf-8")
    print(f"Wrote {len(rows)} one-pagers -> {OUT_DIR.relative_to(ROOT)}")
    print(f"Leak model: {MISSED_CALLS_WK} missed/wk -> ~{lost_jobs} jobs/mo -> "
          f"~A${lost_dollars:,}/mo  (price A${PRICE_MO}/mo)")


if __name__ == "__main__":
    main()
