"""Client login accounts for the owner dashboard.

Operator-provisioned: you create an account for a client after they sign up
(create_account.py), hand them the username + password, and they log into their
own tenant's dashboard. Passwords are pbkdf2-hashed; the store lives in
data/accounts.json, which is gitignored — plaintext passwords are never stored
or committed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

ACCOUNTS_PATH = Path(__file__).parent / "data" / "accounts.json"
_ITERATIONS = 200_000


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS
    ).hex()


def _load() -> dict:
    if ACCOUNTS_PATH.exists():
        return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_account(tenant_id: str, username: str, password: str) -> None:
    """Create or reset a client's login, mapping a username to their tenant."""
    data = _load()
    salt = secrets.token_hex(16)
    data[username.lower()] = {
        "tenant_id": tenant_id,
        "salt": salt,
        "hash": _hash(password, salt),
    }
    _save(data)


def verify(username: str, password: str) -> str | None:
    """Return the tenant_id if the credentials are valid, else None."""
    rec = _load().get(username.lower())
    if not rec:
        return None
    if hmac.compare_digest(rec["hash"], _hash(password, rec["salt"])):
        return rec["tenant_id"]
    return None
