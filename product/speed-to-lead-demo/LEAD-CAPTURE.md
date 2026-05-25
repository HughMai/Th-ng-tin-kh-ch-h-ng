---
bike-method-phase: 1  # Phase 1 — Training wheels. Run manually. Review every output by hand.
three-ms-attribution: |
  Adapted from The Three Ms of AI™ © 2026 Nate Herk. All rights reserved.
---

# Lead Capture — Demo → Telegram → outreach/leads.md

## What this is

A capture surface on the live speed-to-lead simulator (`https://stl.187-77-133-39.sslip.io/`). When a prospect exchanges ≥2 messages with the demo, a CTA banner surfaces asking for name, business, and contact. Submission:

1. Appends a JSONL record to `/app/data/leads.jsonl` on the VPS (persistent volume).
2. Pings Hughie's Telegram via `@BaoBei09bot` with the lead's details + a 1-line conversation summary.

**No AI in the capture loop** — pure deterministic L0. AI-drafted follow-up belongs in `/outreach`.

## How to use (Phase 1 — Training wheels)

The point of Phase 1 is that **you manually review and route every captured lead** before it enters the outreach pipeline. Don't skip this. The manual step is the validation step.

When Telegram pings with `[NEW LEAD] <name> (<business>)`:

1. Read the message in Telegram — name, business, contact, conversation summary, message count.
2. Decide: real prospect, junk, or duplicate?
3. If real, **copy the lead into `outreach/leads.md`** as a new row (markdown table format per the `/outreach` skill's expected schema).
4. Run `/outreach` when you're ready to draft follow-ups.

## When to advance to Phase 2

Phase 2 = auto-sync VPS `leads.jsonl` → local `outreach/leads.md`, eliminating the manual copy step.

Advance when **both** are true:

- ≥10 real leads captured, and the manual copy felt redundant on ≥8 of them.
- <1-in-10 captures is junk (filter rate low enough that auto-sync is safe).

Until then, the manual copy is doing useful work — keep it.

## Files touched

- `app.py` — `POST /api/lead` endpoint + helpers (`_summarize_for_lead`, `_append_lead`, `_telegram_notify`)
- `index.html` — CTA banner, lead modal, JS submit handler
- `.env` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `LEADS_PATH`
- `/app/data/leads.jsonl` (VPS) — append-only log of every captured lead

## Kill switch

Tear it down (or pause it) if:

- **Junk/spam rate >50%** — needs a captcha or rate-limit before re-enabling.
- **Telegram delivery proves unreliable** (Hermes bot rate-limits, etc.) — alternate destination needed.
- **Conversion rate <5%** after ≥30 captures — the CTA copy/timing isn't pulling intent; rethink before re-shipping.

Per the Kill Switch principle: don't keep it running because "we spent time on it."

## Method spec

See `decisions/log.md` entry **2026-05-24 — /level-up Method spec: demo→lead capture on the live simulator** for the full 3Ms scope: constraint, EAD, process map, autonomy, KPI.

## What's deferred (future `/level-up` candidates)

- Auto-sync VPS → `outreach/leads.md` (Phase 2 of this skill)
- AI-drafted personalised follow-up referencing the prospect's actual demo conversation
- Simulator analytics (visit count, message-count per visit, conversion funnel)
- Dedicated `@LeadsBot` if same-thread Telegram traffic gets noisy

---

> *Adapted from The Three Ms of AI™ © 2026 Nate Herk. All rights reserved.*
