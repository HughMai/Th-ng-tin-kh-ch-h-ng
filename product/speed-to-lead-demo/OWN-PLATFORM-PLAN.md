# Own Speed-to-Lead Platform — Source of Truth

> **This is the North Star doc for building Hughie's own speed-to-lead platform.**
> Everything we build references back to this. Supersedes the GHL delivery path
> (decision 2026-06-22 in `decisions/log.md`).

## The goal

Own the speed-to-lead stack end-to-end: a **self-hosted** engine that does
**missed-call → AI text-back → qualify → book/escalate**, where each client's
workflow is **built and edited by describing it to an AI** (not by clicking a
builder), running on Hughie's own VPS — **no GHL subscription**.

Three driving reasons:
1. **No subscription fee** — pay only metered usage (Twilio + LLM), no platform floor.
2. **Owned end-to-end** — clients sign up into Hughie's own system; no third-party app/mobile dependency.
3. **Config by talking to AI** — describe a workflow in plain English, the AI writes/edits it. This is the moat GHL can't offer.

> **Honest cost note:** "no external fees" is not literally achievable — telephony
> (Twilio) and the LLM API are irreducible *usage* costs. What we kill is GHL's
> ~$297/mo subscription floor and the commodity-reseller problem.

## What already exists (Phase 0 — ~80% done)

The core is built and working in `product/speed-to-lead-demo/`:

| File | Role | Status |
|---|---|---|
| `workflow.py` | AI engine: qualify→triage→book/escalate, structured output, prompt caching, 2-nudge follow-up, honest disclosure, **no-quote guardrail** (line 123) | ✅ |
| `app.py` | FastAPI: Twilio voice→missed-call→text-back→engine→alerts (signature-verified, fail-open) + web simulator + lead capture | ✅ |
| `store.py` | SQLite (WAL) conversation + lead persistence | ✅ |
| `twilio_io.py` | SMS send + webhook signature verify | ✅ |
| Dockerfile / docker-compose / Caddyfile | Deploy + auto-TLS | ✅ |
| knowledge.md / evals.md | Editable domain knowledge + eval suite | ✅ |

**Key wins already in hand:** the no-quote guardrail and the missed-call→text-back
flow — the two things the GHL build got stuck on — already run here.

## Vertical: multi-home-service, NOT electrician-only
The product is **not** sparky-specific. Electrician is the reference tenant; the
engine is built to serve any home-service trade (plumbers, HVAC, locksmiths,
cleaners, etc.) by swapping the tenant config. "Describe the business → it's
configured" is a core requirement, not a nice-to-have.

## Product requirements (Hughie, 2026-06-22)
Four capabilities the platform must deliver, mapped to phases:

1. **One-step client onboarding from a number** — give the system a client's
   details + number, it provisions a Twilio number and wires the
   missed-call→text-back. Wiring is scriptable; **buying the Twilio number + AU
   A2P SMS registration is an irreducible manual/regulatory step**. Telephony
   model is **Conditional Call Forwarding** (client keeps their number, forwards
   misses), per `references/ccf-setup-au.md` — *not* porting. → **Phase 3 + 4**
2. **Any home service, configured by describing the business** → **Phase 1 (done) + 2**
3. **Per-tenant quoting policy** — quoting is a *config choice*, not a hard law:
   - `never` — refer all pricing to the human (current default; lowest liability)
   - `ranges` — AI gives ballpark ranges, human confirms
   - `full` — AI quotes firm prices from a config'd price list
   Each client picks their own risk level (note it in their contract). → **Phase 2**
4. **Easy Google Calendar connect** — one-click OAuth during onboarding; engine
   books into the client's existing calendar. → **Phase 3**

## The phases

