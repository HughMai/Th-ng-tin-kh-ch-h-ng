# Decisions Log

Append-only record of meaningful decisions and why they were made. `/level-up` Phase 2 (Method interview) writes scoped automation specs here. You can also append manually whenever you decide something worth remembering.

**Format per entry:**

```
## YYYY-MM-DD — Short title

**Decision:** what was decided.

**Why:** the reasoning, constraints, and what would change your mind.

**Alternatives considered:** what else was on the table.

**Owner:** who's accountable.
```

Keep it terse. Future-you will thank present-you for capturing the *why*, not just the *what*.

---

## 2026-05-16 — Hermes Agent on Hostinger VPS via OpenRouter

**Decision:** Run Hermes Agent (Nous Research) in a Hostinger Docker container, using OpenRouter as the LLM provider, with Telegram (`@BaoBei09bot`) as the messaging interface.

**Why:** Hermes ships with the learning loop (memory, skills, GEPA) needed for a personal AIOS. Hostinger gives 24/7 uptime without managing infra. OpenRouter chosen over direct Anthropic API because Hermes's Anthropic provider sends OpenAI-format requests to Anthropic's URL (platform bug as of May 2026) — OpenRouter natively accepts that format and proxies to Claude.

**Alternatives considered:**
- Direct Anthropic API → broken in Hermes (400 errors)
- Custom Python bot using Anthropic SDK directly → simpler but loses the Hermes learning loop, skills hub, and built-in tooling
- Claude OAuth token (uses subscription) → not tested, may have same provider bug

**Lessons (file at `references/hermes-setup.md`):**
1. Always run `chown hermes:hermes` on `/opt/data/*.yaml|.env|.json` after editing as root — gateway runs as `hermes` user and silently falls back to defaults if it can't read configs
2. Configure providers via `./hermes setup` wizard, not piecemeal `config set` — wizard populates required `base_url` and `api_mode` fields
3. First debug step when bot doesn't respond: `tail /opt/data/logs/gateway.log` for `Permission denied`

**Owner:** Hughie

---

## 2026-05-21 — Build speed-to-lead product before ads or clients (dental, email, named persona)

**Decision:** Build the dental follow-up sequence as the v1 product before running any ads or pitching any clients. Locked specs: dental vertical, email-first channel, named human persona ("Sarah" / front-desk coordinator).

**Why:** A working sequence IS the demo and the unfair advantage. Selling lead gen without a real conversion differentiator puts us in a commoditized market against established agencies. The speed-to-lead edge — instant response, branching that sounds human, clean escalation — is the moat. 2–4 weeks building beats 2–4 weeks pitching a deck nothing backs.

Channel pick (email): cheapest infra to stand up (no Twilio + TCPA), full deliverability control, no carrier compliance. SMS is Phase 2 once the core funnel proves out.

Vertical pick (dental): less marketing-savvy owners than med spa = AI feels more magical to them = easier first sale. Longer LTV than real estate. The $99 special is an established hook in the space. Real estate skipped — too crowded with lead-gen vendors.

Persona pick (named human): converts meaningfully better than disclosed-AI. Mitigation: bot answers honestly when directly asked + always offers human handoff = ethically defensible. Never claims to be human, just leads with a human name.

**Alternatives considered:**
- Sell first, build second → high paralysis risk; nothing to demo; commoditized pitch.
- SMS-first → faster reply latency but higher infra + TCPA cost. Defer to Phase 2.
- Vertical-agnostic v1 → diffuses focus, harder to nail tone, offer hook, and escalation rules.
- Disclosed-AI persona → easier ethically but kills the conversion edge that is the whole point of v1.

**Spec file:** `references/dental-speed-to-lead-sequence.md`

**Open questions tracked in spec file** (persona name policy, calendar source of truth, CRM destination, escalation channel, send domain, offer hook variants) — answer these before infrastructure build.

**Owner:** Hughie

---

## 2026-05-22 — Pivot to trades; v1 product = electrician speed-to-lead (supersedes 2026-05-21)

**Decision:** Pivot the target market from dental / med spa / real estate to **trades**, and lock the v1 product as a **speed-to-lead workflow for electricians**. Same GetLeadMate-style mechanic (instant reply → qualify → quote → book, 24/7), but rebuilt **SMS-first with missed-call-text-back** instead of email. Supersedes the 2026-05-21 dental decision in full.

**Why:**
- *Market:* Construction is Wollongong's largest industry — 3,185 businesses, 19.6% of all businesses (ABS, FY2025), mostly small "Construction Services" operators — the exact solo-operator buyer. `priorities.md` already flagged "pick trades or healthcare."
- *Vertical (electricians):* an emergency-driven trade — the customer rings 2–3 businesses and the first to respond wins, so speed-to-lead *is* the product; no education needed to sell it. The owner is on the tools and structurally can't answer the phone — undeniable pain, one-line ROI.
- *Channel (SMS / missed-call-text-back, not email):* tradies and their customers live on the phone, not the inbox. Missed-call-text-back is the headline feature. GetLeadMate is Messenger-first — this is the wedge.
- *GetLeadMate model:* a vertical-locked, templated, fast-deploy speed-to-lead product already shipping (getleadmate.com.au) — proof the mechanic works. They have zero customer proof after ~6 months; v1's job is one real local before/after stat to out-credential them.

**What changed from the dental decision:** vertical dental → electricians; channel email-first → SMS / missed-call-text-back; shape 14-day 5-touch drip → fast first-5-minutes conversation. **Kept:** build-before-sell, named-human persona with honest disclosure, one local client for proof, clonable per-client template.

**Alternatives considered:**
- *Plumbers* — near-identical fit; electricians taken as the recommended default (marginally higher small-fault call volume and callout ticket). Product is ~90% portable to plumbers as client #2.
- *Stay broad ("trades") for v1* — rejected; the product needs one frozen vertical to nail tone, job types, and escalation logic.
- *Keep dental* — rejected; market data and emergency-trade speed-to-lead fit are stronger.

**Spec file:** `references/electrician-speed-to-lead-workflow.md`

**Superseded:** the dental spec moved to `archives/dental-speed-to-lead-sequence.md` (kept as a template reference, not deleted).

**Open questions tracked in spec file:** founding electrician, phone setup (forward vs. port), after-hours policy, service area, callout-fee disclosure, calendar choice, quote-photo capture.

**Owner:** Hughie

---

## 2026-05-22 — Production telephony: port the electrician's number (not call-forwarding)

**Decision:** For the production build, **port the founding electrician's existing business number** onto the programmable telephony provider (Twilio), rather than conditional call-forwarding to a separate number. Resolves open question #2 in `electrician-speed-to-lead-workflow.md`.

**Why:** Missed-call-text-back needs programmable control of the number — a raw consumer SIM exposes no missed-call webhook and no API SMS, so a provider has to be in the loop regardless. Porting keeps the digits identical: the customer dials, and is texted back from, the electrician's own advertised number — the automation stays invisible, which protects the named-human persona and trust. Call-forwarding leaves the text-back coming from a second, unfamiliar number and, depending on the carrier, can strip the original caller's ID from forwarded calls — and with no caller number there is no one to text back. Cost is trivial either way; UX integrity and reliability decide it. What would change this: if porting the founding electrician's number proves slow or blocked (number locked to a contract), use call-forwarding for the pilot and port later.

**Alternatives considered:**
- *Conditional call-forwarding to a Twilio number* — faster to stand up, no porting wait, but the text-back comes from a different number and forwarded-call caller-ID is unreliable. Kept only as a fallback.
- *A brand-new Twilio number as the business number* — rejected; discards the electrician's existing number equity (trucks, cards, Google listing).
- *Raw consumer SIM, no provider* — not possible; consumer mobile plans expose no missed-call or SMS API.

**Spec file:** `references/electrician-speed-to-lead-workflow.md` (Stage 1 + build stack updated to porting).

**Owner:** Hughie

---

## 2026-05-22 — /outreach agent: warm-lead follow-up engine (level-up scope)

**Decision:** Build `/outreach`, an AI-assisted skill that drafts voice-matched follow-up/outreach messages for warm leads tracked in `outreach/leads.md`. Autonomy L2 — the skill drafts, Hughie reviews and sends by hand; it never sends on its own. Week-1 scope is the message-drafter + lead table only.

**Method spec (3Ms / `/level-up`):**
1. *Constraint:* warm leads from in-person events and referrals go cold — follow-up is ad-hoc. Serves priority #2 (land first client).
2. *EAD:* Automate — not eliminate (following up warm leads is the highest-ROI sales act), not delegate (solo, his voice and relationships). 60/30/10 split.
3. *Process:* trigger = manual `/outreach` run; sources = `leads.md` + `references/voice.md` + `outreach/style.md`; transform = lead context → voice-matched draft; decisions = who's due + message type (first / follow-up / break-up) + channel; destination = drafts → manual send → status update.
4. *Autonomy:* L2 Drafted.
5. *KPI:* Bucket = More customers. Metric = reply rate (replies ÷ messages sent).

**Machine:** AI-assisted skill — deterministic can't voice-match, a sub-agent is overkill. Ships at Bike Method Phase 1 (manual trigger, manual send, manual status update). Message format + persona slotted into `outreach/style.md` for Hughie to fill in later; until then the skill falls back to `references/voice.md`.

**Why:** The bottleneck for client #1 is follow-up discipline, not lead-finding — `priorities.md` says the channel is in-person and referral. Reframed from a cold "Outreach.io clone" to a warm-lead follow-up engine so the build matches the real channel. Boring-is-beautiful: markdown lead table, one AI call, zero infra.

**Deferred** (future `/level-up` runs): Gmail reply auto-detection, follow-up scheduling/reminders, the Instagram automation leg, lead table → Notion.

**Alternatives considered:**
- *Full cold-email Outreach.io clone* (scraping, sequencer, mass send) — rejected; wrong channel, deliverability risk, scope feeds decision paralysis.
- *Buy Instantly / Smartlead* — viable for pure email volume, but doesn't fit warm or IG follow-up and skips the agent-pattern learning.
- *L3 auto-send* — rejected for first build; sender-reputation and off-tone risk before L2 is proven.
- *Lead table in Notion* — deferred; markdown is zero-infra for week 1.

**Artifact:** `.claude/skills/outreach/SKILL.md` (+ `outreach/leads.md`, `outreach/style.md`)

**Owner:** Hughie

---

## 2026-05-22 — v1 captures outcome-labeled lead data (two-layer visibility)

**Decision:** The electrician speed-to-lead product captures **outcome-labeled lead data** from v1 — per lead: source, time, request, full transcript, booked/not-booked (and why), job value, repeat-customer status. Visibility splits into two layers:
- *Raw layer (operator-only):* full transcripts, per-call failure diagnostics, and cross-client pooled patterns. This is the compounding asset and never leaves Hughie.
- *Client outcome report (trades see this):* a monthly per-client summary — leads captured, jobs booked, job value handled, response time, missed-call recovery. Their own numbers only; never another client's data and never the pooled patterns.

