# Electrician Speed-to-Lead Workflow v1

**Goal:** Catch every inbound electrician enquiry — especially missed calls — reply in seconds, qualify the job, triage emergency vs. quote, then book it or hand the sparky a clean summary. 24/7, no human babysitting.

**Vertical:** Residential & light-commercial electricians — solo operators and small crews.

**Model:** GetLeadMate's mechanic (instant reply → qualify → quote → book, 24/7), rebuilt **SMS-first with missed-call-text-back** — the channel GetLeadMate is weakest on (they're Messenger-first). v1 is delivered done-for-you for one Wollongong electrician to capture a real before/after stat, but built as a **clonable template** so client #2 launches in days.

**Persona:** Named human. Solo sparky → reply *as the electrician* ("Dave here — sorry I missed you, I'm up a ladder…"). Small crew → a named booking coordinator. Configurable per client; honest the moment it's asked whether it's a bot.

---

## Why this isn't the dental drip

The dental v1 was a 14-day, 5-touch email nurture — those leads are slow, planned, comparison-shopped over weeks. Electrician leads are the opposite: **urgent, already decided, and ringing 2–3 businesses right now.** The product isn't a drip — it's a fast conversation that has to happen in the first five minutes. Speed is the entire moat. Email is dead here; tradies' customers call.

## The core loop (GetLeadMate mechanic)

Capture → Instant reply (<60s) → Qualify → Triage → Book *or* escalate → Log → Light follow-up if undecided.

---

## Success criteria for v1 (demo-ready)

- Missed call → SMS back within **60 seconds**.
- Web form / FB / IG enquiry → reply within **60 seconds**.
- AI qualifies job type, urgency, suburb, and timeframe with no human input.
- Emergency / safety language → electrician alerted within **2 minutes** (call + SMS), customer reassured.
- Standard job → books an arrival window, *or* produces a clean job summary for the sparky to quote.
- Honest disclosure when asked "is this a bot?"
- Every number compliant: replies only to inbound enquiries, STOP honored instantly.

## Out of scope for v1 (named to prevent drift)

- **Live inbound voice AI** (a voice agent answering the call) — Phase 2. v1 is missed-call-*text*-back.
- **Automated quoting / pricing** — v1 hands job details to the electrician; the licensed human quotes.
- Payment / deposit collection.
- Multi-tech dispatch and schedule optimisation.
- Post-job review-request sequence (separate funnel).
- Channels beyond SMS + web form + FB/IG (WhatsApp later).

---

## Channels — v1

| Channel | Role |
|---|---|
| **Missed-call-text-back (SMS)** | Headline feature. Unanswered call → instant SMS. Primary. |
| **Website "get a quote" form** | Form submit → same flow. |
| **Facebook / Instagram message** | Inbound DM → same flow. |
| Live inbound voice | Phase 2 — not v1. |

---

## The workflow — stage by stage

### 1. Capture (trigger)
Sources: missed call on the business number, website form, FB/IG message. Each normalises to a lead object: `{name?, phone, channel, message?, suburb?, timestamp}`.
Missed-call detection: the electrician's **existing business number is ported** onto the telephony provider (Twilio). Inbound calls ring the electrician's mobile via a Dial-with-timeout; no-answer / busy / rejected → the provider fires the missed-call webhook and the text-back goes out. The customer dials — and is texted back from — the electrician's own number, so the automation stays invisible. (Porting takes a few business days; the digits never change. Chosen over conditional call-forwarding — which leaves the text-back on a second number and can drop the original caller's ID — see decisions log 2026-05-22.)

### 2. Instant reply (<60s)
First SMS — acknowledge, set the persona, ask one qualifying question.
> "Hi, it's Dave from Dave's Electrical — sorry I missed your call, I'm on the tools. What do you need a hand with, and what suburb are you in?"

### 3. Qualify
Conversationally, one question at a time:
- **Job type** — fault / no power, switchboard, powerpoints & lights, appliance or fan install, renovation / rewire, safety inspection, other.
- **Urgency** — emergency now / this week / flexible.
- **Suburb** — service-area check.
- **Property type** — home / business / rental (rental → landlord-approval flag).

