"""AI workflow-builder — describe a business, get a validated tenant config.

This is the moat: onboarding a new home-service client is a conversation, not
JSON editing. Describe the business in plain English; Claude writes the tenant
config (identity, trade, service area, quoting policy, missed-call opener), it's
validated against tenants.py, and saved to tenants/<id>.json — ready for the
same engine that serves every other client.

Run:
  python build_tenant.py "Sam runs Aqua Plumbing, a plumber in Sydney's inner
      west, 24/7 callouts, happy to give rough ballpark prices over text"
  python build_tenant.py "<description>" --save        # write the file
  echo "<description>" | python build_tenant.py         # description via stdin
"""

import argparse
import json
import sys
from typing import Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

import tenants

load_dotenv()

MODEL = "claude-haiku-4-5"
client = anthropic.Anthropic(max_retries=4)


class TenantConfig(BaseModel):
    """The config the builder produces. Mirrors tenants.REQUIRED_FIELDS so a
    generated config passes load_tenant() validation."""

    tenant_id: str
    business_name: str
    owner_name: str
    trade_noun: str
    trade_adj: str
    trade_slang: str
    quoting_policy: Literal["never", "ranges", "full"]
    city: str
    service_area_short: str
    service_area: str
    opener_sms: str
    emergency_def: str
    quote_def: str
    bookable_def: str
    notification_examples: str
    examples_block: str


BUILDER_PROMPT = """You configure a missed-call speed-to-lead SMS assistant for \
home-service businesses (electricians, plumbers, locksmiths, HVAC, cleaners, \
etc.). Given a plain-English description of one business, produce its config.

Field rules:
- tenant_id: a short lowercase slug from the business name (a-z, 0-9, hyphens). e.g. "aqua-plumbing".
- owner_name: the person who runs it / gets the lead alerts. If unnamed, use a sensible first name implied by the business, else "the team".
- trade_noun: the tradesperson noun — electrician, plumber, locksmith, etc.
- trade_adj: the word that fits "X work / X job / X advice" — electrical, plumbing, locksmith, etc.
- quoting_policy: choose from never / ranges / full.
  - "never" unless the description clearly indicates otherwise. This is the safe default.
  - "ranges" if they say they give rough/ballpark prices.
  - "full" if they say they quote firm/fixed prices.
- city / service_area_short / service_area: from the description. service_area_short is a brief phrase ("Sydney's inner west"); service_area is a fuller line for the prompt header ("the inner western suburbs of Sydney, NSW, Australia"). If location isn't given, infer nothing fancy — use what they said.
- trade_slang: the casual word for this tradesperson ("sparky" for electrician, "plumber", "locksmith", "chippie" for carpenter). Used in "suggest a closer <slang>".
- opener_sms: the missed-call text-back, written in the BUSINESS'S voice. Plain Australian English, warm and natural, about one or two short lines (aim <=160 chars). Mention the business name, apologise for missing the call, ask what they need and what suburb. NEVER mention price. No emojis.

These three define how THIS trade is triaged — make them specific to the trade, not generic:
- emergency_def: what counts as an emergency for this trade (the dangerous/urgent jobs that need an immediate human callback). e.g. for a locksmith: "anyone locked out of a home, car, or business, a snapped key in a lock, or a door/lock that won't secure after a break-in. When someone's stuck out or a property is unsecured, treat it as an emergency."
- quote_def: bigger or open-ended jobs that need to be seen or priced before committing.
- bookable_def: standard, clearly-scoped jobs that can be booked straight into a window.
- notification_examples: 3-4 short example alerts to the owner's phone, for THIS trade, showing the handoff format. Each starts with a tag (NEW LEAD / URGENT / QUOTE / BOOKED), then name (if known), suburb, the job, and what to do — a couple of lines each. Indent each with two spaces and separate with a blank line. Make the jobs trade-specific (a locksmith's URGENT is a lockout, not a burning smell).

- examples_block: 3-4 short example exchanges showing tone and judgement for THIS trade. Use EXACTLY this format, and base the scenarios on the trade's real jobs (an emergency, a price/qualify question, a standard bookable job, and the "are you a real person?" honest-disclosure example). Reference the owner by name. Start with this exact header line:
"# Example exchanges (for tone and judgement — these are not scripts)" then a blank line, then blocks of:
Customer: "..."
You: "..."
(short annotation in parentheses)
Separate each block with a blank line. Keep replies to 2-4 short SMS-style lines.

Return only the structured config."""


def generate(description: str) -> TenantConfig:
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=BUILDER_PROMPT,
        messages=[{"role": "user", "content": description}],
        output_format=TenantConfig,
    )
    if resp.parsed_output is None:
        raise RuntimeError(
            f"Model returned no valid config (stop_reason={resp.stop_reason})"
        )
    return resp.parsed_output


def main() -> int:
    ap = argparse.ArgumentParser(description="Describe a business; get a tenant config.")
    ap.add_argument("description", nargs="?", help="plain-English business description")
    ap.add_argument("--save", action="store_true", help="write tenants/<id>.json")
    args = ap.parse_args()

    description = args.description or sys.stdin.read().strip()
    if not description:
        ap.error("provide a business description (arg or stdin)")

    cfg = generate(description)
    data = cfg.model_dump()
    print(json.dumps(data, indent=2, ensure_ascii=False))

    path = tenants.TENANTS_DIR / f"{data['tenant_id']}.json"
    if not args.save:
        print(f"\n[dry-run] would write {path}. Re-run with --save to write it.")
        return 0
    if path.exists():
        print(f"\nRefusing to overwrite existing {path}. Delete it first if intended.")
        return 1

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tenants.load_tenant(data["tenant_id"])  # validates; raises if bad
    print(f"\nSaved + validated: {path}")
    print("Next: connect a number + (optional) add a trade knowledge file, then go live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
