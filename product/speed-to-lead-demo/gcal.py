"""Google Calendar booking for a tenant.

Each connected tenant stores a Google refresh token (see connect_calendar.py).
This module turns that token into a Calendar API client and creates events when
the engine books a job. Booking is best-effort: a calendar failure must never
break the customer reply (app.py calls this in a try/except).
"""

from __future__ import annotations

import os

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def is_connected(tenant: dict) -> bool:
    """True if this tenant has a Google Calendar refresh token stored."""
    gc = tenant.get("google_calendar") or {}
    return bool(gc.get("refresh_token"))


# --- Self-serve web OAuth ----------------------------------------------------
# connect_calendar.py runs a *desktop* OAuth flow (opens a local browser on the
# operator's machine). For a client clicking "Connect" in the dashboard we use
# the *web* flow instead: the dashboard redirects them to Google, Google
# redirects back to /oauth/google/callback, and we exchange the code for a
# refresh token. Needs a "Web application" OAuth client in Google Cloud Console
# with the callback registered as an authorized redirect URI.


def is_configured() -> bool:
    """True if the Google OAuth client credentials are set in the environment."""
    return bool(CLIENT_ID and CLIENT_SECRET)


def _web_flow(redirect_uri: str) -> Flow:
    config = {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
        }
    }
    # autogenerate_code_verifier=False disables PKCE: we build a fresh Flow in the
    # callback that wouldn't have the verifier, and this is a confidential client
    # (client_secret), so PKCE isn't needed. Without this, token exchange fails
    # with "invalid_grant: Missing code verifier".
    return Flow.from_client_config(
        config, scopes=SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False
    )


def authorization_url(redirect_uri: str, state: str) -> str:
    """Build the Google consent URL to send the client to. `state` is an opaque
    value we sign and verify on the way back (carries the tenant + CSRF guard).
    offline + consent guarantees a refresh token comes back."""
    url, _ = _web_flow(redirect_uri).authorization_url(
        access_type="offline", prompt="consent", state=state
    )
    return url


def exchange_code(redirect_uri: str, code: str) -> str:
    """Exchange the authorization code from the callback for a refresh token.
    Returns "" if Google didn't return one (client must re-approve with consent)."""
    flow = _web_flow(redirect_uri)
    flow.fetch_token(code=code)
    return flow.credentials.refresh_token or ""


def _service(tenant: dict):
    gc = tenant["google_calendar"]
    creds = Credentials(
        token=None,
        refresh_token=gc["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event(
    tenant: dict,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
) -> str:
    """Create an event in the tenant's calendar. Returns the event's link.

    start_iso / end_iso are ISO 8601 datetimes with a timezone offset.
    """
    gc = tenant["google_calendar"]
    calendar_id = gc.get("calendar_id", "primary")
    tz = tenant.get("timezone", "Australia/Sydney")
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
    }
    created = (
        _service(tenant)
        .events()
        .insert(calendarId=calendar_id, body=event)
        .execute()
    )
    return created.get("htmlLink", "")
