# Tests — "never drop a lead"

A dropped text costs the owner a job and us our reputation. Every test here
defends one invariant:

> **Every inbound call/text → the customer gets a reply AND the lead is visible
> to the owner. Even when the AI engine, calendar, or Telegram fails.**

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # once
.\.venv\Scripts\python.exe -m pytest                                # every change
```

Layers 1 & 2 are **free, offline, deterministic** (Twilio + Claude are mocked) —
run them on every change and before every deploy.

## The layers

| Layer | File | Catches | Built |
|---|---|---|---|
| 1. Unit | `test_unit.py` | routing, signature trust, time-rollover, store + report logic | ✅ |
| 2. Webhook integration | `test_webhook.py` | the silent-drop paths — **engine crash still texts customer+owner**, missed-call text-back, spoof rejection, human-takeover, **idempotency (no double-texts on Twilio retries)** | ✅ |
| 3. Engine evals | `evals.py` (TODO) | the *brain* — wrong triage, a price quoted, a missed emergency | ⬜ |
| 4. Live canary | `canary.py` + `test_canary.py` | what offline tests **can't** see: VPS down, DNS/TLS dead, Claude credits exhausted, DB wedged | ✅ |
| 5. Reconciliation | (prod cron) | "received vs replied" mismatch — the drop *detector* | ⬜ |

## Why offline tests aren't enough

Layers 1–3 prove the **code** is correct. The most likely real-world drop — the
VPS down, DNS/TLS broken, or Claude credits exhausted — is **invisible to every
offline test**. The live canary (Layer 4) catches it.

## Layer 4 — the live canary (`canary.py`)

Probes `/health/deep` over the public URL (DNS → TLS → Caddy → app → Claude →
DB) and pings Hughie on **@BaoBei09bot** on any failure. Creates no lead and
sends no customer SMS, so it's safe to run often.

```powershell
python canary.py              # alert only on failure (cron default)
python canary.py --heartbeat  # also send an "all good" ping (e.g. daily)
```

**Run it OFF the VPS** (a GitHub Actions cron, or another host) so a dead VPS
still produces an alert. Set `CANARY_TOKEN` (same value in the app's env and the
canary's), `BAOBEI_BOT_TOKEN`/`CANARY_CHAT_ID`, and `CANARY_URL` (or
`PUBLIC_HOSTNAME`). See `.env.example`. Suggested cadence: every ~10 min.
Pair with an UptimeRobot check on `/health` for dumb-liveness redundancy.

## Idempotency — fixed ✅

`/twilio/sms`, `/twilio/voice-status`, and the CCF voice text-back now dedupe on
Twilio's `MessageSid`/`CallSid` via `store.mark_seen()`, so a retried webhook
(slow turn) can't text the customer twice. Covered by `test_webhook.py`.
