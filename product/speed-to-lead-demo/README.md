# Electrician Speed-to-Lead — Demo

A working demo of the v1 product: an AI that catches an inbound electrician
enquiry, qualifies the job, triages emergency vs. quote vs. bookable, and books
it or alerts the sparky — in seconds.

It's a **web simulator**: two phones on one screen. You play the customer; the
real AI (Claude) runs the actual qualify → triage → book workflow; when it hits
an emergency or books a job, **Dave's phone lights up**. Built to show a
prospective electrician what the product does on a laptop — no telephony, no
setup, can't fail in a meeting.

The AI engine (`workflow.py`) is the real product brain. Production swaps the
front door — this web UI — for a Twilio missed-call-text-back webhook. Nothing
about the engine changes.

Full plan: `../../references/electrician-speed-to-lead-workflow.md`

## Run it

From this folder, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# open .env and paste your Anthropic API key
python -m uvicorn app:app --reload
```

Then open **http://127.0.0.1:8000**

## Demoing it

Three one-click scenarios run the whole thing in 60 seconds:

- **Emergency — sparks** → AI reassures the customer, says call 000 if it
  worsens, and fires an `URGENT` alert to Dave.
- **Quote — rewire** → AI captures the scope, asks for photos, sends Dave a
  `QUOTE REQUEST`.
- **Standard — fan install** → AI qualifies, offers two arrival windows, books
  it, and sends Dave a `JOB BOOKED` alert.

Every enquiry also buzzes Dave the instant it lands — a `NEW LEAD` heads-up —
before the AI has finished qualifying. The richer `URGENT` / `QUOTE REQUEST` /
`JOB BOOKED` alert follows once the AI knows more.

You can also free-type as the customer. The "What the AI worked out" panel
shows the structured state — job type, urgency, suburb, triage — updating live.

When a customer goes quiet before booking, a **fast-forward** control appears
under the composer. Click it to jump ahead in time and watch the AI chase the
lead — a warm nudge a couple of hours later, one more the next day, then it
stops. Roughly half of tradies never follow up at all; this is where the
booked jobs hide.

The **scorecard** under the phones tallies the whole session — enquiries
caught, jobs booked, emergencies escalated, quotes sent, and an estimated
dollar value. Each scenario chip starts a fresh enquiry and banks the last
one into the score, so a few clicks build the number that closes the sale.
Dave's phone keeps every alert across enquiries; **Reset** clears everything.

## How it works

- `app.py` — FastAPI. Serves `index.html` and two endpoints: `POST /api/message`
  (a conversation turn) and `POST /api/followup` (a nudge for a quiet lead).
- `workflow.py` — the engine. Sends the SMS thread to Claude (`claude-haiku-4-5`)
  and gets back a structured turn: the reply, the triage decision, the
  qualified fields, the alert for Dave, and the booking.
- `knowledge.md` — the electrical-trades knowledge base the engine loads into
  its prompt: job taxonomy, emergency criteria, quoting practice, NSW
  compliance. Edit this to make the AI smarter — no code change needed.
- `evals.md` — a fixed set of test conversations with known-good outcomes. Run
  them after any change to `workflow.py` or `knowledge.md` to confirm you
  improved the assistant rather than regressed it.
- `index.html` — the two-phone simulator UI (no build step, no dependencies).

The browser holds the conversation and sends the full thread each turn — the
engine is stateless, exactly as it will be behind a webhook.

## Notes

- **Prompt caching** is wired on the system prompt (`cache_control: ephemeral`).
  With the workflow rules plus `knowledge.md`, the cached prefix is ~5,400
  tokens — past Haiku 4.5's 4,096-token minimum — so caching is live: repeat
  turns read the prompt from cache at ~0.1x cost instead of full price. Each
  turn prints a `[usage]` line (watch `cache_read`) so you can see it working.
- **Cost** — roughly a fraction of a cent per message turn. A full demo run is
  negligible.
- **Not production** — no real phone number, no calendar, no database. This is
  the sales demo. The production build (Twilio, n8n, calendar, escalation
  routing) is the next step once a founding electrician is signed.
