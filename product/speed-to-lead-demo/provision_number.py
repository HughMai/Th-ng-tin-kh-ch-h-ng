"""Provision a Twilio number for a tenant and wire its webhooks.

The "give me a number -> it's live" step. Either assign a number you already
bought (--number) or buy a fresh AU mobile/local number (--buy), point its
voice + SMS webhooks at this app, and write twilio_number + owner_mobile into
the tenant config so inbound calls route to that tenant
(see tenants.find_by_number).

The client side (forwarding their existing line to this number) is Conditional
Call Forwarding — see references/ccf-setup-au.md. Twilio must approve the AU
regulatory bundle before this script can buy the number.

Run:
  python provision_number.py --tenant lockedout-locksmiths --number +61480000000 \
      --owner-mobile +61400000000
  python provision_number.py --tenant lockedout-locksmiths --buy \
      --number-type mobile --bundle-sid BUxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
      --owner-mobile +61400000000
  python provision_number.py --tenant ... --buy --number-type local \
      --area-code 02 --bundle-sid BUxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python provision_number.py --tenant ... --number +61... --dry-run
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from twilio.rest import Client

import tenants

load_dotenv()

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
if not PUBLIC_BASE_URL and os.environ.get("PUBLIC_HOSTNAME"):
    PUBLIC_BASE_URL = f"https://{os.environ['PUBLIC_HOSTNAME'].strip('/')}"


def _client() -> Client:
    sid = os.environ.get("TWILIO_ACCOUNT_SID") or os.environ.get("Twilio_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN") or os.environ.get("Twilio_Auth")
    if not sid or not token:
        sys.exit(
            "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN "
            "(legacy Twilio_SID/Twilio_Auth also supported)."
        )
    return Client(sid, token)


def _webhooks() -> dict:
    if not PUBLIC_BASE_URL:
        sys.exit("PUBLIC_BASE_URL is not set — needed for Twilio webhooks.")
    return {
        "voice_url": f"{PUBLIC_BASE_URL}/twilio/voice",
        "voice_method": "POST",
        "sms_url": f"{PUBLIC_BASE_URL}/twilio/sms",
        "sms_method": "POST",
    }


def _save_to_tenant(tenant_id: str, number: str, owner_mobile: str | None) -> None:
    path = tenants.TENANTS_DIR / f"{tenant_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["twilio_number"] = number
    if owner_mobile:
        data["owner_mobile"] = owner_mobile
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tenants.load_tenant(tenant_id)  # validate
    print(f"Wrote twilio_number={number} to {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision a Twilio number for a tenant.")
    ap.add_argument("--tenant", required=True, help="tenant id (tenants/<id>.json)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--number", help="assign an existing number you own (E.164)")
    g.add_argument("--buy", action="store_true", help="search + buy a new AU number")
    ap.add_argument(
        "--number-type",
        choices=("mobile", "local"),
        default="mobile",
        help="AU number type when buying (default: mobile)",
    )
    ap.add_argument("--area-code", help="area code for a local number (e.g. 02)")
    ap.add_argument(
        "--bundle-sid",
        help="approved Twilio AU regulatory bundle SID (BU...)",
    )
    ap.add_argument("--owner-mobile", help="where lead alerts go (E.164)")
    ap.add_argument("--dry-run", action="store_true", help="print actions, hit no APIs")
    args = ap.parse_args()

    if args.area_code and args.number_type != "local":
        ap.error("--area-code can only be used with --number-type local")
    if args.buy and not args.bundle_sid:
        ap.error("--bundle-sid is required when buying an AU number")

    tenants.load_tenant(args.tenant)  # tenant must exist + validate
    hooks = _webhooks()

    if args.dry_run:
        print(f"[dry-run] tenant={args.tenant}")
        action = (
            f"buy AU {args.number_type} number with bundle {args.bundle_sid}"
            if args.buy
            else "assign " + args.number
        )
        print(f"[dry-run] {action}")
        print(f"[dry-run] set voice_url={hooks['voice_url']}")
        print(f"[dry-run] set sms_url={hooks['sms_url']}")
        print(f"[dry-run] owner_mobile={args.owner_mobile}")
        print("[dry-run] then write twilio_number into the tenant config.")
        return 0

    client = _client()

    if args.buy:
        search = {"sms_enabled": True, "voice_enabled": True, "limit": 1}
        available = client.available_phone_numbers("AU")
        if args.number_type == "local":
            if args.area_code:
                search["area_code"] = args.area_code
            avail = available.local.list(**search)
        else:
            avail = available.mobile.list(**search)
        if not avail:
            sys.exit(
                f"No matching AU {args.number_type} numbers with voice and SMS available."
            )
        bought = client.incoming_phone_numbers.create(
            phone_number=avail[0].phone_number,
            bundle_sid=args.bundle_sid,
            **hooks,
        )
        number = bought.phone_number
        print(f"Bought {number} and wired webhooks.")
    else:
        number = args.number
        owned = client.incoming_phone_numbers.list(phone_number=number, limit=1)
        if not owned:
            sys.exit(f"{number} isn't in this Twilio account — buy it first or use --buy.")
        owned[0].update(**hooks)
        print(f"Wired webhooks on {number}.")

    _save_to_tenant(args.tenant, number, args.owner_mobile)
    print("\nDone. Inbound calls/SMS to this number now route to this tenant.")
    print("Client side: set Conditional Call Forwarding (references/ccf-setup-au.md).")
    print("The AU regulatory bundle used for purchase must remain compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