### 4. Triage — the branch
- **A. Emergency** — sparks, burning smell, exposed or live wires, smoke, no power with a medical need. → Escalate immediately: call + SMS the electrician with an `[URGENT]` summary; tell the customer "I'm getting Dave to call you straight away — if there's smoke or fire, call 000 now."
- **B. Bookable job** — standard schedulable work (replace powerpoints, install a fan, switchboard upgrade). → Stage 5.
- **C. Quote-first job** — bigger or unclear scope (rewire, reno, "how much for…"). → Capture scope + request photos → clean summary to the electrician to quote. The AI never quotes.

### 5. Book
Offer **two specific arrival windows** — electricians work in windows, not exact times ("Tuesday 8–11am", not "8:30"). Confirm → calendar event → confirmation SMS with what to expect and the **callout fee disclosed upfront**.

### 6. Handoff / escalation
Every escalation and quote-job sends the electrician a structured summary: name, number, suburb, job type, urgency, the customer's own words, photos, recommended action.

### 7. Log
Every lead → CRM / sheet: source, job type, outcome, response time, booked value if known. **This is what produces the before/after proof stat.**

### 8. Follow-up (light — not a drip)
If undecided: one nudge at ~2h, one at ~24h, then stop. Tradie jobs are decided fast — a lead cold for a day has already booked someone else.

---

## Reply branching (intents)

| # | Intent | AI action |
|---|---|---|
| 1 | Describes a job | Qualify (Stage 3) |
| 2 | "How much / can you price it" | Quote-first path — capture scope + photos, hand to electrician |
| 3 | Emergency / safety language | `[URGENT]` escalate; advise 000 if fire or immediate danger |
| 4 | Picks a time | Confirm → calendar event → confirmation SMS |
| 5 | Wants a different time | Offer 2–3 arrival windows — never dump full availability |
| 6 | "Just call me" | Confirm best number + window → `[HOT LEAD]` alert |
| 7 | "Do you do X / service my area?" | Answer from client config; out of area → refer out politely |
| 8 | Rental / not the owner | Flag landlord-approval needed before booking |
| 9 | "Are you a bot?" | Honest: "I'm an AI assistant helping Dave keep up with calls — want Dave to ring you direct?" |
| 10 | Not interested / sorted it | Polite close → log reason → stop |
| 11 | STOP / unsubscribe | Honor immediately → opt-out flag → stop |

## Escalation rules

| Trigger | Channel | SLA | Tag |
|---|---|---|---|
| Emergency / safety language | Call + SMS | 2 min | `[URGENT]` |
| "Call me" request | SMS | 15 min | `[HOT LEAD]` |
| Quote-first job | SMS | 1 hr | `[QUOTE]` |
| Out-of-hours emergency | Call + SMS | 2 min | `[URGENT]` |
| Complaint / negative sentiment | SMS | 30 min | `[ATTN]` |

## Stop conditions

Job booked · opt-out received · "not interested" reply · 24h elapsed after the second nudge with no reply.

---

## Compliance (Australian — read before build)

- **Spam Act 2003:** SMS replies to a customer-initiated enquiry are responding to their request — not unsolicited marketing. Safe. Still: identify the sender in the first message and honor STOP instantly. Do **not** repurpose collected numbers for marketing blasts without express consent.
- **Electrical licensing:** the electrician must hold a current NSW licence. The AI must **never give electrical advice or DIY instructions** — safety and liability. It triages and books only.
- **Emergency duty of care:** if a customer describes fire, smoke, or immediate danger, the script tells them to call **000** first, then escalates. Don't let the bot "handle" a dangerous situation.
- **Persona disclosure:** truthful the moment it's asked. Never claims to be human — just leads with a real name.
- **Privacy:** store addresses and photos minimally; delete on request.

## Build stack (proposed)

| Layer | Tool | Note |
|---|---|---|
| Orchestration | n8n | AAA default; clone the template per client |
| SMS + missed-call | Twilio (AU) | Port the electrician's existing number onto it — customers see his real number |
| Conversation / intent | Claude API | Haiku to classify, Sonnet to reply |
| Calendar | Google Calendar or Cal.com | Sparkies already use Google |
| CRM / log | Airtable or Google Sheet | v1 simplicity |
| Escalation | Twilio SMS to electrician | Structured summary |

