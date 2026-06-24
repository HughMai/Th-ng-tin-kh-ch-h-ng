"""Connect a tenant's Google Calendar — the one-time OAuth step.

Opens a browser, the client signs in once and approves, and we save a refresh
token into the tenant config so the engine can book jobs into their calendar.

Setup (once, in Google Cloud Console):
  - Create an OAuth client (type: Desktop app).
  - Put its id/secret in .env as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.

Run (per client):
  python connect_calendar.py --tenant lockedout-locksmiths
  python connect_calendar.py --tenant ... --calendar-id someone@gmail.com
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

import tenants

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _client_config() -> dict:
    cid = os.environ.get("GOOGLE_CLIENT_ID")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first.")
    return {
        "installed": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Connect a tenant's Google Calendar.")
    ap.add_argument("--tenant", required=True, help="tenant id (tenants/<id>.json)")
    ap.add_argument("--calendar-id", default="primary", help="calendar to book into")
    args = ap.parse_args()

    tenants.load_tenant(args.tenant)  # must exist + validate

    flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
    # offline + consent guarantees a refresh token comes back.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        sys.exit("No refresh token returned — re-run and approve with a fresh consent.")

    path = tenants.TENANTS_DIR / f"{args.tenant}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["google_calendar"] = {
        "refresh_token": creds.refresh_token,
        "calendar_id": args.calendar_id,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Connected. Saved Google Calendar token to {path} (calendar: {args.calendar_id}).")
    print("The engine will now book confirmed jobs into this calendar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