**Why:** A thin LLM wrapper has no moat (Hormozi's point) — but a service business's moat is distribution, trust, and switching cost, not data. The genuinely defensible asset over 12–18 months is *outcome-labeled* data: most competitors capture the conversation, few capture the booked/not + revenue outcome that makes it predictive. It's nearly free to instrument now and expensive to backfill later. Clients must see the outcome report because it's the ROI receipt — it makes value visible, which is what prevents churn and doubles as a sales asset for the next prospect. The split protects the moat (pooled data stays operator-side) while keeping clients honest-numbers transparent. Constraint: capture must be a byproduct of booking jobs, not a separate data-platform build. What would change this: if a client contractually demands a full data export, treat their leads/customers/job-values as their data (it is) but keep the labeled/pooled derived layer as Hughie's.

**Alternatives considered:**
- *Operator-only, no client visibility* — rejected; value goes invisible, client forgets why they pay, churn risk.
- *Full data/dashboard access for clients* — rejected for v1; hands over the moat and over-scopes into a data-platform build. Dashboard is a later decision, only if clients ask.
- *Capture conversation only, skip outcome labels* — rejected; the outcome label is the entire differentiator.

**Spec file:** to fold into `references/electrician-speed-to-lead-workflow.md`. Client report ships in v1 as a dead-simple generated summary, not a dashboard.

**Owner:** Hughie

---

## 2026-05-22 — Telephony failover: fail open to the electrician's mobile

**Decision:** The ported business number must **fail open** — if any part of the automation stack is unreachable, an inbound call still rings the electrician's mobile exactly like an ordinary call. A broken automation degrades to a normal phone; it never degrades to dead air.

Mechanism:
- *Voice path independent of the orchestrator.* The basic "ring the mobile" instruction is a static Twilio TwiML Bin (Twilio-hosted, zero external dependency), wired as the number's **fallback voice URL**. The smart routing (missed-call detection, n8n) is the *primary* handler; if it errors or times out, Twilio falls through to the static Bin and the call connects. The call ringing through never depends on n8n being up.
- *Smart layer is allowed to degrade.* If n8n or the Claude API is down, the worst case is no text-back — an ordinary missed call, the exact pre-product status quo. Acceptable, not a catastrophe.
- *First text-back is a static template.* The opening "sorry I missed you, Dave will call back" SMS uses a fixed template, so a Claude outage still sends it — only the follow-up *conversation* needs the LLM.
- *Health alerting.* An uptime check on n8n pings Hughie when the stack is degraded, so he learns it from a monitor, not a client complaint.

**Why:** Porting the client's number (decision 2026-05-22) makes Hughie a single point of failure in a business whose livelihood is inbound calls. An automation outage that drops calls would lose real jobs, destroy trust, and kill the pilot's before/after proof. The cost of fail-open is trivial (TwiML Bins are free); the cost of fail-closed is the whole client relationship. What would change this: at scale, a second telephony provider for true number-provider redundancy — over-engineering for v1.

**Alternatives considered:**
- *No failover (automation down = phone down)* — rejected; catastrophic for a client whose income is inbound calls, and it would sink the pilot.
- *Full multi-provider telephony redundancy (second number provider)* — rejected for v1; over-engineering. Twilio's own uptime is high; a Twilio-level outage is the rare residual risk, accepted for now.
- *Failover on the SMS/text-back path only* — insufficient; the critical path is the voice call *connecting*, not the text.

**Spec file:** verified in the QA stage of the delivery pipeline — `references/electrician-speed-to-lead-workflow.md`.

**Owner:** Hughie

---

## 2026-05-22 — Hosting: one multi-tenant n8n on a Hostinger VPS; maintenance by monitoring

**Decision:** The speed-to-lead system runs as **one self-hosted n8n instance on a single Hostinger VPS** (Docker), multi-tenant — every client is a cloned workflow with their own config on the same instance. Not per-client VPSes. Everything else is SaaS in other providers' clouds (Twilio, Claude API, Google Calendar, Airtable); the n8n orchestrator is the only box Hughie hosts.

Account ownership: the **Twilio account is Hughie's**, one sub-account per client — so a churned client can't lock him out of numbers he operates. Counterweight: the number stays the client's business asset; the contract must commit to porting it back out if they leave. The raw/pooled data layer stays operator-only (per outcome-capture decision); each client gets a shared view of their own Airtable base.

Maintenance after handoff is **monitoring, not babysitting** — every failure must announce itself so Hughie responds to alerts instead of logging in to check:
- *Synthetic canary (highest leverage)* — a scheduled job runs a fake lead through the whole pipeline every few hours and alerts on failure; the QA harness promoted to run forever in production. Catches the silent breakages (expired Google OAuth token, lapsed Twilio card, exhausted Claude credits, config-permission slips) before a customer does.
- *Uptime monitor* — UptimeRobot on an n8n health endpoint.
- *Conversation quality* — weekly spot-check of the raw data layer; graduates to an agentic conversation monitor (review exceptions, not every transcript) past ~10 clients.
- *Patching* — n8n pinned to a known-good version, updates tested on the dev workflow first; Hermes config/permission lessons apply.

**Why:** One VPS is a flat ~$5–15/mo regardless of client count, reuses the Hostinger + Docker muscle already built for Hermes, and minimises boxes to maintain — which is what "run solo" requires. Per-client VPSes scale both cost and patching load linearly. The multi-tenant single-instance risk is bounded by the failover decision: if the VPS dies, calls still ring through — every client degrades to an ordinary missed call, not dead air. The silent failures, not the loud crashes, are what lose clients, so the canary is the load-bearing maintenance mechanism. What would change this: a client big or sensitive enough that shared-instance risk is unacceptable, or hiring help that makes more infra manageable — revisit then, not before.

**Alternatives considered:**
- *Per-client isolated VPS/instance* — rejected for v1; maximum isolation but cost and ops load scale linearly with clients, fighting the solo constraint. Reconsider only at a large or compliance-sensitive client.
- *n8n Cloud (managed SaaS)* — rejected; no server to patch, but higher per-month cost at multi-client scale and less control; the self-hosted box is already a known quantity.
- *Client holds the Twilio account* — rejected; hands number control to the client and creates lock-out risk if the relationship sours.

**Spec file:** to fold into `references/electrician-speed-to-lead-workflow.md` (hosting note + canary in the delivery pipeline's steady-state stage).

**Owner:** Hughie

---

## 2026-05-22 — Drop n8n: production layer extends the FastAPI engine directly

**Decision:** The Twilio production layer is built by **extending the existing FastAPI conversation engine** (`product/speed-to-lead-demo/`) with Twilio webhook endpoints, SQLite conversation-state persistence, and escalation-SMS routing. **No n8n.** Supersedes the n8n orchestration choice in the build-stack table of the electrician spec and the "one n8n instance" element of the 2026-05-22 hosting decision. The single Hostinger VPS, Docker deployment, and the fail-open telephony decision all still stand.

**Why:** A working, production-quality conversation engine already exists in Python — `workflow.py` runs the full qualify→triage→book/escalate workflow with structured Pydantic output and prompt caching. Building "in n8n" would mean either rebuilding that engine as n8n nodes (discards working code; n8n is weaker at structured-LLM logic) or running n8n purely as Twilio glue — a second always-on service to deploy, patch, and monitor for ~100 lines of work. Adding the glue to the FastAPI service that already exists is smaller and leaves one service, not two — the "run solo / minimize boxes" principle the hosting decision is built on. n8n's real value (visual, clonable, non-coder-maintainable workflows) is an agency-scale benefit; with zero clients and a capable coder it is premature. SQLite (a file, no extra service) replaces the planned Postgres for v1; Postgres stays the upgrade path. What would change this: client volume large enough that visual, delegate-able workflow management is worth a second service — revisit n8n then.

**Alternatives considered:**
- *n8n as glue calling the Python engine* — rejected; a second always-on service for a job Python does in ~100 lines, against the minimize-boxes principle.
- *Rebuild the engine as n8n nodes* — rejected outright; discards working code and moves structured-LLM logic into a weaker tool.

**Affects:** `product/speed-to-lead-demo/` — Twilio endpoints, SQLite store, escalation routing added. The build-stack table in `references/electrician-speed-to-lead-workflow.md` still says n8n — to be corrected (n8n → FastAPI) with the other pending spec folds.

**Owner:** Hughie

---

## 2026-05-24 — Business entity: sole trader under personal name, GST deferred

**Decision:** Trade as a **sole trader** under Hughie's personal name. Apply for an ABN immediately (free, instant via abr.gov.au). **Do not register for GST** until turnover hits the $75k/yr threshold. **Do not register a business name** until a trading-as name is actually needed. Revisit the Pty Ltd question only when liability exposure or revenue warrants it.

**Why:** Sole trader is the cheapest, simplest entity to stand up — zero setup cost, no annual ASIC fees, profits flow straight onto the personal tax return. The job at v1 is landing the first paying client, not optimising for tax structures that only matter post-revenue. GST registration below $75k is optional admin overhead with no upside for a service business selling to GST-registered SMBs (who don't care either way pre-threshold). A Pty Ltd structure adds ~$500 setup + ~$310/yr ASIC fee + accountant complexity, and its main benefit (limited liability) is largely covered by Professional Indemnity + Public Liability insurance at this stage. What would change this: hitting $75k turnover (forces GST), signing a client whose contract requires Pty Ltd, or taking on enough risk/staff that personal liability becomes material.

**Alternatives considered:**
- *Pty Ltd from day 1* — rejected; ~$500 setup, ~$310/yr ASIC, separate tax return, accountant overhead. Liability protection real but premature; insurance covers the early-stage risk.
- *Register for GST voluntarily* — rejected; adds BAS lodgement every quarter for zero net benefit until $75k. Trades clients don't care about GST status pre-threshold.
- *Register a business name now* — deferred; only needed if trading as something other than "Hugh Mai." Decide at branding time, not before.

**Next actions:** Apply for ABN at [abr.gov.au](https://abr.gov.au); open a business transaction account; get Professional Indemnity + Public Liability quote via BizCover before first signed client.

**Owner:** Hughie

---

## 2026-05-24 — AU carriers silently filter human-sounding outbound SMS from US long-codes

**Decision:** Treat outbound SMS from the US demo number `+19129785856` to AU mobiles as **unreliable for conversational/persona-matched content** and reliable only for terse system messages (test pings, internal alerts). Use the web simulator (`https://stl.187-77-133-39.sslip.io/`) for all engine iteration; reserve real SMS tests for smoke-checking the wire. Real AU-phone tests of the engine's actual replies are deferred until production replaces the US demo number with a ported AU number (per the 2026-05-22 porting decision).

**Why:** Empirical evidence from slice 1 deploy day — four outbound messages from the same Twilio US number to the same AU mobile (+61402129328) in one afternoon, all with Twilio `status=sent` and **no Twilio-side error**:

| # | Body | Style | Delivered |
|---|---|---|---|
| 1 | "Speed-to-lead deploy test 2 — full account check…" | Generic test | ✅ |
| 2 | "NEW LEAD — Power out. Still qualifying…" | Terse internal alert | ✅ |
| 3 | "Hey, no worries — I'll get Dave onto it. Quick check…" | Engine reply, persona-matched | ❌ |
| 4 | "Hi, it's Dave from Dave's Electrical — sorry I missed your call…" | Static opener, persona-matched | ❌ |

Pattern fits AU carriers' tightened anti-foreign-long-code filtering (active since 2024 under ACMA's anti-scam codes): system-style messages pass; marketing/conversational text from a US long code to an AU mobile is silently dropped at the carrier with no upstream error. The technical pipeline (app → Twilio API → carrier accept) is **fully proven**; the carrier-to-handset leg is the unreliable bit, and only for this specific US-long-code → AU-mobile configuration.

This is a **testing limitation, not a product limitation**: in production the SMS originates from a *ported AU number* (decision 2026-05-22), which is AU-to-AU and not filtered. The go-to-market path is unchanged — only how slice 1 is exercised on the operator's own AU phone.

**Alternatives considered:**
- *AU Sender ID registration on the US number* — weeks of paperwork and additional cost, throwaway work the moment a real client's AU number is ported. Rejected.
- *Buy an AU number for testing now* — Twilio AU local numbers need a regulatory bundle (ID, address proof, ABN for mobile-class numbers) and 1–5 days approval. Throwaway given the production plan. Rejected.
- *Test conversational delivery with a US-mobile recipient (friend / burner)* — would prove the carrier-leg works for US-to-US; useful as a one-off sanity check, not a daily workflow.

**Implication for the demo / sales path:** the **web simulator IS the demo** for prospect pitches — same engine, same prompts, zero per-message cost, zero carrier risk. Real SMS is a smoke test, not a workflow.

**Owner:** Hughie

---

## 2026-05-24 — Speed-to-lead deploy lessons (env_file, image rebuild, ZeroSSL, TwiML Bin, Hermes env)

**Decision:** Capture five operational facts surfaced during real deploys (slice 1 plus the lead-capture layer) so future client onboardings (or a fresh deploy) don't relitigate them. Each is a 1-line runbook pin that saves a 30-minute debug cycle.

**Lessons:**

1. **`docker compose restart` does NOT reload `env_file`.** Restarting a container preserves its existing env; `.env` changes only take effect on container *recreation*. The right command after editing `.env` is `docker compose up -d` (or `--force-recreate <service>` for an explicit reload). Slice 1 lost ~30 minutes debugging "why is Twilio signature validation failing?" because the running container still had the placeholder `your-auth-token` after a `restart`. Pin to the per-client runbook.

2. **`docker compose up -d` does NOT rebuild the image.** Recreates the container with the current `.env` (fixing lesson 1's gap) but reuses the existing image. After **code** changes, use `docker compose up -d --build`. The two flags pair: `--force-recreate` for env-only changes, `--build` for code changes (which implies recreate). Hit during the lead-capture deploy when `POST /api/lead` returned 404 because the running image was stale.

3. **`sslip.io` is Let's Encrypt rate-limited.** Caddy's first cert request hit HTTP 429 ("too many certificates issued for sslip.io in the last 168h") because LE treats sslip.io as one registered domain and the shared community blows through the per-domain limit. Caddy fell back to LE *staging* (works but staging certs aren't publicly trusted → TLS internal-error alert in clients). Fix: set a global `email` directive in the Caddyfile, which engages Caddy's automatic **ZeroSSL** fallback (separate ACME CA, no shared rate limit). ZeroSSL is now the de-facto cert provider for `stl.187-77-133-39.sslip.io`. A real registered domain wouldn't hit this — sslip.io is the only place it bites.

4. **Twilio TwiML Bins are Console-only.** No REST API to create them — clicked through in 2 minutes. Wiring the Bin's URL as a number's Voice **Fallback URL** *can* be done via API and was. Per-client runbook: TwiML Bin = manual click; wiring = API.

5. **Hermes' `.env` is inside the container, not on the host.** The `references/hermes-setup.md` notes describe paths like `/opt/data/.env` — those paths are *inside* the `hermes-agent-…` container, not at `/opt/data/` on the host VPS. To pull a value (e.g., for reuse between containers): `docker exec hermes-agent-5c1k-hermes-agent-1 sh -c 'grep ^KEY= /opt/data/.env'`. Hit during the lead-capture deploy when pulling Hermes' `TELEGRAM_BOT_TOKEN` to reuse `@BaoBei09bot` for `[NEW LEAD]` notifications.

**Why log this:** all five surfaced during a real deploy and all five will recur for every future deploy. Each is a 1-sentence pin to the runbook.

**Spec file:** to fold into a "Deploy lessons" section of `references/electrician-speed-to-lead-workflow.md` alongside the existing delivery pipeline.

**Owner:** Hughie

---

## 2026-05-24 — /level-up Method spec: demo→lead capture on the live simulator

**Decision:** Add a deterministic lead-capture surface to the live speed-to-lead simulator (`https://stl.187-77-133-39.sslip.io/`). After a prospect exchanges 2+ messages with the demo, a CTA banner surfaces asking for name + business + contact. Submission writes a lead record to `/app/data/leads.jsonl` on the VPS and pings Hughie's Telegram via the existing `@BaoBei09bot` (Hermes bot reused for notifications, `[NEW LEAD]` prefix differentiates from Hermes traffic). Hughie manually transfers each captured lead from Telegram into `outreach/leads.md` to enter the `/outreach` follow-up pipeline. Autonomy **L0 throughout** — no AI in the capture loop.

**Method spec (3Ms / `/level-up`):**
1. *Constraint:* Without a capture surface, prospects sent to the simulator URL (Chamber events, in-person follow-ups, link-in-bio) vanish without a follow-up vector. Serves priority #2 (land first paying client).
2. *EAD:* **Automate**. Eliminate rejected (URL goes to people whose contact isn't already on hand); delegate doesn't fit (small deterministic surface). 60/30/10 split: capture loop is ~100% deterministic; AI-drafted follow-up belongs in `/outreach`.
3. *Process map:*
   - **Trigger:** prospect engages simulator → after 2nd customer message, CTA banner surfaces
   - **Sources:** form fields (name, business, contact) + browser-held conversation thread
   - **Transformations:** bundle into a JSONL record (identity + 1-line conversation summary + UTC timestamp + message count)
   - **Decision points:** CTA timing (after 2 messages); destinations (Telegram ping + JSONL log on VPS)
   - **Destination:** VPS `/app/data/leads.jsonl` (persistent volume) + Telegram chat with Hughie via `@BaoBei09bot`
4. *Autonomy:* **L0** throughout. Pure deterministic. AI-drafted follow-up belongs in `/outreach` (separate skill, separate `/level-up` run).
5. *KPI:* Bucket = **More customers**. Metric = captured leads per week (raw count for now; conversion rates once volume exists).

**Machine:** deterministic code change to the existing live product:
- `index.html` — CTA banner (hidden until 2nd customer message) + lead modal + JS submit handler
- `app.py` — new `POST /api/lead` endpoint (writes JSONL, sends Telegram)
- `.env.example` — adds `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `LEADS_PATH`
- `LEAD-CAPTURE.md` — runbook with `bike-method-phase: 1` frontmatter + Three Ms attribution

Telegram reuses the existing `@BaoBei09bot` token (read from `/opt/data/.env`, the Hermes env). One bot, two senders — distinguished by `[NEW LEAD]` prefix.

**Bike Method Phase 1 (training wheels):** capture runs live; Hughie manually reviews each Telegram ping and copies the lead into `outreach/leads.md` before `/outreach` picks it up. After ~10 captured leads, decide whether to automate the VPS → `leads.md` sync (Phase 2).

**Why:** First `/level-up` run. Priorities playbook says months 1–3 = Chamber events + in-person → warm leads. Web simulator IS the demo (per the 2026-05-24 AU SMS filtering decision). Capture surface is the missing layer between "tries the demo" and "Hughie follows up via `/outreach`."

**Alternatives considered:**
- *Google Form linked from simulator* — easiest, but loses conversation context + breaks UX flow. Rejected.
- *Notion direct write via MCP* — cross-device access, queryable, but adds Notion API dependency + DB schema work. Rejected for slice 1; revisit after Phase 1 review.
- *Persistent or exit-intent CTA* — captures more visitors but lower intent. Rejected; intent quality > volume at this stage.
- *Build AI-drafted follow-up inline* — scope creep; `/outreach` already handles drafting, would duplicate.

**Deferred** (future `/level-up` runs):
- VPS → `outreach/leads.md` sync automation (eliminate the manual copy-paste)
- AI-drafted personalised follow-up referencing the prospect's actual demo conversation
- Analytics on the simulator (visit count, message-count per visit, conversion funnel)
- Dedicated `@LeadsBot` if same-thread Telegram traffic gets noisy

**Artifact:** code changes in `product/speed-to-lead-demo/` (index.html, app.py, .env.example) + `product/speed-to-lead-demo/LEAD-CAPTURE.md` (runbook with Bike Method Phase 1 frontmatter).

**Owner:** Hughie

> Adapted from The Three Ms of AI™ © 2026 Nate Herk.

---

## 2026-05-25 — /onboard-client skill: Stage 0 intake interview

**Decision:** Ship `/onboard-client` as a dedicated skill (`.claude/skills/onboard-client/SKILL.md`) that conducts the Stage 0 intake interview from the delivery pipeline (`references/electrician-speed-to-lead-workflow.md`). Skill is **data-collection only** — asks the questions in the right order across 8 sections (business basics, persona, service offer, numbers, hours, calendar, quote-first jobs, compliance), writes answers to `clients/<slug>/intake.md`, and produces a "what's blocking build" block listing every TBD and external dependency. **No `.env` generation, no Twilio API calls, no auto-build.** Those are Stage 1+ and remain manual in Phase 1.

**Why now (not "when client #1 signs"):** Pre-building the intake structure before the first signed client converts the discovery call from extraction to confirmation. Without it, the first intake call risks freelancing the questions, missing fields, and triggering rework. Scoped tight enough that there's no over-engineering risk — the skill is essentially a structured question list with an output contract.

**Method spec (3Ms / `/level-up`):**
1. *Constraint:* No structured per-client intake → every new client = ad-hoc discovery call, forgotten fields, post-call back-and-forth, slow time-to-build. Serves priority #2 (land + ship first paying client cleanly).
2. *EAD:* **Automate**. Eliminate rejected (intake is non-skippable per the delivery pipeline). Delegate doesn't fit (Hughie does the call; the skill structures the capture). Mostly deterministic (~90%) — just ordered Q&A → markdown file. AI's only role is conversational pacing.
3. *Process map:*
   - **Trigger:** new signed client → Hughie invokes `/onboard-client`
   - **Sources:** answers from Hughie's discovery call (typed/pasted), workflow spec defaults (callout-fee-disclose default, 3h window default, etc.)
   - **Transformations:** structured into 8-section markdown + "what's blocking build" block
   - **Decision points:** TBD-vs-answered (skill doesn't guess); license-held check (blocks intake if no)
   - **Destination:** `clients/<slug>/intake.md` + one-line entry in `decisions/log.md`
4. *Autonomy:* **L1 — Suggested.** Skill asks; Hughie answers, decides, and does every downstream step manually. L0 would be a static template (rejected — loses ordering + completeness check). L2+ would auto-generate `.env` (deferred to Phase 2 after ≥2 manual intakes).
5. *KPI:* Bucket = **Less cost** (per-client setup time). Metric = minutes of Hughie's time from "yes" to "intake.md complete with no TBDs." Target: <30 min. Secondary metric over time: % of fields TBD'd at first pass (proxy for discovery-call quality).

**Machine:**
- `.claude/skills/onboard-client/SKILL.md` — frontmatter (`bike-method-phase: 1`, three-ms-attribution), 8-section interview spec, output contract, deferred list
- No code, no other files. Skill writes its outputs at run-time into `clients/<slug>/` (created on first use).

**Bike Method Phase 1 (training wheels):** every captured value reviewed by Hughie; secrets and external dependencies (Twilio sub-account creation, calendar OAuth, port form submission, license verification) all manual. Phase 2 = auto-generate `.env` from intake. Advance only after ≥2 real intakes where the manual translation step felt redundant on both.

**Alternatives considered:**
- *Defer until client #1 signs (the prior queue position)* — risks freelancing the first intake; reversed because the skill is small and the structure is genuinely portable across clients regardless of which client is first.
- *Notion form / Google Form for the client to fill themselves* — better for steady-state (client #3+) but the first 2 intakes need conversational extraction; client doesn't know what fields matter. Deferred to a future Phase as "pre-call discovery questionnaire."
- *Bundle `.env` generation into the same skill* — would cross the L1→L2 line on first ship; explicitly rejected per the user's "(a) only" scope pick.
- *Multi-skill split (intake / build / port / handoff as separate skills)* — premature. Don't write the full delivery runbook before client #1 (explicit guidance in workflow spec line 181). Intake is the only stage with enough pattern certainty to skill-ify today.

**Deferred** (future `/level-up` runs):
- Phase 2: auto-generate `clients/<slug>/.env` from intake (with secrets blanked)
- Phase 2: auto-create Twilio sub-account via Twilio API
- Phase 3: pre-flight checklist generator (verifies all TBDs cleared before build)
- Pre-call discovery questionnaire the client fills before the intake call
- Client-facing intake confirmation (their copy of what was captured)

**Artifact:** `.claude/skills/onboard-client/SKILL.md` (only file created). `clients/<slug>/` directories created at run-time per intake.

**Owner:** Hughie

> Adapted from The Three Ms of AI™ © 2026 Nate Herk.

---

## 2026-05-25 — Intake run: `test-client`

Intake complete: `test-client` — status: **intake-pending-tbds** (11 items in the "What's blocking build" block: 7 TBDs, 4 verifications/clarifications, 4 external dependencies). First dry-run of `/onboard-client` skill — verifies the 8-section flow holds together and the TBD-over-guess rule fires cleanly. Artifact: `clients/test-client/intake.md`.

---

## 2026-05-25 — Pricing + service model: flat fee, managed-service (Model A)

**Decision (LOCKED):**
1. **Pricing model: flat monthly fee.** Single recurring charge per client. Rules out per-lead, per-feature tiers, and success/revenue-share.
2. **Service model: Model A (Managed).** Hughie's accounts hold VPS, Twilio master + per-client sub-account, Anthropic key. Client logs into nothing. One invoice. Failure mode: the moment a client logs into Twilio Console, the model has broken — find out what they actually needed instead.

**Decisions PROPOSED (not yet locked — Hughie's call on real numbers after client #1–3):**
- Standard tier: **$497/mo AUD** flat
- Fair-use cap: **100 inbound leads/month**, with conversation-not-surcharge above
- Setup fee: **$0 for clients #1–3** (case-study clients, lower friction wins); **$497–997 from #4 onward** once build is templated
- Cancellation: **30-day notice**, free port-out, data export within 7 days, deletion within 30 days, no pro-rata refund

**Why flat:**
- Predictable for client (no surprise bills on a busy month) AND predictable for Hughie (no lead-volume forecasting for cash flow)
- Per-lead = punishes growth + fraud-dispute exposure ("that lead was junk")
- % of revenue = unverifiable on a tradie's books
- Tiered-by-feature = decision fatigue on the sales call

**Why Model A for client #1:**
- Trades electricians don't want to manage SaaS — the entire pitch is "you keep your job, I handle the digital"
- Splitting bills breaks the managed-service framing
- Unit economics (direct cost ~$60/mo dominated by Twilio SMS, ~$440/mo gross margin at $497) make absorbing variability safe through ~3× lead surge

**Unit economics (steady state, per client, AUD/month):**
- VPS share: ~$1–2 (Hostinger box amortised across clients + Hermes)
- Anthropic API: ~$2 (Haiku 4.5, ~$0.04/conversation × ~50 leads)
- Twilio AU number rental: ~$1.50
- Twilio SMS: ~$50 (~$0.10/msg × ~10 msgs/lead × ~50 leads) ← dominant variable
- **Total recurring direct cost: ~$55–60/mo**
- Gross margin at $497 PROPOSED tier: ~$440/mo

**Risk safeguards Hughie must action before client #1:**
1. Per-sub-account Twilio daily spend cap (loop-runaway protection)
2. Anthropic API key spend limit + quarterly rotation
3. UptimeRobot free tier → `/health` every 5 min → Telegram alert
4. Failover TwiML Bin already shipped (decisions log 2026-05-23)

**Out-of-scope for this policy (deferred):**
- Client service agreement (IP, liability cap, indemnity, GST, dispute resolution) — needs lawyer or template before client #1 signs; do NOT self-draft
- Pricing revision for client #4+ — revisit when 3 clients live and SMS distribution is observed
- Multi-tenant refactor — current 1-container-per-client fine through ~10 clients
- Client-facing proposal template — separate artifact, drives shorter sales cycles

**Artifact:** `references/pricing-ops-policy.md` (10 sections — pricing, service model, tiers, fair-use, SLAs, onboarding timeline, cancellation, outage policy, deferred items, operating constraints).

**Owner:** Hughie

---

## 2026-05-25 — Free Missed-Call Audit shipped (first attraction offer per Hormozi Money Model)

**Decision:** Ship the **Free Missed-Call Audit** as the speed-to-lead attraction offer. Two artifacts: `outreach/missed-call-audit-template.md` (the audit one-pager template Hughie clones per prospect) + `outreach/missed-call-audit-loom-script.md` (the 5–7 min walk-through script).

**Why:** Hormozi review of the pricing+ops policy (same date) flagged the Money Model gap — the policy assumes the prospect is already a client and skips the attraction-offer layer. Per *$100M Leads* Lead Magnet framework (Free Audit / Diagnostic): give the strongest hit first, create a *custom dollar number for each prospect* before the sales call, so the conversation shifts from "is this worth $X" to "is this worth recovering $Y." Without this layer, every cold outreach starts at price defensiveness; with it, every cold outreach starts at problem recognition.

**Mechanism:**
1. 15 min of research per prospect (3 test calls + GBP scan + review scan + benchmark lookup)
2. Fill the template → personalised PDF with custom dollar figure
3. Record 5–7 min Loom walking through the findings live
4. Send email/DM with Loom link + PDF attached
5. Log to `outreach/leads.md` → `/outreach` handles the follow-up cadence

**Money Model position (per Hormozi $100M Money Models):**
- **Attraction offer:** Free Missed-Call Audit ← NEW, this artifact
- **Core offer:** Speed-to-Lead install + retainer (currently $497/mo PROPOSED — under repricing review per same-date Hormozi entry)
- **Upsell offer:** TBD (Review Reactivation Engine, Quote Follow-Up Bot — named, not built)
- **Downsell offer:** TBD ("DIY Setup + 30-day support")
- **Continuity offer:** monthly retainer (present)

**Bike Method Phase 1:** Hughie does every step manually for the first ~5 prospects — the research, the writing, the Loom recording, the send. Phase 2 candidates (future `/level-up` runs):
- Auto-research script (GBP scrape + test-call dialer + benchmark fetch → pre-filled template)
- Audit JSONL log keyed by prospect (mirrors the `outreach/leads.md` schema)
- Loom-view-rate → outreach prioritisation (anyone past 50% watched = warm)

**Why now (not deferred):** Hormozi pushback identified this as the single highest-leverage move blocking the rest of the pricing conversation. Without an attraction offer, the underpriced $497 retainer (separate decision) can't be raised — there's nothing creating the value perception that justifies a higher price. Attraction offer + repricing are paired moves.

**Alternatives considered:**
- *Defer until pricing is finalised* — rejected; the audit IS what enables the repricing conversation. Order matters: build the value mechanism first, then raise the price.
- *Ship a generic 1-pager (not personalised)* — rejected; per Hormozi, custom is the entire mechanism. A generic PDF is just a brochure.
- *Skip the Loom and just send the PDF* — rejected; the face-cam Loom is the trust delta. Tradies trust the bloke they can see talk.

**Deferred** (named, not built):
- Auto-research script
- Auto-fill of the template from scraped data
- Loom analytics → `/outreach` prioritisation
- Audit metrics tracking (send → watch rate → reply rate → call-booked rate)

**Artifacts:**
- `outreach/missed-call-audit-template.md` — the audit template + pre-fill checklist + workflow + anti-patterns
- `outreach/missed-call-audit-loom-script.md` — the 5–7 min Loom script with timing beats, variations, and after-Loom workflow

**Owner:** Hughie

> Adapted from Alex Hormozi's *$100M Leads* (Lead Magnet framework) and *$100M Money Models* (Four Types of Offers).

---

## 2026-06-17 — Hermes LLM swapped to NVIDIA DeepSeek V4 Flash (free) — supersedes 2026-05-16 provider

**Decision:** Run Hermes Agent on **`deepseek-ai/deepseek-v4-flash`** via NVIDIA's **native `nvidia` provider** (`base_url: https://integrate.api.nvidia.com/v1`, key in `NVIDIA_API_KEY`). Supersedes the 2026-05-16 OpenRouter/Claude-Sonnet setup. The box had silently drifted to `meta-llama/llama-3.3-70b-instruct:free` on OpenRouter's free tier and was throwing **HTTP 429** (rate-limited) — effectively down under any load.

**Why:** DeepSeek V4 Flash is free on NVIDIA's endpoint (no per-token charge, no card on file → can't bill, only throttles), is strong at **tool calling** (verified live — returned a correct `tool_call`), and is low-latency, which matters for Hermes's tool-heavy loop (up to 90 iterations). Net effect: inference cost dropped to **$0** (was about to pay OpenRouter/Anthropic rates). Trade-off: NVIDIA free tier is rate-limited (~40 req/min) — fine for demo/testing, **must move to a paid endpoint before a paying client pushes real lead volume.**

**The load-bearing lesson (cost ~6 debug cycles):** Hermes's **`model.base_url` field is IGNORED when `model.provider` is a named provider** (`openrouter`, etc.) — the provider plugin hardcodes its own URL. Setting `provider: openrouter` + `base_url: nvidia` sent the NVIDIA key to **openrouter.ai** → `HTTP 401 Missing Authentication header`. Fix: set `provider:` to the **matching native provider name** (`nvidia`), which supplies the correct base_url and reads the right key env var. Hermes ships native provider plugins under `/opt/hermes/plugins/model-providers/` (nvidia, deepseek, openai, gemini, xai, custom, etc.) — `_URL_TO_PROVIDER` in `agent/model_metadata.py` maps host → provider name. The earlier "Nous Research base_url" in config was always decorative for the same reason (provider was openrouter → it hit OpenRouter all along).

**Also learned:**
- Live config + real `.env` are on the **host** at `/docker/hermes-agent-5c1k/data/` (the `/opt/data/...` paths are the in-container view).
- `request_dump_*.json` files are written **only on errors** — absence of a fresh dump after a message = clean success.
- `chown hermes:hermes` fails on the host (no such user there) but is harmless; the gateway reads the bind-mounted config fine. Restart via `docker restart hermes-agent-5c1k-hermes-agent-1`.

**Alternatives considered:**
- *DeepSeek V4 Pro / Nemotron Ultra 550B / Qwen3.5-397b* — stronger but likely partner (paid) endpoints and heavier → more rate-limit pressure on a free tier.
- *Stay on OpenRouter free Llama* — rejected; that's the 429 status quo we were fixing.
- *Keep paid Claude via OpenRouter* — reliable but costs money; deferred to "before first client" as the paid-endpoint upgrade path.
- *Qwen3-next-80b* — kept as the automatic fallback pick if deepseek-v4-flash had been partner-gated (it wasn't — tested 200 OK).

**Verification:** direct API test (200 OK + valid tool_call) → config swap → live Telegram message answered cleanly on `provider=nvidia` with no 401/429.

**Affects:** `references/hermes-setup.md` and `references/vps-access.md` updated to the live DeepSeek/NVIDIA config; `agents/hermes/.env` records `NVIDIA_API_KEY` + `HERMES_MODEL`.

**Open / next:** NVIDIA free `nvapi` key is now exposed (pasted in chat) — **rotate it** and add an SSH key to the VPS, then rotate the root password. Move to a paid endpoint before client #1.

**Owner:** Hughie

---

## 2026-06-17 — Seeded Hermes persistent memory with Hughie's context

**Decision:** Populate Hermes Agent's persistent memory so it acts as a context-aware personal assistant, not a blank-slate chatbot. Wrote two files to `/docker/hermes-agent-5c1k/data/memories/` (injected into every turn):
- **`USER.md`** (1164/1375 chars) — identity profile: 22, Wollongong, solo founder, the two businesses (own AI service + HTP), 3-year north star, decision-paralysis blocker, and working-style preferences (direct, action-first, "to what extent could AI do this?").
- **`MEMORY.md`** (1410/2200 chars) — durable facts: Q2 priorities, trades/electricians locked, flat-fee managed-service pricing, Free Missed-Call Audit attraction offer, Chamber/in-person channel, HTP's KH/ĐL tiers, Hormozi-first + 3Ms frameworks.

Both distilled from `context/about-me.md`, `context/about-business.md`, `context/priorities.md`. Old `USER.md` (stale "prefers Claude Haiku" note) backed up on the box.

**Why:** Hermes injects `USER.md`/`MEMORY.md` into every turn (char-capped: 1375 / 2200), so the highest-leverage facts must be distilled, not dumped. A context-loaded agent reduces repeated steering — it already knows the businesses, priorities, and preferred register. Wrote files directly (owned UID 10000 = in-container hermes user); no restart needed since memory is read fresh per turn. Hermes also self-updates memory as it learns from chats, so this is a seed, not a freeze.

**Open / deferred:** optional one-command (or cron) sync to re-push `context/` → Hermes memory whenever the repo files change — not built yet; current state is a manual seed.

**Owner:** Hughie

---

## 2026-06-17 — OPEN QUESTION: white-label GoHighLevel vs. the already-built custom stack (NOT decided)

**Status:** OPEN — strategic reconsideration, *not* a decision. Logged so it doesn't float in conversation. Conflicts with locked decisions below; resolve before any money is spent or any GHL account is created.

**The question:** Should v1 delivery be **white-labeled GoHighLevel** (resell + configure GHL sub-accounts per client, Agency/SaaS tier ~$297–497/mo) instead of the **custom-built stack already shipped**? Hughie is energized by the white-label model (ship fast, enterprise-grade on day one, snapshot = clonable per-client template, GHL Conversation AI for the agentic layer).

**Why this is a real fork, not an add-on — what GHL would SUPERSEDE if chosen:**
- *2026-05-22 "Drop n8n":* the custom **FastAPI conversation engine** (`product/speed-to-lead-demo/`) already runs qualify→book/escalate with structured output + prompt caching. GHL replaces this with its own workflows + Conversation AI.
- *2026-05-22/23 telephony:* **ported AU number on Hughie's Twilio**, fail-open TwiML Bin, single Hostinger VPS. GHL brings its own telephony/Twilio rebilling — different ownership model.
- *2026-05-25 pricing/ops:* Model A managed-service, ~$60/mo direct cost → ~$440 margin at $497. GHL adds a ~$297–497/mo *platform floor* before client #1, compressing margin and changing the unit economics.
- *2026-05-24/25 moat:* outcome-labeled data + custom engine = the stated differentiator vs. "thin wrapper." White-labeling GHL makes Hughie "just another GHL agency" — weaker moat, but faster to a paying client.

**The genuine tension (why it's worth asking despite the sunk work):**
- *For GHL:* speed-to-cash. Land + deliver a Wollongong electrician faster; the North Star filter (documented, repeatable audit→configure→deliver loop via GHL snapshot) passes. Sidesteps the AU SMS carrier-filter pain (2026-05-24) via GHL's A2P registration flow. Lower maintenance than self-hosting.
- *Against GHL:* a working custom stack already exists and is *deployed* — switching discards real shipped code, hands the moat to a platform, and adds a fixed monthly cost. Classic decision-paralysis trap: forking to a new path before the built one has a client.

**What would resolve it (decide BEFORE building/buying):**
1. Unit-economics comparison: custom (~$60/mo cost) vs. GHL (~$297–497 floor + Twilio) → margin per client at 1 / 3 / 10 clients.
2. Honest read: is the blocker *delivery tech* (then GHL helps) or *getting client #1 to say yes* (then GHL changes nothing — the custom stack already works)?
3. If GHL wins, this entry is rewritten as a decision that explicitly supersedes the 2026-05-22/23/25 stack decisions.

**Recommendation (mine, not locked):** The custom stack is already shipped and the simulator *is* the demo (2026-05-24). The bottleneck is client #1, not delivery tech — so don't switch yet. Use GHL only if a concrete blocker in the built stack proves it. Keep this OPEN.

**Owner:** Hughie

---

## 2026-06-19 — Hermes LLM swapped to OpenRouter GLM-4.7-flash — supersedes 2026-06-17 NVIDIA

**Decision:** Run Hermes Agent on **`z-ai/glm-4.7-flash`** via the native **`openrouter`** provider (`base_url: https://openrouter.ai/api/v1`, key in `OPENROUTER_API_KEY`). Supersedes the 2026-06-17 NVIDIA DeepSeek V4 Flash setup. The NVIDIA free endpoint was throwing **HTTP 429** under normal use (~40 req/min cap) — the bot was effectively down.

**Why:** A ChatGPT subscription gives **no API access** (it's the chat app only), so it can't power Hermes — the real options are pay-per-token API keys. Used the `OPENROUTER_API_KEY` already on the box (no new account/billing). GLM-4.7-flash is a cheap **execution-tier** model: **$0.06 in / $0.40 out per Mtok** (~20× cheaper than GLM 5.2, which is the *priciest* GLM despite the higher version number), 200K context, strong tool-calling. Reasonable reliability + tiny cost vs. NVIDIA's free-but-throttled tier. Trade-off: now paying per token (cents/day at this volume) instead of $0; GLM does light reasoning so replies are a touch slower than NVIDIA.

**The wasted cycle (logged so it doesn't recur):** First attempt edited `agents/hermes/hermes.py` + `agents/hermes/.env` in the repo — but **those files are NOT the live bot.** The live Hermes is the Nous Research **product** on the VPS, configured by `/docker/hermes-agent-5c1k/data/config.yaml`. The repo `hermes.py` is an unrelated/abandoned custom FastAPI script. Editing repo files changes nothing live. The retry/"trying fallback" text in Telegram is the *product's* built-in handling, not our code — its absence from `hermes.py` was the tell.

**How the switch was actually made:** edited `config.yaml` on the host — `model.default → z-ai/glm-4.7-flash`, `model.provider → openrouter`, `model.base_url → openrouter URL` (per 2026-06-17 lesson: `base_url` is decorative for named providers; `provider:` is what routes). Backed up config, `docker restart hermes-agent-5c1k-hermes-agent-1`. Verified: container Up, Telegram reconnected, zero errors. `providers: {}` stays empty — `openrouter` is a native plugin like `nvidia`, no explicit block needed.

**Alternatives considered:**
- *OpenAI API direct* — needs a new funded API account; OpenRouter key already existed → no setup.
- *GLM 5.2 (what Hughie named)* — most expensive GLM ($1.20/$4.10); overkill for an execution bot. Flash chosen; one-line switch to 5.2/4.6 documented.
- *Stay on free NVIDIA* — rejected; that's the 429 status quo we were fixing.

**Open / next:** still must rotate exposed secrets (NVIDIA keys, VPS root password) + add SSH key. Watch OpenRouter spend at openrouter.ai/activity.

**Affects:** live `config.yaml` on VPS; `references/hermes-setup.md` + `references/vps-access.md` updated to the OpenRouter/GLM config.

**Owner:** Hughie

---

## 2026-06-19 — Gmail + Calendar wired to Hermes via Google Workspace MCP

**Decision:** Give Hermes Gmail (read+send) and Calendar (read/write) by connecting it — as an MCP client — to the **Google Workspace MCP server** (`taylorwilsdon/google_workspace_mcp`, PyPI `workspace-mcp`, run via `uvx` inside the Hermes container, stdio transport, `--single-user --permissions gmail:send calendar:full`). Uses Hughie's own Google Cloud OAuth client (Desktop app, project `hermes-gmail`, account `mth9703@gmail.com`). Verified live: read 3 latest emails + 3 upcoming calendar events through Telegram.

**Why:** Hermes has no native Gmail; MCP is its supported extension path (`hermes mcp add`). The Workspace MCP is the most complete Google MCP and keeps the data path entirely Hughie's infra → Google (no third party). Chose Gmail+Calendar together (one server) since Calendar was the next CLAUDE.md integration anyway. Read+send (not read-only) so Hermes can act on outreach later; Calendar full for scheduling.

**ChatGPT subscription dead-end (logged so it's not re-asked):** a ChatGPT/OpenAI *subscription* grants no API or automation access — irrelevant to wiring tools. The only credential that mattered was a Google OAuth client, which only Hughie could create (his account + browser consent).

**CLI vs MCP (considered, rejected CLI):** a CLI via Hermes's terminal tool has lower idle token cost but is less reliable (model hand-writes shell + parses text) and still needs the identical OAuth consent. MCP overhead (~5k tokens/msg ≈ $0.0003 on GLM-4.7-flash) is negligible. **Mitigation:** trimmed enabled tools 20→10 (kept search/read/send/draft/label, list-calendars/get-events/manage-event, re-auth; disabled batch-fetch, attachments, label-admin, OOO, focus-time, free/busy, create-calendar) — ~half the overhead, full reliability. Reversible via `hermes tools enable`.

**Load-bearing lessons (full writeup: `references/gmail-hermes-setup.md`):**
1. **Creds dir must be owned by uid 10000** (the gateway/MCP user). A `./hermes -z` test run via `docker exec` defaults to **root** and wrote a root-owned `oauth_states.json` → MCP got `Errno 13 Permission denied` and couldn't start auth. Fix: `chown -R 10000:10000 /opt/data/google_creds`.
2. **Headless OAuth callback** (`localhost:8000/oauth2callback`, callback server inside the container, port unpublished) needs a bridge: a temporary in-container TCP forwarder (`0.0.0.0:8765 → 127.0.0.1:8000`) + laptop SSH tunnel `-L 8000:172.16.1.2:8765`. Removed after consent.
3. Token persists at `/opt/data/google_creds/` (bind-mounted → survives restarts). `python3` not on container PATH (use `/opt/hermes/.venv/bin/python3`); background via `docker exec -d`. `hermes mcp add` prompts to enable tools → pipe `printf 'Y\n'`.

**Open / next:**
- **7-day token expiry:** app is in OAuth "testing" mode → refresh token expires weekly. Hughie to click **Publish app** on the consent screen to make it permanent. Re-auth runbook in the reference file if it lapses.
- Still pending from 2026-06-19 OpenRouter switch: rotate exposed secrets (NVIDIA keys, VPS root password) + add SSH key.

**Artifacts:** live MCP config on VPS (`config.yaml` server `google-workspace`, wrapper `/opt/data/google-workspace-mcp.sh`); new `references/gmail-hermes-setup.md`.

**Owner:** Hughie

---

## 2026-06-19 — /level-up Method spec: Missed-Call Audit research bot (Hermes skill)

**Decision:** Ship `missed-call-audit` as a **Hermes skill** (on the VPS, triggered from Telegram) that automates the research + drafting of the Free Missed-Call Audit attraction offer. Autonomy **L2** — Hermes scrapes public data, computes the dollar-loss, and drafts the audit one-pager with evidence; Hughie reviews, adds the 3 test-call results, records the Loom, and sends.

**Method spec (3Ms / `/level-up`):**
1. *Constraint:* ~15 min of manual research per prospect throttles audit volume — and audits-sent is the top of the only client-acquisition funnel (priority #2, and the "audit SMBs" front of the north-star loop).
2. *EAD:* **Automate.** Not eliminate (the custom $-number is the entire Hormozi mechanism); not delegate (solo). 60/30/10: ~60% deterministic (GBP scrape, competitor benchmark, review-text scan, $-loss formula), ~30% AI-assisted (drafting findings in voice), ~10% manual & retained by Hughie (the 3 phone test-calls — Hermes has no telephony — and the face-cam Loom).
3. *Process map:* **Trigger** = Hughie gives a business name/suburb (or GBP URL) to @BaoBei09bot. **Sources** = GBP, website, top-2 local competitors, review corpus. **Transform** = scrape → compute weekly/monthly $-loss → fill template. **Decision point** = evidence-gating (write a finding only with real evidence, else `<<NEEDS: …>>`; never fabricate). **Destination** = drafted `<<slug>>-audit.md` back in Telegram → Hughie finishes → `/make-pdf` → Loom → send → log to `outreach/leads.md`.
4. *Autonomy:* **L2 — Drafted.** L3 (auto-send) rejected: sending unreviewed audits risks fabricated findings reaching prospects. Bike Method Phase 1 (review every output).
5. *KPI:* Bucket = **More customers.** Metric = research time per audit (~15 min → <5 min of Hughie's time) + audits-sent/week.

**Machine:** AI-assisted Hermes skill (Boring-is-Beautiful: mostly deterministic, one drafting pass, no sub-agent). Self-contained — embeds the audit template, the $-loss formula + benchmarks, evidence-gating, and the anti-fabrication rules. Source-of-truth copy in repo; deployed to VPS.

**Boundaries:** Hermes cannot make the 3 phone test-calls (no telephony) — it leaves placeholders. Current Google scope is Gmail+Calendar only (no Sheets/Drive), which this skill doesn't need.

**Deferred** (future `/level-up` runs): auto-log the prospect into `outreach/leads.md` + set a Calendar follow-up (ties into the proposed lead follow-up engine); pull live AU benchmark figures instead of the static ones; auto-record/host the Loom.

**Artifacts:** `agents/hermes/skills/missed-call-audit/SKILL.md` (repo source) + deployed to `/opt/data/skills/missed-call-audit/SKILL.md` on the VPS (Hermes shows it `enabled`).

**Owner:** Hughie

> Adapted from The Three Ms of AI™ © 2026 Nate Herk.


## 2026-06-21 — Onboard GHL clients via Snapshot + API, not Playwright

**Decision:** Provision each new client's GHL sub-account by cloning a saved "Speed-to-Lead v1" Snapshot via the v2 API (`agents/onboarder/clone_client.py`), then swapping per-client custom values. Build the template once in the UI; never script the UI per client.

**Why:** Snapshots are GHL's native "build-once, clone-per-client" mechanism — stable, fast, scriptable. Playwright-per-client is brittle (breaks on every UI change, fights 2FA, slow to debug) and re-does work the platform already does for free. Fits the "one well-built agent beats five manual workflows" rule.

**What stays manual (API can't):** phone-number provisioning + AU A2P/compliance, and final go-live approval. Snapshots also don't carry phone numbers, sending domains, or A2P registration — those are per-location.

**Open risk:** snapshot-assign-on-create (`snapshotId` on `POST /locations/`) is flagged TODO-VERIFY in code — may be gated to SaaS-mode agencies, or need a separate "load snapshot into location" call. Verify on one throwaway sub-account before relying on it.

**Auth:** agency-level Private Integration token (`pit-...`). Company ID `WBcLFKbpX2Dh0Kvvix0l` retrieved via `clone_client.py --check`. Token was exposed in chat during setup — rotate before going live.

**Alternatives considered:**
- Playwright per client → brittle, slow, rejected.
- Fully custom FastAPI/Twilio build (no GHL) → the long-term moat, but slower to client #1; GHL validates first.

**Owner:** Hughie

---

## 2026-06-21 — Connect client numbers via AU Conditional Call Forwarding (GSM codes), not porting or US codes

**Decision:** Wire each client's existing business number into the GHL missed-call→AI-text-back flow using **Conditional Call Forwarding (CCF)** — divert on no-answer/busy/unreachable to a GHL AU mobile number. Use the **GSM standard codes** (`*61*`, `*67*`, `*62*`), which work on every AU carrier (Telstra/Optus/Vodafone + MVNOs). No number porting. SOP + onboarding email at `references/ccf-setup-au.md`.

**Why:** CCF keeps the client's number, lets their phone ring normally first, and only hands off on a miss — lowest-friction, no porting risk. AU is all-GSM so one code set covers every carrier (unlike the US, which needs carrier-specific codes). This is also the concrete edge over LeadSaver, which only catches Google-listing calls; CCF on the client's own mobile catches *every* missed call (referrals, repeats, van signage).

**Key gotcha (verify per client):** carrier voicemail/MessageBank competes with the divert. If voicemail picks up before the no-answer divert fires, nothing forwards. Fix: set divert timer short (`*61*[N]**20#`) or disable carrier voicemail. Test every go-live.

**Also locked:** first text-back is a STATIC instant SMS (speed + reliability); Conversation AI (stable, non-BETA) handles the reply/qualify/book — not the first message.

**Alternatives considered:**
- US carrier codes from the original Gemini playbook → wrong country, don't work in AU. Rejected.
- Number porting → slow, scary, breaks things; unnecessary with CCF.
- AI-generated first text → adds latency + hallucination risk on the one message that must always send. Rejected.

**Owner:** Hughie

---

## 2026-06-22 — 🔒 LOCKED: Build own self-hosted platform; GHL demoted to fallback (supersedes 2026-06-21 GHL onboarding)

**Status:** LOCKED.

**Decision:** Build and ship Hughie's **own self-hosted speed-to-lead platform** on the existing `product/speed-to-lead-demo/` engine (FastAPI + Twilio + Claude + SQLite, on the Hostinger VPS), and make it **configurable by describing workflows to an AI**. **GHL is demoted to a fallback** — used only to land client #1 fast *if* a client says yes before the own-platform path is client-ready. The own platform is the primary build. Source-of-truth plan: `product/speed-to-lead-demo/OWN-PLATFORM-PLAN.md`.

**Why:** Three driving reasons — (1) kill GHL's ~$297/mo subscription floor (own stack costs only Twilio + LLM *usage*); (2) own the client relationship end-to-end, signups into Hughie's own system, no third-party app dependency; (3) build/edit each client's workflow by *talking to an AI* — a capability GHL structurally cannot offer, and the real moat. Decisive enabler: the audit (2026-06-22) found the core is **already ~80% built** — `workflow.py` runs qualify→triage→book/escalate with the no-quote guardrail already enforced (line 123), and `app.py` already does missed-call→text-back via Twilio (the exact thing the GHL build got stuck on in `draft`). So this is **not** a from-scratch rebuild that risks the "fork before client #1" trap — it extends shipped, working code. What would change this: a client signs and needs delivery *now* before Phases 1-3 are ready → use the GHL fallback for that one, migrate later.

**Honest cost caveat:** "no external fees" is not literally achievable — telephony (Twilio) and the LLM API are irreducible usage costs. What dies is the subscription floor + commodity-reseller positioning.

**The build (phases in the plan doc):**
1. Externalize workflow into per-tenant config (keystone refactor — unlocks multi-tenancy + AI editing).
2. AI workflow-builder — describe in English → AI rewrites tenant config → validate → hot-reload. ⭐ the moat.
3. Multi-tenant routing + owner dashboard (replaces GHL's app).
4. Self-serve signup that provisions a tenant.

**Alternatives considered:**
- Stay on GHL as primary → recurring platform tax + commodity moat + the build kept getting stuck (draft/publish/token friction); rejected as primary, kept as fallback.
- Full rebuild of every GHL feature before a client → the decision-paralysis trap flagged 2026-06-17; avoided by building only the differentiated slice on the existing engine and serving one real client.

**Supersedes:** 2026-06-21 "Onboard GHL clients via Snapshot + API" and the GHL-primary stance — those are now the fallback path, not the main one.

**Owner:** Hughie

---

## 2026-06-23 — 🏁 MILESTONE: own platform v1 feature-complete (Phases 0–3 shipped)

**Status:** Milestone log. Built in one session on `product/speed-to-lead-demo/`. Plan + status: `product/speed-to-lead-demo/OWN-PLATFORM-PLAN.md`.

**What shipped (all verified, mostly against the live model):**
- **Phase 1 — config-driven tenants.** Per-client config (`tenants/<id>.json`) renders the engine prompt; `dave` stays byte-identical. `tenants.py` loads/validates.
- **Phase 2 — AI workflow-builder + trade-neutral engine.** `build_tenant.py`: describe a business in plain English → Claude writes a full validated tenant config (identity, trade-specific triage/examples, quoting policy). All trade-specific content moved out of the shared prompt into config (sentinels in `workflow.py`), so a locksmith no longer talks like an electrician. Per-tenant `quoting_policy` (never/ranges/full) with a high-salience override (single clause-swap was insufficient — found via smoke test). New outcomes `callback` + `not_a_job`.
- **Phase 3a — multi-tenant telephony.** Inbound routes by `To` number → tenant (`tenants.find_by_number`); engine runs per-tenant (`run_turn(system=…)`, cached); store keyed by `(tenant_id, phone)`; `provision_number.py` assigns a number + wires webhooks.
- **Phase 3b — Google Calendar booking.** `connect_calendar.py` (one-time OAuth → per-tenant refresh token); engine returns ISO `booking_start`/`booking_end`; `app.py` creates the event (fail-open, `gcal.py`). Bookings are forced forward in time by both prompt rule and a deterministic `_roll_to_future` guard (never books the past).
- **Phase 3c — owner dashboard.** Read-only token-gated console at `/dashboard`: tenants + counts → per-tenant pipeline grouped by stage → conversation transcript. Server-rendered, XSS-safe.

**Honest state / what's NOT done:**
- The irreducible **manual/live steps** (need real creds, cost money, regulatory): buy Twilio number + **AU A2P SMS registration**; create Google OAuth client + run `connect_calendar.py` per client.
- **Phase 4 (self-serve signup)** not built.
- Dashboard auth is a single shared `DASHBOARD_TOKEN` — harden to per-client logins before any client-facing access.
- Minor refinements logged: `full` quoting needs a per-tenant price list; callback alert fires after collecting a name (could be immediate).
- **Deploy debt:** new deps (`google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `tzdata`) → `pip install -r requirements.txt` on the VPS; set `DASHBOARD_TOKEN`; new tenant configs are live SMS state (DB auto-migrates `conversations` to `(tenant_id, phone)`).

**Why log as a milestone:** large surface shipped in one session; this is the checkpoint to resume from and the basis for the next decision (Phase 4 vs deploy-and-land-client-#1).

**Owner:** Hughie

---

## 2026-06-23 — Client logins (operator-provisioned); supersedes "client logs into nothing"

**Decision:** Clients get their **own login** to a tenant-scoped dashboard. Signup is **operator-provisioned**, not self-serve: when a client agrees, Hughie builds + customizes their tenant (`build_tenant.py` → `provision_number.py` → `connect_calendar.py`), then runs `create_account.py --tenant <id> --username <u>` to mint credentials and hands over the username + password. The client logs in at `/login` and sees ONLY their own leads. **Public self-serve signup + payment is deferred** (Phase 4b) — not needed for client #1–N. This supersedes the 2026-05-25 managed-service stance of "client sees one invoice, logs into nothing."

**Why:** Letting a client see their own lead inbox / pipeline / booked jobs makes the ROI visible, which is a retention and upsell asset (and a live sales proof for the next prospect). Operator-provisioned (vs self-serve) avoids building a public signup + billing flow before there's volume to justify it — Hughie controls account creation, which is fine at this stage and keeps the build small. What would change this: enough inbound that manual account creation is a bottleneck → build 4b (self-serve + Stripe).

**Auth model:** signed session cookie (HMAC via `SECRET_KEY`); two roles — admin (Hughie, sees every tenant, can also `?key=DASHBOARD_TOKEN`) and tenant (a client, hard-scoped to their own `tenant_id`, 403 on any other). Client passwords are pbkdf2-hashed in `data/accounts.json` (gitignored). Verified: client blocked from other tenants, logout clears, admin sees all.

**Security fix (same day):** tenant configs (`tenants/*.json`) hold per-client secrets (Google refresh token, numbers) and are now **gitignored** (with a committed `tenants/example.json` template); they live on the VPS as per-deployment data, never committed. No secrets were ever tracked. Before going client-facing: set a strong random `SECRET_KEY`, serve over HTTPS only, and consider rotating tokens that appeared in chat during setup.

**Alternatives considered:**
- *Keep "logs into nothing" (operator-only dashboard)* — simpler, but the client never sees their ROI; weaker retention and no self-serve proof. Rejected now that the dashboard exists.
- *Public self-serve signup now* — over-build before volume; needs billing + abuse handling. Deferred to 4b.
- *Secrets in tenant config, committed* — leaks Google tokens; rejected. Secrets stay in gitignored files on the VPS.

**Artifacts:** `accounts.py`, `create_account.py`, `/login`+`/logout`+scoped `/dashboard` in `app.py`, `.gitignore` (tenants), `tenants/example.json`.

**Owner:** Hughie

---

## 2026-06-23 — Speed-to-lead demo proven END-TO-END on a live AU number

**Decision:** The first fully-wired, live demo of the speed-to-lead product is running on a real AU Twilio number (`+61468089224`) against a demo tenant (`rapidflow-plumbing` — a fictional Wollongong plumbing business, "RapidFlow Plumbing & Gas"). Verified end-to-end with **real Conditional Call Forwarding**: missed call → carrier forwards to Twilio → AI greeting → instant text-back to the caller → AI qualify/book → owner-alert SMS. Demo to prospects via the Twilio number directly; CCF stays a per-client onboarding step.

**Verified against Twilio logs (2026-06-23):**
- Direct call + direct SMS to the number → AI flow runs, text-back delivered.
- Real CCF: a forwarding phone diverted an unanswered call to Twilio → text-back delivered to the **original caller** (correct `From` routing, no loop).

**Key findings:**
- **amaysim (Optus MVNO) does NOT complete forwarded calls.** The divert activates (immediate, no ring) but the leg never reaches Twilio. Worked perfectly on a different carrier. → Carrier limitation, not a product bug. CCF belongs to per-client onboarding on the client's (typically Telstra/Optus postpaid) line.
- **App now sends outbound SMS from the AU number.** It was sending from a leftover US number (`+19129785856`) via the VPS `.env` `TWILIO_NUMBER`; that would have broken reply routing (replies must come back to the tenant's own number). Fixed.
- **US test number released** (de-activated → deleted): stops billing, useless for an AU trades business.
- **New `voice_textback_only` tenant flag.** For CCF safety-net tenants, the Twilio voice handler must NOT dial the owner back (the caller already missed them) — it greets + texts back only, preventing a forward→dial-back loop (`ccf-setup-au.md` gotcha #2). Default `false` preserves the ring-owner behaviour for other tenants.
- **Regulatory bundle:** using an Individual bundle for now (Business bundle needs a website); switch to Business under the entity before the first paying client.

**Deploy note (cost ~1 cycle):** tenants are baked into the image (only `./data` is a volume). New/edited tenants need a `docker cp` into the running container (live, ephemeral) **and** an image rebuild (`docker compose up -d --build app`) to persist. `docker restart` / `--force-recreate` alone **reverts** cp'd changes and does **not** reload `.env`.

**Artifacts:** `tenants/rapidflow-plumbing.json`, `voice_textback_only` branch in `app.py` `/twilio/voice`, VPS `/opt/speed-to-lead` rebuilt + redeployed.

**Owner:** Hughie

---

## 2026-06-24 — Self-serve calendar connect + conversation-engine fixes shipped to live prod

**Decision:** Clients can now connect their Google Calendar themselves from their dashboard (a "Connect Google Calendar" button → web OAuth → back to "Connected ✓"), instead of Hughie running `connect_calendar.py` per client. Also fixed three conversation-engine defects found in a live RapidFlow test and shipped everything to the production VPS.

**What shipped (live on `https://stl.187-77-133-39.sslip.io`):**
- **Self-serve calendar OAuth.** New web-flow helpers in `gcal.py` (`is_configured`, `authorization_url`, `exchange_code`), `tenants.save_google_calendar`, a status banner + two routes (`/dashboard/{id}/calendar/connect`, `/oauth/google/callback`) in `app.py`. State is an HMAC-signed, 10-min, tenant-scoped capability reusing the existing `_sign`/`_unsign`. PKCE disabled (confidential web client) — required, else `invalid_grant: Missing code verifier`.
- **Phone-number bug.** Engine never actually received the caller's number, so it kept asking for it. Now injected into the per-turn context note in `app.py` + a hard rule in the prompt.
- **Address gap.** A "booked" job had no street address. Added `address` to the `Qualified` structured output and required it before booking; calendar event title/description now carry address + customer number.
- **Booking railguard.** "Just book it" on a quote-first job used to deflect to a phone callback (so `booking_start` was never set → no calendar event). Now confirms a window and sets `booking_start/end`.
- **`load_dotenv` ordering fix** in `app.py` (gcal read `GOOGLE_CLIENT_ID` at import before `.env` loaded — broke local dev, incl. the CLI booking path).

**Model decision:** Production stays on **`claude-haiku-4-5`** ($1/$5 per Mtok). The defects were prompt/wiring, not capability. If post-fix booking date-math or quote-vs-book judgment still flakes, bump to **`claude-sonnet-4-6`** ($3/$15) — a one-line change in `workflow.py:25`. Cost is negligible either way (cents/conversation vs a multi-hundred-dollar job); don't use Opus (overkill, slower for SMS).

**Caveats:**
- **Google OAuth app is in "Testing" mode** — only added test-user Gmails can connect, tokens expire after 7 days. Must move to "Production" (triggers Google verification review for the `calendar.events` scope, can take days) **before** onboarding a paying client who isn't a test user.
- **Client secret (`GOCSPX-…`) was pasted in chat** during setup — consider rotating before first paying client.
- Tested tenant must have its calendar connected for a booking to actually create an event.

**Deploy:** 4 files (`app.py`, `gcal.py`, `tenants.py`, `workflow.py`) scp'd to `/opt/speed-to-lead`; `GOOGLE_CLIENT_ID`/`SECRET` appended to VPS `.env`; `docker compose up -d --build` (pip layer cached). Verified live: `/health` ok, oauth routes present, `gcal.is_configured: True`, `address` field live, prod redirect URI correct. **Rollback:** prior files backed up to `/opt/speed-to-lead/.bak/20260624-102311/`.

**Artifacts:** `gcal.py`, `tenants.py`, `app.py`, `workflow.py`, `.env.example` (Google web-client docs); VPS `/opt/speed-to-lead` rebuilt + redeployed.

**Owner:** Hughie

---

## 2026-06-24 — Conversation engine bumped Haiku 4.5 → Sonnet 4.6 (executes the 2026-06-24 conditional bump)

**Decision:** Switch the Twilio conversation engine model from `claude-haiku-4-5` to `claude-sonnet-4-6` — one line, `workflow.py:25`. This executes the conditional upgrade path flagged in the same-day calendar/engine-fixes entry ("If post-fix booking date-math or quote-vs-book judgment still flakes, bump to `claude-sonnet-4-6`"). `build_tenant.py` (one-off tenant-config generator) stays on Haiku — not on the live response path.

**Why:** Hughie's call to run the higher-capability model on the lead-facing replies. Verified with the full `smoke_test.py` run on Sonnet — all five scenarios route correctly (bookable → windows + address, emergency → instant escalation, callback → `notify_kind=callback`, not-a-job → closed with no owner alert, quoting never vs ranges behaves per policy). Replies are noticeably warmer/sharper than Haiku.

**Trade-off:** Sonnet is $3/$15 per Mtok vs Haiku's $1/$5 (~3× input, ~3× output) plus slightly higher latency — both negligible at cents/conversation against multi-hundred-dollar jobs, and well inside the ~$440/mo gross margin in the pricing-ops unit economics. Opus stayed ruled out (overkill + slower for SMS).

**Caveat:** The unit-economics line in the 2026-05-25 pricing decision still cites "Haiku 4.5, ~$0.04/conversation" — now stale; per-conversation API cost roughly triples (still ~$6/mo per client at ~50 leads, immaterial).

**Affects:** `product/speed-to-lead-demo/workflow.py:25`. **Deployed live 2026-06-24** to the VPS `/opt/speed-to-lead`: old file backed up to `.bak/20260624-180610/`, `workflow.py` scp'd up, `docker compose up -d --build` (image rebuilt, `speed-to-lead-app-1` recreated). Verified — running container shows `claude-sonnet-4-6` on line 25, `/health` 200 on `https://stl.187-77-133-39.sslip.io`, clean startup logs. Rollback: restore from `.bak/20260624-180610/workflow.py` + rebuild.

**Owner:** Hughie

---

## 2026-06-25 — Eval-gated prompt tuning: majority-vote gate + precision/triage/safety fixes

**Decision:** Adopt a majority-vote-over-N eval harness (`evals.py`) as the quality gate for the conversation engine, and ship a set of *measured* changes through it: engine `temperature=0.4`; a "don't invent / don't guess" accuracy rule; earlier triage + bookable decisiveness; sharper quote follow-up (ask if they've seen the quote, don't re-send); per-tenant `emergency_safety` guidance; rapidflow opener no longer self-discloses as AI.

**Why:** A single-run eval was too noisy to trust (same HEAD scored 90% then 70%). Majority-vote over N (parallelised, ~2 min at N=8) separates real failures from sampling noise and gates on robust safety (each safety scenario ≥80% of runs) + overall ≥90%. Measuring caught two things eyeballing would have missed: (1) brevity-forcing prompt edits *dropped required steps* (quote photo, rental landlord note) — reverted; replies were already tight (~1.2 lines), so brevity was the wrong target; (2) the engine occasionally gave operational emergency advice ("switch off the power") — fixed trade-aware so an electrician never tells someone to touch a faulty board, while a plumber can still say "turn off the water at the mains".

**Result:** Gate PASS at N=8 — safety 10/10 robust, overall 19/20 (95%), precision 1.1 lines / 193 chars / 92% one-question. B1 + F3 fixed solidly; B3 ("downlight swap, no details") remains borderline (~3/8 — model asks two relevant questions) and is flagged advisory.

**Alternatives considered:**
- Apply prompt edits and eyeball the demo (fast path) → rejected; would have shipped the dropped-step + emergency-advice regressions invisibly.
- Hardcode a no-power-switching rule in the shared prompt → rejected; breaks plumbers. Used the per-tenant `<<emergency_safety>>` sentinel instead.

**Affects:** `product/speed-to-lead-demo/workflow.py`, `app.py` (idempotency + `/health/deep` + ROI report route), `store.py` (`mark_seen` + `report_counts` + `processed_events` table), `report.py` (new), `evals.py` (majority-vote + `--runs`/`--concurrency`), `tenants/{dave,rapidflow-plumbing,lockedout-locksmiths,example}.json` (new `emergency_safety`).

**Deployed live 2026-06-26** to VPS `/opt/speed-to-lead`: backup at `.bak/20260626-001331/` (app.py, store.py, workflow.py, tenants/, .env.bak); scp'd `workflow.py app.py store.py report.py canary.py`; patched `tenants/{dave,rapidflow-plumbing,example}.json` **in place** (added `emergency_safety`, updated rapidflow opener) to preserve VPS secrets — `lockedout` is not on the VPS; appended `CANARY_TOKEN` to `.env` to gate `/health/deep`; `docker compose up -d --build`. Verified: `/health` 200, `/health/deep` `{db:ok,engine:ok}`, 403 without token, both tenants load + render `emergency_safety`, `processed_events` table present, clean startup logs. **Rollback:** restore from `.bak/20260626-001331/` + rebuild.

**Follow-ups:** B3 triage-field + X2 rental-landlord note are advisory-flaky; lockedout's "are you a bot?" reply claims to be human ("I'm Mick, real person") — contradicts the shared "never claim to be human" rule, worth a separate fix.

**Owner:** Hughie

---

## 2026-06-26 — Pricing & go-to-market: free pilot → $300/mo, performance-based deferred

**Decision:** Land client #1 with a **free pilot first** (prove real work + conversion on a live line), then convert to a flat **$300/mo** retainer. **Performance-based pricing** (per recovered booking) is deferred — revisit only after the $300 model has proven conversion with at least one client. Reserve premium tiers ($1k+) for multi-van / higher-ticket firms later; not the wedge.

**Why:** Came out of the `/roast` council (verdict: RESHAPE). The earlier $2k–$4k/mo target was demolished by the unit-economics math (a solo sparky at ~$350/job doesn't have the call volume to clear a 3x value ratio) and by live AU competitor pricing — Leva $199, Lana $300, Sophiie $300/mo. $300 sits dead-on the established market rate, so it's a price tradies already accept and won't anchor against. Free-first kills the two biggest objections at once (no-clients-yet trust gap + "prove I'm actually losing money") and produces the one asset that makes every later sale close on math: a real recovered-dollar number + testimonial. Performance-based is the *right* long-term model (fee can never exceed value delivered) but needs a proven conversion baseline first — you can't price per booking until you know your booking rate.

**What would change my mind:** once the free pilot yields a hard recovered-$/mo figure and a repeatable booking rate, move to performance-based or raise the floor. If conversion proves the value is well above $300, raise it; if a multi-van firm bites, that's the $1k+ tier.

**Alternatives considered:**
- $500–900/mo entry (council's suggested floor) → rejected; $300 matches the proven AU market rate and lowers the barrier to client #1 in the 5-week window.
- Performance-based from day one → rejected; no conversion baseline yet to price against.
- Charge from day one (no free pilot) → rejected; no case study + no-client trust gap makes a cold paid close in 5 weeks unlikely.

**Owner:** Hughie

---

## 2026-06-26 — Front door: SMS text-back → instant AI voice callback (modifies 2026-05-22 channel)

**Decision:** Flip the speed-to-lead first touch from **SMS missed-call-text-back** to an **instant AI voice callback**. On a missed call the system rings the caller straight back with a live AI voice agent; **SMS text-back becomes the fallback** when the callback isn't answered or hits voicemail. Voice is **ElevenLabs**; built **fully in-house** — Twilio Media Streams → Deepgram (streaming STT) → the existing Claude brain → ElevenLabs (streaming TTS). Modifies (does not supersede) the channel element of the 2026-05-22 electrician pivot: missed-call-text-back is now the safety net, not the headline. Everything else from that decision stands (trades/electricians, build-before-sell, named-human persona with honest AI disclosure, fail-open, single VPS).

**Why:** A human-sounding callback ~30s after a missed call beats a text on the one axis that wins emergency-trade jobs — the caller is still in buying-mode and the first real voice to reach them takes the job. In Hormozi's Value Equation (*$100M Offers*) it crushes two levers at once: **time delay** (instant) and **effort/sacrifice** (the caller just answers a ringing phone — no reading, no typing). Because the lead rang us first, an instant callback is a *returned missed call*, not cold outbound — it sidesteps AU Do Not Call / telemarketing exposure that would kill prospecting calls, and the existing "always disclose you're an AI + offer a human" rule carries straight over. The existing `workflow.py` trade brain (triage, two-window booking, emergency handling) is reused verbatim — only the I/O contract changes from one-shot structured output to streaming text + tool calls. COGS rises ~5–10× vs SMS (~$0.30–0.60 for a 3-min call, ElevenLabs the dominant layer) but stays trivial against a caught job ($150–2,000+), and buys materially higher conversion.

**What would change my mind:** the live risk is **latency/barge-in** on the raw media loop. If tuning the in-house pipeline eats too much time, fall back to **Twilio ConversationRelay** — it hands the STT/TTS plumbing to Twilio while still keeping the Claude brain *and* an ElevenLabs voice; the only thing lost is "full in-house." Also revisit if a tradie's customers turn out to prefer text (then keep SMS primary, voice opt-in).

**Alternatives considered:**
- *Voice-only, retire SMS* → rejected; no fallback when the caller can't take a call right then = dropped lead.
- *Keep SMS primary, voice opt-in* → rejected for the wedge; voice is the differentiator, lead with it.
- *ElevenLabs Agents Platform / Vapi / Retell (managed)* → faster to live, but re-authors the brain into their config or adds a platform per-minute margin; "full in-house" picked for control + lowest per-minute cost.
- *Twilio ConversationRelay* → strong middle ground (kept as the documented fallback above), but not full in-house.

**Build approach:** `voice_engine.py` (streaming + tool-calling adapter over `system_for(tenant)`; Haiku 4.5 default for low latency) — **shipped + verified**. `voice_server.py` (media bridge) and the `app.py` trigger + no-answer→SMS fallback — **specced, not yet built**. New deps: `deepgram-sdk`, `elevenlabs`. New env: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, public `wss://` tunnel. The media loop's real verification needs live keys + a real phone call.

**Owner:** Hughie

> Value framing per Alex Hormozi's *$100M Offers* (Value Equation).

---

## 2026-06-26 — Voice agent shipped on Deepgram's managed Voice Agent API (pivots the in-house/ElevenLabs build above)

**Decision:** Built the live voice front door on **Deepgram's managed Voice Agent API** (`wss://agent.deepgram.com/v1/agent/converse`), not the in-house Twilio→Deepgram-STT→Claude→ElevenLabs-TTS pipeline the earlier 2026-06-26 entry specced. One WebSocket: Deepgram owns STT (nova-3), turn-taking, barge-in, **Aura-2 TTS** (`aura-2-thalia-en`, replacing ElevenLabs) **and hosts the Claude brain**. Tools (`alert_owner` / `book_job` / `end_call`) run client-side over the same socket and fire through the existing SMS-path modules. Also note: the wiring is **inbound-answer** (AI picks up when the customer dials, via `<Connect><Stream>` on `voice_answer: true` tenants) — not the outbound missed-call callback the prior entry described.

**Why:** Deepgram's managed API takes the two things that were the live risk in the prior plan — latency and barge-in on a raw media loop — off our plate, over a single socket. Far less plumbing than self-hosting STT + streaming TTS + the turn-taking loop. This is essentially the "ConversationRelay-style managed fallback" the prior entry pre-authorised, landed on Deepgram instead of Twilio so we keep one vendor for STT+TTS+brain. The trade brain is reused, not rewritten: `voice_engine.VOICE_MODE` + the `workflow.py` SYSTEM_PROMPT become Deepgram's `think.prompt`, `voice_engine.TOOLS` become its `think.functions`.

**Trade-offs / constraints:** Deepgram hosts the LLM, so the model id is pinned to `claude-sonnet-4-20250514` (their Anthropic enum lags — newer ids are rejected; SMS path is on Sonnet 4.6). The long example exchanges + knowledge file are dropped from the voice prompt to fit Deepgram's prompt cap (`_lean_voice_prompt`). The self-hosted `stream_voice_turn` path (voice_engine.py, Haiku) stays **dormant as the fallback** and is what `evals.py --mode voice` exercises — the prompt is shared, so prompt tuning transfers, but the eval does not exercise the live Deepgram path.

**Status:** Settings schema, Aura-2 greeting audio, and all 3 functions **validated via `/voice/probe` against the live key**. **NOT yet verified on a real call** (FunctionCall round-trip + barge-in + the hosted-LLM first turn are unexercised). Code compiles clean.

**Open issues — both resolved 2026-06-26 (same session):**
1. ~~Blocker: `DEEPGRAM_API_KEY` missing from `product/speed-to-lead-demo/.env`.~~ **Fixed** — copied from the root vault's `Deepgram_API` into the product `.env` (the app's `load_dotenv()` resolves to the product `.env`, never the root). For the live VPS test it must also be in the Docker env.
2. ~~Prompt/wiring mismatch: `VOICE_MODE` framed the call as an outbound callback while the wiring is inbound-answer.~~ **Fixed** — rewrote `voice_engine.VOICE_MODE` to inbound-answer ("the customer rang {business} and you answered… don't say you're ringing them back"). This also removed a contradiction the voice eval was feeding the model (every eval scenario starts with the customer speaking first).

**Also shipped this session (supporting reliability + proof layer):** webhook **idempotency** guards (`store.mark_seen` / `processed_events` table — stops Twilio retries double-texting/double-booking); a **synthetic canary** (`canary.py` + `/health/deep`) that probes DNS→TLS→Caddy→app→Claude→DB and alerts on `@BaoBei09bot`; an **owner ROI report** (`report.py` + `/dashboard/{tenant}/report` — conservative booked-jobs-only $ value, the proof that justifies the invoice); and a **goal-driven auto-fix loop** (`goal_loop.py` + `run_loop.ps1`) that measures the voice eval and lets `claude -p` apply one allowlisted prompt edit per pass until safety-100%/overall-≥90%. Plus trade-brain prompt hardening in `workflow.py` (book sooner, no-invent rule, emergency-safety per tenant).

**Update (live-call debugging, 2026-06-26):** First real calls dropped ~5s in. Two stacked causes: (1) the dialled AU number routed to the **RapidFlow** tenant, which was `voice_textback_only` (the old say-and-text path) — switched it to `voice_answer`. (2) The voice agent then dropped right after the greeting because Deepgram's hosted Anthropic returned **404 not_found for `claude-sonnet-4-20250514`** (still on Deepgram's allowlist but retired upstream) → `THINK_REQUEST_FAILED` → `FAILED_TO_THINK` → socket close → call ends. Fixed by switching `DG_THINK_MODEL` to **`claude-sonnet-4-6`** (current, on Deepgram's allowlist, matches the SMS engine; verified live by injecting Deepgram-TTS speech and confirming an LLM reply + `alert_owner` tool-call with no error). Also added Deepgram `Error`/`Warning` logging + `PYTHONUNBUFFERED=1` so future think failures surface in `docker logs`. **Lesson:** Deepgram's model allowlist ≠ what's actually live upstream — pin to a current id, and a dead think model fails *silently* (clean socket close) unless you log the `Warning`/`Error` events.

**Owner:** Hughie

---

## 2026-06-26 - Voice latency/naturalness tuning + post-call owner summary shipped live

**Decision:** The live Deepgram voice agent now prioritises lower latency and a more natural call close. Current live voice config: Deepgram Flux listen (`flux-general-en`, `version=v2`, `eot_threshold=0.65`, `eager_eot_threshold=0.45`, `eot_timeout_ms=1500`), Anthropic hosted think model `claude-haiku-4-5` at temperature `0.3`, and Deepgram Aura voice `aura-2-hyperion-en`. The server now queues `alert_owner` and `book_job` actions during the call, then sends one consolidated owner SMS/Telegram summary after the call ends. `end_call` waits about 2 seconds after the goodbye, hangs up, then flushes queued owner actions. The Twilio stream also passes `caller` and `call_sid` into `/twilio/voice-stream`, so post-call summaries identify the caller.

**Why:** Immediate Telegram/SMS/calendar work during the live call risks adding latency and makes the conversation feel less natural. The caller should hear only the receptionist flow; the owner should receive one clean handoff after the customer is finished. The model is instructed to ask "Anything else I can help you with?" before closing; if the caller says no, it gives a short warm goodbye and ends the call.

**Verification:** Local product suite passed (`41 passed`). Deployed to VPS `/opt/speed-to-lead` with backups `.bak/20260626-113123/` and `.bak/20260626-113803/`; rebuilt with `docker compose up -d --build app`. Verified public health `https://stl.187-77-133-39.sslip.io/health` = ok, inside-container `/health` = ok, clean startup logs, and live container config shows Flux v2 + Haiku + Aura Hyperion. `/voice/probe` shows `Welcome` + `SettingsApplied` + greeting audio; its `ok:false` remains expected because the probe still looks for an old `Ready` event this Deepgram API version does not send.

**Affects:** `product/speed-to-lead-demo/app.py`, `voice_server.py`, `voice_engine.py`, `tests/test_voice_server.py`, `tests/test_webhook.py`.

**Owner:** Hughie

---

## 2026-06-27 — Voice: Cartesia→Aura revert (Australian voice) + Deepgram handshake pre-connect

**Decision (3 changes, all live on `https://stl.187-77-133-39.sslip.io`):**
1. **Revert TTS Deepgram Cartesia → native Aura.** The 2026-06-26 Cartesia/Katie switch was the wrong call: Katie was **en-US** (lost the Australian accent) **and** added a separate TTS provider hop on top of the ~800ms Deepgram socket connect. Aura is Deepgram-native (no extra hop). `DEEPGRAM_TTS_PROVIDER` cartesia→deepgram in the VPS `.env`.
2. **Female Australian voice:** `DEEPGRAM_VOICE=aura-2-theia-en` (was unset → code default `aura-2-hyperion-en`, male). Verified it resolves via `/voice/probe` (SettingsApplied, no Error). The code default stays `hyperion` so `test_voice_agent_defaults…` still asserts it.
3. **Handshake pre-connect (option b).** Open the Deepgram WebSocket during Twilio's ring (fired from `app.py:twilio_voice` at webhook time, keyed by `CallSid`) and reuse it when the caller's media arrives — hides the cross-ocean handshake (the box is in Brazil) behind the ring. **Settings is NOT sent on the pre-connection**, only the bare socket, so the greeting still speaks into a live bridge (sending Settings early would speak the greeting into a socket with no caller yet and lose it). Env kill-switch `VOICE_PRECONNECT=0`; any failure silently falls back to a fresh open on the stream = today's behaviour.

**Why:** Hughie's live ear caught that the post-Cartesia voice felt slower and lost its accent. Confirmed on the box: `dg_connect_ms≈791`, `first_agent_audio_ms≈2001`, dominated by the per-call Deepgram socket connect (not TTS). Aura removes the extra hop and restores the accent in one move; the pre-connect targets the handshake itself. The full-open-early + buffer-greeting version (option a) was rejected: it can't be tested without a real call (the synthetic `voice_stress.py` harness connects to Deepgram *directly*, bypassing the Twilio media-stream path) and the greeting-timing logic is exactly the kind of subtle thing that breaks a live demo.

**Honest verification state:**
- Unit-tested the coordination logic (3 new tests: preconnect-hit reuses the warm socket with `dg_connect_ms=0`; preconnect-failure falls back to fresh; kill-switch opens fresh) — **8/8 pass** in-container. Plus `/health` ok, probe clean, startup clean.
- **NOT verified end-to-end:** neither the probe nor `voice_stress.py` exercises the media-stream path, so the actual latency win + no-call-drop is confirmed only by a real phone call. `docker logs speed-to-lead-app-1 | grep "voice latency"` will show `preconnect=hit|miss` on real calls — a high hit rate = the win landed. If a real call regresses, `VOICE_PRECONNECT=0` + recreate kills it instantly with no redeploy.

**Trade-off noted:** on the rare race where media arrives fast AND the Deepgram connect is pathologically slow that call, the stream waits ≤`PRECONNECT_WAIT_S` (0.5s) then opens fresh — up to ~0.5s worse than today on that one call, never a broken call. Tunable via env.

**Alternatives considered:**
- *Option a (pre-open + send Settings early, buffer the greeting)* — bigger latency win but untestable blind and carries the greeting-loss landmine; rejected for a live demo that just regressed.
- *Leave latency, only do the voice revert* — Hughie explicitly asked for the pre-connect; built it with the safety contract instead.
- *Keep Cartesia* — rejected; lost the accent and added a hop for no benefit over native Aura.

**Affects:** `product/speed-to-lead-demo/voice_server.py` (preconnect registry + `_take_or_open_dg` + `_open_dg_authed`/`_safe_close`/`_reap_preconnect`; `voice_stream` now take-or-open then send Settings; latency log gains `preconnect=`), `app.py` (fires `preconnect_call` in the `voice_answer` webhook branch), `tests/test_voice_server.py` (3 new tests + the defaults test made env-independent). VPS `.env`: `DEEPGRAM_TTS_PROVIDER=deepgram`, `DEEPGRAM_VOICE=aura-2-theia-en`, `VOICE_DEBUG_EVENTS=0`, `VOICE_PRECONNECT=1`. Owner-phone routing already `+61402129328` via `ELECTRICIAN_MOBILE` (the `dave` tenant has no `owner_mobile` override) — no change. Deploy: scp `voice_server.py`/`app.py` → `docker compose up -d --build app`; pre-existing files backed up to `/opt/speed-to-lead/.bak/preconnect/`. `VOICE_DEBUG_EVENTS=1` (left on from last session's debugging) turned off.

**Owner:** Hughie