GetLeadMate runs OpenAI + Supabase + Meta. We copy the **mechanic, positioning, and per-vertical template** — not the stack. n8n keeps it clonable; SMS-first is the wedge.

## Open questions (resolve before build)

1. **Founding electrician** — who? (warm contact / walk-in in Wollongong)
2. **After-hours policy** — does the electrician take 2am emergencies, or is the answer "first thing AM"?
3. **Service area** — exact suburbs covered.
4. **Callout fee** — confirmed it's disclosed in the booking SMS? (Recommended: yes.)
5. **Calendar** — Google Calendar or Cal.com.
6. **Quote photos** — how does the customer send them — MMS, or a link to upload?

*Phone setup resolved 2026-05-22 — port the electrician's number (see decisions log).*

## What gets built next (after questions answered)

1. Twilio account + **US dev number** + missed-call webhook — build and test the whole flow on this (US numbers are free and instant, no AU regulatory paperwork)
2. n8n orchestrator — capture → branch
3. Claude intent classifier + reply generator
4. Calendar integration
5. Electrician escalation router (SMS)
6. Logger + the before/after proof dashboard
7. Test harness — synthetic leads end-to-end before any real customer touches it
8. **Go-live** — port the founding electrician's existing AU number (submit the AU regulatory bundle, then cut over from the dev number). The only step that needs an Australian number.

## Delivery pipeline (per client)

The section above builds the *template* once. This is the repeatable process for shipping it to **each** client. There is no clean "handoff" — porting the number means you cut over and then *operate* the client's inbound permanently. After go-live the electrician's involvement is ≈ zero (that's the product); the operator's never is.

| Stage | What happens | Notes / bottleneck |
|---|---|---|
| **0. Intake** | Discovery call → fill the per-client config: the open questions in this spec + persona name, job types, escalation number, calendar access. Data collection, not engineering. | Use a structured intake form, not a freeform meeting. |
| **1. Build** | Clone the n8n template, drop in the config, wire the client's Google Calendar + a Twilio sub-account, point the capture sources (form, FB/IG). **Config, not code** — if a client needs new code, repeatability has broken; fix the template instead. | This is where repeatability lives or dies. |
| **2. QA** | Synthetic leads end-to-end on the free US dev number — all 11 intents, every escalation, the emergency path, "are you a bot", booking lands in the calendar, missed-call-text-back fires. **Plus the failover test:** kill the automation and confirm an inbound call still rings the electrician's mobile (decisions log 2026-05-22 — failover). Fixed checklist. | Nothing real touches the client until this passes. |
| **3. Port + cutover** | Submit the AU porting bundle, wait (days–weeks, carrier-gated), then cut over from the dev number to the live ported number. Schedule the cutover deliberately — never a busy weekday morning. | The long pole. A port can stall or fail; conditional call-forwarding is the documented fallback (decisions log 2026-05-22). |
| **4. Babysitting** | 1–2 weeks watching every conversation near-live: tone misfires, wrong job-type branches, escalations that didn't fire. Tune as you go. | Not optional, not yet automatable — caps how many clients launch in parallel. |
| **5. Steady state** | Monthly outcome report to the client, periodic tuning from the raw data layer, ongoing uptime ownership. | Recurring revenue = recurring obligation. |

**Handoff to the electrician** is a 15-minute brief, not a delivery: what they'll now see, how the escalation summary reaches them, how to read the monthly report.

**Don't write the full delivery runbook before client #1.** Client #1 is where the process is *discovered* — the intake form, QA checklist, and SOP get extracted from it, not designed ahead of it. Designing the perfect pipeline first is the decision-paralysis trap.

## The proof play

v1 exists to produce **one number**: *"[Electrician] was missing ~X calls/week. After: Y% answered instantly, Z jobs booked, ~$_ recovered per month."* That stat is the case study — and it's exactly what GetLeadMate still doesn't have after ~6 months live. Land it, film it, and you out-credential the competitor.
