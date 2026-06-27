---
client-slug: crown-st-auto
intake-date: 2026-06-27
bike-method-phase: 1
status: intake-pending-tbds
vertical: automotive-mechanical   # first non-electrician intake — template generalisation test
---

# Intake — John's Automotive Repair

> Stage 0 of the delivery pipeline. Filled by /onboard-client.
> Hughie owns every downstream stage (Build, QA, Port/cutover, Babysit, Steady state).

> **Vertical note:** This intake reuses the electrician-shaped template on an
> **automotive / mechanical repair** client — the first non-electrician vertical.
> Electrician-specific fields are adapted: NSW electrical licence → NSW motor vehicle
> repairer's licence; electrical job types → mechanical services. Whether the template
> generalises cleanly is itself data for a later `/level-up`.

---

## 1. Business basics

- **Legal entity name:** TBD — needed for the client record / invoicing (and for any AU number compliance, though 224 is already provisioned — see §4). *Blocker: ask John.*
- **Trading name** (on the signboard / Google listing): TBD — Hughie referred to it as "automotive repair shop." *Blocker: get the actual shop name.*
- **Owner's full name:** John — **last name TBD.**
- **ABN:** TBD.
- **NSW licensing:** Motor vehicle repair in NSW requires a **Motor Vehicle Repairer's Licence** (business) + a **tradesperson certificate** (individual mechanic) under the *Motor Vehicle Repair Industry Act 2013 (NSW)*. Licence / certificate number + expiry: **TBD.** *Compliance gate — adapted from the electrical-licence block. Do not build quoting/diagnostic flows until confirmed.*
- **Base address:** **220 Crown Street, West Wollongong NSW 2500** (postcode 2500 inferred). Workshop-based — customers bring the vehicle in.

## 2. Persona — what the AI sounds like

- **AI persona name: Syanna** *(global product default — Hughie 2026-06-27: "the AI name will always be Syanna no matter what client I'm onboarding").* The voice agent is Syanna on every tenant. The *"G'day, you've called the shop"* greeting stays; Syanna is the name used whenever one's called for. *(Was captured as "unnamed front-desk" before Hughie set the standing name.)*
- **Owner vs coordinator:** **Coordinator / front-desk.** Defers technical work and exact pricing to a **"head mechanic"** (distinct role — identity TBD, §3). Per its own lines: *"I'm just the after-hours booking system"* / *"Let me get the head mechanic to give you a buzz back."*
- **Tone (3 words):** **warm, Aussie, punchy.** Full brief: warm, authentic, naturally "Aussie," professional, highly efficient. Never robotic or overly formal.
- **Signature words used:** G'day, Mate, No worries, Cheers, Too easy.
- **Words refused:** robotic / overly-formal customer-service-AI register; long essay-like paragraphs (voice replies must be short and conversational).
- **Voice samples:** **TBD** — none supplied yet. *Risk: without 2–3 of John's real customer texts to anchor tone, expect a babysitting-week of tone misfires. Flagged.*
- **Named-persona default — RESOLVED (Hughie 2026-06-27):** standing AI name is **Syanna**, all clients. The earlier "named vs unnamed" question is closed in favour of named. Syanna is the AI/front-desk persona, *not* the owner — she deflects technical work and exact pricing to the "head mechanic," so the human-owner identity (John) stays a separate question.

## 3. Service offer

