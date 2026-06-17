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

