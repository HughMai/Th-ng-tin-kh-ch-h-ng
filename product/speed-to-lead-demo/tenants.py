"""Tenant config loading (Phase 1).

Each client is a JSON file in `tenants/<id>.json` holding the identity fields
the conversation engine renders into its prompt — business name, owner, trade,
service area, and the missed-call opener SMS. This is the seam that turns the
single-client engine into a multi-tenant one, and the file the AI
workflow-builder (Phase 2) will read and write.
"""

from __future__ import annotations

import json
from pathlib import Path

TENANTS_DIR = Path(__file__).parent / "tenants"

# Fields every tenant config must define for the engine to render its prompt.
REQUIRED_FIELDS = (
    "tenant_id",
    "business_name",
    "owner_name",
    "trade_noun",
    "trade_adj",
    "trade_slang",
    "city",
    "service_area_short",
    "service_area",
    "opener_sms",
    "emergency_def",
    "quote_def",
    "bookable_def",
    "notification_examples",
    "examples_block",
)


def load_tenant(tenant_id: str) -> dict:
    """Load and validate a tenant config by id. Raises if missing or incomplete."""
    path = TENANTS_DIR / f"{tenant_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No tenant config at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Tenant '{tenant_id}' is missing fields: {missing}")
    policy = data.setdefault("quoting_policy", "never")
    if policy not in ("never", "ranges", "full"):
        raise ValueError(
            f"Tenant '{tenant_id}' has invalid quoting_policy '{policy}' "
            f"(expected never / ranges / full)"
        )
    return data


def all_tenants() -> list[dict]:
    """Load every valid tenant config. Skips files that fail validation so one
    bad config can't take the whole service down."""
    out = []
    for path in sorted(TENANTS_DIR.glob("*.json")):
        if path.stem == "example":  # committed schema template, not a real tenant
            continue
        try:
            out.append(load_tenant(path.stem))
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def save_google_calendar(tenant_id: str, refresh_token: str, calendar_id: str = "primary") -> None:
    """Persist a tenant's Google Calendar connection into their config file.
    Used by the dashboard's self-serve connect flow (mirrors connect_calendar.py)."""
    path = TENANTS_DIR / f"{tenant_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["google_calendar"] = {"refresh_token": refresh_token, "calendar_id": calendar_id}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_by_number(number: str) -> dict | None:
    """Route an inbound call/SMS to the tenant whose Twilio number was dialled.
    `number` is the Twilio `To` field (E.164). Returns None if unassigned."""
    if not number:
        return None
    for t in all_tenants():
        if t.get("twilio_number") == number:
            return t
    return None