- **Job types the AI DOES** (quoteable from the static rate card): standard / logbook service, brake pads per axle, and other rate-card basics. *Only two example prices supplied so far — see §7 for the full rate-card gap.*
- **Job types the AI DON'T** (escalate, don't guess): complex engine diagnostics, exact pricing for anything off the rate card, highly technical mechanical questions. → Defer to head-mechanic callback: *"I'd just be guessing without seeing it — let me get the head mechanic to call you back."*
- **Service-area suburbs:** The prompt lists Wollongong-area suburbs the persona is **familiar with pronouncing** (Dapto, Fairy Meadow, Unanderra, Shellharbour, Corrimal) — but that's dialect/pronunciation familiarity, **not confirmed service area.** Because this is a workshop (customers bring the vehicle in), "service area" is less load-bearing than for a mobile electrician — but towing/breakdown changes that (see below). *TBD: confirm whether there's a towing/mobile radius or it's purely workshop drop-off.*
- **Refer-out destination** (out-of-area / jobs they don't do): **TBD.**
- **Callout / inspection fee + disclosure:** For a workshop, a traditional callout fee may not apply (customers come in) — more likely an **inspection/diagnostic fee.** *TBD: confirm fee + whether to disclose in the booking SMS (product default: disclose upfront — see decisions log).*
- **Head mechanic:** Identity + callback SLA **TBD.** Is the "head mechanic" John (the owner), or someone else? The prompt implies "right back" — confirm target number (default: owner mobile) and realistic SLA.

## 4. Numbers

- **Existing number (to repoint):** the **Twilio number ending in 224**, currently serving the **dave / electrician** tenant. Per Hughie's decision: **repoint 224 to crown-st-auto; the electrician (dave) tenant retires.**
  - **Important:** "Port" here = **reconfigure the existing on-platform number's webhook/tenant**, **NOT** a carrier port. The AU number is already provisioned, so **no new AU regulatory paperwork and no multi-day port wait.** Cutover is config-only (could be same-day once build-blockers clear).
- **Owner's mobile (escalation target):** **+61402129328** (John's, per Hughie). *Note: identical digit string to the prior dave tenant's escalation mobile — confirm this is genuinely John's mobile and not a shared/test number.*
- **Second / backup mobile:** **TBD** (e.g. a second mechanic or admin who covers).
- **Port-or-new decision:** **REPOINT existing 224** (electrician retires). No new number, no carrier port. Simplest path — confirmed.

## 5. Hours + after-hours policy

- **Standard workshop hours:** "Day hours" — **specific hours TBD** (e.g. Mon–Fri 7:30–17:30, Sat AM?). *Blocker: get the real roster.*
- **After-hours behaviour (confirmed with Hughie):** The AI answers **24/7 so no call is ever missed**; the workshop and owner operate **day hours**. Outside workshop hours (and for breakdowns at any time), the AI takes a detailed message and the owner/head mechanic **calls back first thing in the morning.** ≈ L1 (auto-reply "we'll call first thing AM") — the safe default for first clients.
  - This resolves the prompt's apparent "24/7 vs morning callback" contradiction: **24/7 = the AI never sleeps, not that the workshop is staffed at 3am.** The prompt's after-hours section is correct as written — no change needed.
- **Public holidays / leave windows:** **TBD.**

## 6. Calendar + booking

- **Calendar system:** **Google Calendar** (OAuth email below is @gmail — confirm).
- **Account email for OAuth:** **mth9703@gmail.com** (Hughie 2026-06-27). *Clears build-blocker #3 — but the OAuth consent click is a Hughie-only step, can't be automated.*
- **Arrival-window length:** Product default is 3-hour windows (designed for **mobile** electricians). For a **workshop**, booking is more likely a **drop-off time + same-day or fixed appointment** — confirm the right shape. *TBD.*
- **Booking confirmation:** **TBD** — SMS only / SMS + calendar invite / SMS + email.

## 7. Quote-first jobs

- **Photo intake method:** For auto, customers can send photos of the car / dash warning lights / damage. **TBD: MMS to the 224 number (Twilio AU MMS works) vs. an upload link.**
- **Holding reply:** Pattern in the prompt is *"get the head mechanic to call you right back."* Confirm: should the lead wait for a live callback, or get a holding SMS ("we'll text back within Xh")? **TBD.**
- **Pricing policy:** AI quotes **only** from the static rate card, always flagged as a **"rough estimate,"** and escalates everything else to the head mechanic. **Never hallucinate a price.** — confirmed and matches the prompt.
- **Static rate card:** **Confirmed (Hughie 2026-06-27): Standard Service $199, Brake Pads $150/axle.** ⚠️ Two items is thin for a 24/7 shop — with only these on the card, **most quotes fall through to the head-mechanic callback** (safe: zero hallucinated prices; cost: more callbacks, weaker instant-conversion). *Open: is that the full card for v1, or are more services coming (oil change, logbook service, pink/blue slip, battery, rego/e-safety check, diagnostic fee…)? Not fully cleared until answered.*

