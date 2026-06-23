"""Telegram helpers for the owner lead-mirror bot (@LeadCapturev1_bot).

Separate from the Hermes bot — uses its own LEADBOT_TOKEN so the two never
interfere. One bot serves every tenant; each tenant's leads post to that
tenant's own group/DM (tenant config `telegram_chat_id`). The owner replies to
a lead's message to take over; see /telegram/webhook in app.py.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _token() -> str:
    return os.environ.get("LEADBOT_TOKEN", "")


def send_message(chat_id, text: str, reply_to_message_id: int | None = None) -> int | None:
    """Post a message to a chat. Returns the Telegram message_id (so we can map
    it back to a lead when the owner replies), or None if unconfigured/failed.
    Best-effort: a Telegram failure must never break the SMS flow."""
    token = _token()
    if not token or not chat_id:
        return None
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=10))
        return (resp.get("result") or {}).get("message_id")
    except Exception as exc:  # noqa: BLE001 — never let Telegram break a lead
        print(f"[telegram] send failed: {exc}")
        return None
