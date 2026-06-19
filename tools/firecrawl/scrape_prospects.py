#!/usr/bin/env python3
"""
Wollongong trades prospect scraper for speed-to-lead outreach.

Pipeline: Firecrawl /search (find businesses) -> filter to real business sites
-> Firecrawl /scrape with a JSON schema (pull qualifying signals) -> write
../../clients/prospects.csv as an outreach-ready dossier.

Only pulls what's legitimately scrapable. Google review data and the live
call/form mystery-shop are left as blank columns for Hughie to fill, since
those need the Places API / a phone.

Run: python tools/firecrawl/scrape_prospects.py
"""

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---- config (edit these) ---------------------------------------------------
QUERIES = [
    "electrician Wollongong",
    "emergency electrician Wollongong",
    "electrician Shellharbour Illawarra",
]
RESULTS_PER_QUERY = 10
MAX_SCRAPES = 18          # cap credits on each run
SCRAPE_DELAY_SEC = 1.0

# Domains that are directories / socials, not the prospect's own site.
# Hitting one is itself a lead-source signal, recorded separately.
DIRECTORY_DOMAINS = {
    "hipages.com.au", "oneflare.com.au", "yellowpages.com.au", "truelocal.com.au",
    "localsearch.com.au", "airtasker.com", "yelp.com", "wordofmouth.com.au",
}
SKIP_DOMAINS = {
    "reddit.com", "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "google.com", "wikipedia.org", "gumtree.com.au", "indeed.com", "seek.com.au",
    "tripadvisor.com", "productreview.com.au", "seek.com",
}

API = "https://api.firecrawl.dev/v2"
ROOT = Path(__file__).resolve().parents[2]
OUT_CSV = ROOT / "clients" / "prospects.csv"

SCRAPE_SCHEMA = {
    "type": "object",
    "properties": {
        "business_name": {"type": "string"},
        "owner_name": {"type": "string", "description": "owner/founder name if stated"},
        "phone": {"type": "string"},
        "email": {"type": "string"},
        "suburb": {"type": "string"},
        "services": {"type": "array", "items": {"type": "string"}},
        "has_contact_form": {"type": "boolean"},
        "has_live_chat": {"type": "boolean"},
        "mentions_24_7": {"type": "boolean", "description": "claims 24/7 or after-hours availability"},
        "response_promise": {"type": "string", "description": "any stated callback/response time, else empty"},
        "lead_source_mentions": {
            "type": "array", "items": {"type": "string"},
            "description": "badges/links to hipages, oneflare, etc.",
        },
    },
    "required": ["business_name"],
}

CSV_COLUMNS = [
    # scraped automatically
    "business_name", "website", "phone", "email", "owner_name", "suburb",
    "services", "has_contact_form", "has_live_chat", "mentions_24_7",
    "response_promise", "lead_source_mentions",
    # manual / Places API (left blank)
    "google_rating", "google_reviews", "reviews_last_90d", "replies_to_reviews",
    "responsiveness_complaints", "call_test_result", "form_test_result",
    "leak_estimate_monthly", "notes",
]


COMPLAINT_PATTERNS = [
    "call back", "called back", "callback", "no answer", "never answer",
    "never called", "didn't call", "didnt call", "did not call", "no show",
    "didn't turn up", "didnt turn up", "didn't show", "unresponsive",
    "couldn't get", "couldnt get", "took days", "no response", "ignored",
    "waited days", "never returned", "never got back",
]


def load_env() -> dict:
    env = Path(__file__).resolve().parent / ".env"
    out = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def places_enrich(business_name: str, suburb: str, key: str) -> dict:
    """Look up a business on Google Places (New) -> rating, review count,
    and any responsiveness complaints quoted from the sampled reviews."""
    if not key:
        return {}
    query = f"{business_name} {suburb or 'Wollongong'} NSW"
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=json.dumps({"textQuery": query}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": ("places.displayName,places.rating,"
                                 "places.userRatingCount,places.reviews"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"notes": f"places lookup failed: {e}"}
    places = j.get("places") or []
    if not places:
        return {}
    p = places[0]
    complaints = []
    for rv in p.get("reviews", []):
        rating = rv.get("rating", 5)
        if rating > 3:          # only negative reviews count as complaints
            continue
        text = (rv.get("text") or {}).get("text", "")
        low = text.lower()
        if any(pat in low for pat in COMPLAINT_PATTERNS):
            complaints.append(f'[{rating}*] "{text.strip()[:140]}"')
    return {
        "google_rating": p.get("rating", ""),
        "google_reviews": p.get("userRatingCount", ""),
        "responsiveness_complaints": " || ".join(complaints),
    }


def post(path: str, payload: dict, key: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def domain(url: str) -> str:
    d = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return d[4:] if d.startswith("www.") else d


def main() -> None:
    env = load_env()
    key = env.get("FIRECRAWL_API_KEY")
    places_key = env.get("GOOGLE_PLACES_API_KEY", "")
    if not key:
        sys.exit("FIRECRAWL_API_KEY not found in tools/firecrawl/.env")

    # 1. search -> pool of candidate business sites (dedup by domain)
    candidates: dict[str, str] = {}   # domain -> url
    directory_hits: set[str] = set()
    for q in QUERIES:
        res = post("search", {"query": q, "limit": RESULTS_PER_QUERY}, key)
        for item in (res.get("data") or {}).get("web", []):
            d = domain(item["url"])
            if any(dir_d in d for dir_d in DIRECTORY_DOMAINS):
                directory_hits.add(d)
            elif any(skip in d for skip in SKIP_DOMAINS):
                continue
            elif d not in candidates:
                candidates[d] = item["url"]
        print(f"  search '{q}': pool now {len(candidates)} sites")

    targets = list(candidates.items())[:MAX_SCRAPES]
    print(f"\nScraping {len(targets)} business sites (directory sources seen: "
          f"{', '.join(sorted(directory_hits)) or 'none'})\n")

    # 2. scrape each with schema
    rows = []
    for d, url in targets:
        res = post("scrape", {
            "url": url,
            "formats": [{"type": "json", "schema": SCRAPE_SCHEMA}],
            "onlyMainContent": True,
        }, key)
        data = (res.get("data") or {}).get("json") or {}
        if not res.get("success"):
            print(f"  x {d}: {res.get('error', 'no data')}")
            continue
        row = {c: "" for c in CSV_COLUMNS}
        row.update({
            "business_name": data.get("business_name", ""),
            "website": url,
            "phone": data.get("phone", ""),
            "email": data.get("email", ""),
            "owner_name": data.get("owner_name", ""),
            "suburb": data.get("suburb", ""),
            "services": "; ".join(data.get("services") or []),
            "has_contact_form": data.get("has_contact_form", ""),
            "has_live_chat": data.get("has_live_chat", ""),
            "mentions_24_7": data.get("mentions_24_7", ""),
            "response_promise": data.get("response_promise", ""),
            "lead_source_mentions": "; ".join(data.get("lead_source_mentions") or []),
        })
        # enrich with Google Places (rating, review count, complaint scan)
        row.update(places_enrich(row["business_name"], row["suburb"], places_key))
        rows.append(row)
        flag = " <-- COMPLAINT" if row["responsiveness_complaints"] else ""
        print(f"  + {row['business_name'] or d} | {row['phone']} | "
              f"{row['google_rating']}* ({row['google_reviews']}) | "
              f"form={row['has_contact_form']} 24/7={row['mentions_24_7']}{flag}")
        time.sleep(SCRAPE_DELAY_SEC)

    # 3. write dossier
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} prospects -> {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
