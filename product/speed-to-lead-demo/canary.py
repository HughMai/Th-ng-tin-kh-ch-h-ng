"""Synthetic canary — catches the silent breakages a customer would otherwise
find first: Claude credits exhausted, the app/VPS down, DNS/TLS broken, the DB
wedged. Offline tests can't see any of these; only a live probe can.

It hits /health/deep over the PUBLIC url (exercising DNS -> TLS -> Caddy -> app
-> Claude -> DB end to end) and, on any failure, pings Hughie on @BaoBei09bot.

Stdlib only, on purpose: run it OFF the VPS (a GitHub Actions cron, or any other
host) so that when the VPS itself dies, the canary still runs and alerts. Pair
it with an UptimeRobot check on /health for dumb-liveness redundancy.

    python canary.py              # alert only on failure (the cron default)
    python canary.py --heartbeat  # also send an "all good" ping (e.g. once a day)

Env:
    CANARY_URL        full probe url (default: $PUBLIC_BASE_URL/health/deep, or
                      https://$PUBLIC_HOSTNAME/health/deep)
    CANARY_TOKEN      token the endpoint requires (must match the app's CANARY_TOKEN)
    CANARY_TIMEOUT    seconds before the probe counts as failed (default 25)
    BAOBEI_BOT_TOKEN  @BaoBei09bot token (falls back to TELEGRAM_BOT_TOKEN)
    CANARY_CHAT_ID    Telegram chat id to alert (Hughie's own chat)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def probe_url() -> str:
    if os.environ.get("CANARY_URL"):
        return os.environ["CANARY_URL"]
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        host = os.environ.get("PUBLIC_HOSTNAME", "").strip("/")
        base = f"https://{host}" if host else ""
    url = f"{base}/health/deep"
    token = os.environ.get("CANARY_TOKEN", "")
    return f"{url}?token={urllib.parse.quote(token)}" if token else url


def fetch(url: str, timeout: float) -> tuple[int, str]:
    """GET the url. Returns (status_code, body). Raises on network/TLS/DNS error
    (urlopen also returns non-2xx as an HTTPError, which carries the code/body)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        return exc.code, exc.read().decode("utf-8", "replace")


def evaluate(status_code: int, body: str) -> tuple[bool, str]:
    """Decide pass/fail from a probe response. Pure — unit-tested without network."""
    if status_code != 200:
        return False, f"HTTP {status_code}: {body[:300]}"
    try:
        data = json.loads(body)
    except ValueError:
        return False, f"unparseable response: {body[:300]}"
    if data.get("status") != "ok":
        return False, f"deep check failed: {data.get('checks')}"
    return True, f"ok ({data.get('latency_ms')}ms)"


def send_telegram(text: str) -> bool:
    """Alert Hughie on @BaoBei09bot. Self-contained (no app imports) so the
    canary runs anywhere. Returns False if unconfigured or the send failed."""
    token = os.environ.get("BAOBEI_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("CANARY_CHAT_ID", "")
    if not token or not chat_id:
        print("[canary] BAOBEI_BOT_TOKEN / CANARY_CHAT_ID not set — cannot alert", file=sys.stderr)
        return False
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[canary] telegram alert failed: {exc}", file=sys.stderr)
        return False


def run(*, fetcher, sender, heartbeat: bool = False) -> int:
    """Probe, evaluate, alert. Returns a process exit code (0 ok, 1 failed) so a
    cron/CI run also surfaces the failure. `fetcher`/`sender` are injected for
    tests."""
    try:
        status_code, body = fetcher()
        ok, detail = evaluate(status_code, body)
    except Exception as exc:  # noqa: BLE001 — unreachable host, TLS, DNS, timeout
        ok, detail = False, f"unreachable: {exc}"

    if not ok:
        sender(f"🚨 Speed-to-Lead canary FAILED\n{detail}")
        return 1
    if heartbeat:
        sender(f"✅ Speed-to-Lead canary OK — {detail}")
    return 0


def main(argv: list[str]) -> int:
    heartbeat = "--heartbeat" in argv
    url = probe_url()
    if not url.startswith("http"):
        print("[canary] no probe url — set CANARY_URL or PUBLIC_BASE_URL/PUBLIC_HOSTNAME", file=sys.stderr)
        return 2
    timeout = float(os.environ.get("CANARY_TIMEOUT", "25"))
    return run(
        fetcher=lambda: fetch(url, timeout),
        sender=send_telegram,
        heartbeat=heartbeat,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