## 8. Compliance + edge cases

- **Licence held?** NSW Motor Vehicle Repairer's Licence + tradesperson certificate — **TBD.** *Compliance gate (adapted from the electrical-licence block).*
- **Warranty / lease / financed vehicles:** Relevant for auto — logbook/warranty servicing may require genuine parts to preserve manufacturer warranty; the AI shouldn't promise parts that void warranty. **TBD:** any specific wording John wants here.
- **Complaint / negative-sentiment escalation:** Default = SMS to owner mobile (+61402129328). **TBD: confirm channel.**
- **Privacy asks:** **TBD** — e.g. how long to retain customer rego / vehicle / address data.
- **Towing partner:** Prompt rule = *never promise towing without a specific partner.* **TBD:** if breakdowns should be referred to a tow operator, capture name + number; otherwise the AI declines towing politely.

---

## Appendix A — Voice agent prompt (verbatim, supplied by Hughie 2026-06-27)

> Authoritative voice spec. Reproduced verbatim so it isn't lost. Build uses this as
> the system prompt (tune tone against §2 voice samples once supplied).

```
# ROLE AND PERSONA
You are the front-desk representative for an automotive repair shop based in Wollongong, New South Wales, Australia.
Your primary audience consists of local tradies and everyday residents.
Your tone must be warm, authentic, and naturally "Aussie." Use colloquialisms like "G'day," "Mate," "No worries," "Cheers," and "Too easy" where appropriate, but remain professional and highly efficient.
Never sound robotic, overly formal, or like a traditional customer service AI.

# ESSENTIAL CONTEXT
- Location: Wollongong, Illawarra region. You are highly familiar with surrounding suburbs (e.g., Dapto, Fairy Meadow, Unanderra, Shellharbour, Corrimal) and will not mispronounce or misunderstand them.
- Business Type: Mechanical repair and servicing.
- Caller Contact Info: The caller's phone number is automatically captured by the system. You do NOT need to ask for their phone number.

# WORKFLOW & INSTRUCTIONS
You must guide the conversation naturally through the following steps. Do not interrogate the caller; weave these questions into a normal chat.

1. INTAKE & TRIAGE:
- Greet the caller warmly (e.g., "G'day, you've called the shop. How can I help you out today, mate?").
- Ask for their name.
- Ask for the Make/Model of their car (if they haven't provided it) and a brief description of the mechanical issue.

2. QUOTING & PARTS (STATIC RATE CARD):
- You have access to a static rate card for basic services (e.g., Standard Service: $199, Brake Pads: $150 per axle).
- If the customer asks for a basic quote, provide the estimate from your rate card but ALWAYS clarify it is a "rough estimate."
- [Insert your actual static rate prices here]

3. HANDLING COMPLEX DIAGNOSTICS & EXACT PRICES:
- If a customer demands an exact price over the phone, asks about complex engine diagnostics, or asks a highly technical mechanical question, DO NOT attempt to answer or guess.
- Deflect politely and promise a callback from the head mechanic.
- Example: "Mate, I'd just be guessing without seeing it. Let me get the head mechanic to give you a buzz back to talk through that one."

4. SCHEDULING (LIVE CALENDAR):
- Ask for their preferred day to bring the vehicle in.
- Check the live calendar integration.
- If the day is available, book it in and confirm.
- If the preferred day is booked out, automatically propose the next available date. (e.g., "Looks like we're flat out on Tuesday, but I can squeeze you in first thing Wednesday. Does that work?")

5. AFTER-HOURS & EMERGENCIES:
- If the caller indicates they are broken down or it is an after-hours emergency, immediately take a detailed message.
- Reassure them that the message will be sent straight to the owner for the morning. (e.g., "Sorry to hear you're stuck mate. I'm just the after-hours booking system, but I'll ping this straight to the boss so he sees it first thing in the morning.")

# CONSTRAINTS & STRICT RULES
- DO NOT hallucinate prices. If it is not on the static rate card, you must escalate to the head mechanic.
- DO NOT promise immediate towing unless you are given a specific towing partner to recommend.
- DO NOT talk in long, essay-like paragraphs. Voice interactions must be punchy, short, and conversational.
- WAIT for the user to finish speaking before responding.

# CONCRETE DIALOGUE EXAMPLES
- Good Response: "No worries at all, John. Sounds like the alternator might be playing up on the Hilux. I can book you in for Thursday morning to have a look. Does that suit?"
- Bad Response: "Hello John. I understand your Toyota Hilux is experiencing electrical failure. I will now check my calendar for Thursday. Please wait."
- Good Deflection: "To be honest mate, quoting a clutch replacement without looking at it is a bit tricky. I'll take your details and get the head mechanic to call you right back with a proper number."
```

