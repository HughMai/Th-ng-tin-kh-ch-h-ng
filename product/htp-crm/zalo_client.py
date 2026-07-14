"""Zalo OA API client — OAuth token exchange/refresh + message sending.

Endpoints below match Zalo's OA API v3/v4 docs at the time this was written
(oauth.zaloapp.com, openapi.zalo.me). Zalo has changed these paths before —
if a call starts failing, check developers.zalo.me/docs first.

Tokens are NOT kept in .env (env vars aren't rewritable at runtime). They're
persisted to a small JSON file next to the SQLite DB and refreshed in place.
"""
import json
import time
from pathlib import Path

import requests

OAUTH_BASE = "https://oauth.zaloapp.com/v4/oa"
API_BASE = "https://openapi.zalo.me/v3.0/oa"

_tokens_path: Path = Path(__file__).parent / "data" / "zalo_tokens.json"


def configure(tokens_path: str) -> None:
    global _tokens_path
    _tokens_path = Path(tokens_path)
    _tokens_path.parent.mkdir(parents=True, exist_ok=True)


def auth_url(app_id: str, redirect_uri: str, state: str) -> str:
    return (
        f"{OAUTH_BASE}/permission?app_id={app_id}"
        f"&redirect_uri={redirect_uri}&state={state}"
    )


def _load_tokens() -> dict:
    if not _tokens_path.exists():
        return {}
    return json.loads(_tokens_path.read_text(encoding="utf-8"))


def _save_tokens(data: dict) -> None:
    _tokens_path.parent.mkdir(parents=True, exist_ok=True)
    _tokens_path.write_text(json.dumps(data), encoding="utf-8")


def exchange_code(app_id: str, app_secret: str, code: str, redirect_uri: str) -> dict:
    """First-time OAuth: code -> access_token + refresh_token. Saves to disk."""
    resp = requests.post(
        f"{OAUTH_BASE}/access_token",
        headers={"secret_key": app_secret, "Content-Type": "application/x-www-form-urlencoded"},
        data={"code": code, "app_id": app_id, "grant_type": "authorization_code"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Zalo OAuth exchange failed: {data}")
    data["obtained_at"] = int(time.time())
    _save_tokens(data)
    return data


def _refresh(app_id: str, app_secret: str, refresh_token: str) -> dict:
    resp = requests.post(
        f"{OAUTH_BASE}/access_token",
        headers={"secret_key": app_secret, "Content-Type": "application/x-www-form-urlencoded"},
        data={"refresh_token": refresh_token, "app_id": app_id, "grant_type": "refresh_token"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Zalo token refresh failed: {data}")
    data["obtained_at"] = int(time.time())
    _save_tokens(data)
    return data


def get_valid_access_token(app_id: str, app_secret: str) -> str | None:
    """Current access token, refreshing first if it's stale (>50 min old —
    Zalo OA access tokens are short-lived, on the order of an hour)."""
    tokens = _load_tokens()
    if not tokens.get("refresh_token"):
        return None
    age = int(time.time()) - tokens.get("obtained_at", 0)
    if age > 50 * 60:
        tokens = _refresh(app_id, app_secret, tokens["refresh_token"])
    return tokens.get("access_token")


def is_connected() -> bool:
    return bool(_load_tokens().get("refresh_token"))


def send_text(access_token: str, zalo_user_id: str, text: str) -> None:
    resp = requests.post(
        f"{API_BASE}/message/cs",
        headers={"access_token": access_token, "Content-Type": "application/json"},
        json={"recipient": {"user_id": zalo_user_id}, "message": {"text": text}},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"Zalo send failed: {data.get('message')}")