| Phase | Deliverable | Why this order | Status |
|---|---|---|---|
| **0** | Single-tenant engine + telephony + persistence + deploy | Foundation (already built) | ✅ Done |
| **1** | **Externalize the workflow into per-tenant config** — pull business name, owner, trade, service area, opener SMS out of the hardcoded prompt into a config file the engine reads | Unlocks BOTH multi-tenancy and AI-editing. The keystone refactor. | ✅ Done (2026-06-22) |
| **2** | **AI workflow-builder** ⭐ — `build_tenant.py`: describe a business in English → Claude writes a full validated tenant config (identity, trade-specific triage/examples, quoting policy) → saved. Engine is now genuinely trade-neutral (all trade content is per-tenant config). | Reason #3, the moat + req #2/#3 | ✅ Done (2026-06-22) |
| **3a** | **Multi-tenant telephony** ✅ — inbound calls/SMS route by `To` number → tenant (`tenants.find_by_number`); engine runs per-tenant (`run_turn(system=...)`, cached); store keyed by `(tenant_id, phone)`; `provision_number.py` assigns a number + wires webhooks | Req #1 — a tenant can receive calls | ✅ Done (2026-06-22) |
| **3b** | **Google Calendar booking** ✅ — `connect_calendar.py` (one-time OAuth → per-tenant refresh token); engine returns ISO `booking_start`/`booking_end`; `app.py` creates the event in the tenant's calendar on a confirmed booking (best-effort, fail-open via `gcal.py`) | Req #4 | ✅ Done (2026-06-22) |
| **3c** | **Owner dashboard** ✅ — read-only operator console (`/dashboard`, token-gated): tenants + lead counts → per-tenant pipeline grouped by stage → conversation transcript drill-in. Server-rendered, XSS-safe. | Replaces the core of GHL's app | ✅ Done (2026-06-22) |
| **4a** | **Client accounts + login** ✅ — operator-provisioned: `create_account.py` makes a client login; `/login` sets a signed session cookie; each client sees ONLY their own tenant dashboard (admin sees all). Passwords pbkdf2-hashed in gitignored `data/accounts.json`. | Client logins (reverses "logs into nothing") | ✅ Done (2026-06-23) |
| **4b** | **Public self-serve signup** — a signup form + payment that provisions a tenant without operator involvement | Scale play, not needed for client #1–N | ⬜ (deferred) |

> **Model change (2026-06-23):** moved from "managed service, client logs into nothing" to **client logins** — each client logs into their own scoped dashboard with operator-provisioned credentials. Signup stays operator-driven (you create the account); public self-serve is deferred.

## Tenant config will grow to include
Beyond Phase 1's identity fields: `quoting_policy` (+ optional price list),
`owner_mobile`, `twilio_number`, `google_calendar` (OAuth token + calendar id),
booking windows / business hours.

## Refinements noted from the 2026-06-22 smoke test
- **`full` quoting needs a per-tenant price list** to quote firm numbers. Without one it safely defers to the owner (verified). Add `price_list` to tenant config when a `full` client exists.
- ~~Callback alert timing~~ ✅ FIXED 2026-06-23 — alerts (NEW LEAD on first message, BOOKED on window confirm, CALLBACK on request) now fire immediately on the same turn, without waiting to collect a name. Edge cases verified: spam first-message stays silent (not-a-job), emergency first-message fires emergency not new_lead.

## Known gaps / irreducible manual steps
- **Booking is conversational only** today — real Google Calendar booking is Phase 3.
- **Buying a Twilio number + AU A2P SMS registration** can't be automated away (regulatory, ~days). Everything around it is scriptable.
- **The no-quote guardrail is currently hardcoded** in `workflow.py` — Phase 2 moves it behind `quoting_policy`.
- `ELECTRICIAN_MOBILE` / `TWILIO_NUMBER` are still env-level (single tenant); move to per-tenant config in Phase 3.

## Phase 1 definition of done — ✅ ALL MET (2026-06-22)
- ✅ `tenants/dave.json` holds the per-client identity fields.
- ✅ `tenants.py` loads + validates a tenant; `workflow.py` `build_system(tenant)` renders the prompt from config (`render_for_tenant`).
- ✅ Rendering the `dave` tenant is **byte-identical** to the original prompt — verified programmatically (`rendered == SYSTEM_PROMPT`). Zero behaviour change.
- ✅ `OPENER_SMS` now comes from tenant config (`app.py`), not a hardcoded constant.
- ✅ No new dependencies (stdlib JSON); simulator + Twilio paths unchanged.
- ✅ Multi-tenant proven: swapping in a plumber config re-renders all identity terms.

**Files:** `tenants/dave.json`, `tenants.py` (new); `workflow.py`, `app.py` (edited).

## Next: Phase 2 — AI workflow-builder
Now that a workflow = a tenant config file, build the chat that turns *"text them back, wait 5 min, escalate emergencies to me"* into edits on that config, validated and hot-reloaded.