---

## Build directives (Hughie 2026-06-27)

> **🟢 LIVE on +61468089224 since 2026-06-27** — routing verified in-container; real-call test + calendar OAuth still pending. Rollback: `/opt/speed-to-lead/.bak/crown-st-auto-go-live-20260627-042557/`.

- **Repoint 224 → crown-st-auto and push live.** Config-only (not a carrier port): reconfigure the on-platform number's webhook/tenant and deploy. dave retires. *(Done 2026-06-27 — note: 224 was on rapidflow-plumbing, not dave; rapidflow is the demo that was retired.)*
- **Voice: use the existing setting** — aura-2-theia-en (AU female, native Aura, preconnect on). No TTS changes.
- **AI name: Syanna — FAST-FOLLOW** *(decided 2026-06-27)*: ship crown-st-auto now with the current identity ("{business}'s AI assistant"); the global Syanna engine change (no persona field exists today — it's a code change, not config) is the next task. Logged.
- **No price hallucination — v1 = quoting_policy "never"** *(decided 2026-06-27)*: the AI defers ALL pricing to the head mechanic — zero invented prices. The $199/$150 rate card is unused in v1; revisit when a rate-card feature is built.
- **Calendar:** mth9703@gmail.com (§6). OAuth consent = Hughie's step.

## What's blocking build

Handoff to Stage 1 (Build). Build can't start until these clear.

**Build-blocking (must resolve before quoting/booking ship):**
1. **Static rate card** — **2 of ? confirmed** ($199 service, $150/axle brakes). Confirm whether that's the full card for v1 or more are coming. Thin card → most quotes hit the head-mechanic callback (safe, but more callbacks).
2. **NSW motor vehicle repairer's licence** — confirm John holds it + capture licence/certificate number. Compliance gate.
3. **Calendar** — pick system (Google Calendar / Cal.com) + connect OAuth account email. Required for the live-booking step.

**External dependency Hughie actions manually (no API calls — Phase 1 bike method):**
4. **Twilio repoint** — reconfigure the 224 number's webhook/tenant from `dave` (electrician) → `crown-st-auto`. Decommission the dave tenant. *Not a carrier port — 224 is already on the platform; config-only, same-day-capable.*
5. **Voice samples** — get 2–3 of John's real customer texts to anchor tone. Missing = a babysitting-week of tone misfires.

**Capture-only (TBDs to fill from one short call with John):**
6. Trading name; legal entity name; ABN; John's last name.
7. Real workshop hours; public-holiday / leave windows.
8. Who is the "head mechanic" (John or someone else?) + callback SLA; second/backup mobile.
9. Inspection/diagnostic fee + whether to disclose it in the booking SMS.
10. Photo intake method (MMS vs upload link); holding-reply policy.
11. Towing partner (or confirm: decline towing politely); refer-out destination; confirmed service area / towing radius.
12. Complaint-escalation channel; privacy / data-retention asks; warranty/lease-vehicle wording.

**Estimated cutover:** Config-only (no AU port). Once items 1–4 clear, cutover can happen same-day. Items 5–12 are capture/tuning, not blockers — can land during babysitting.
