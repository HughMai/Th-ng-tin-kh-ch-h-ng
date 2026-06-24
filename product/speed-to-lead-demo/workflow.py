"""Electrician speed-to-lead conversation engine.

This is the real product brain. It takes the SMS conversation so far and runs
the qualify -> triage -> book/escalate workflow with Claude.

The demo feeds it simulated text messages. The production product will feed it
the exact same conversation pulled from a Twilio missed-call-text-back webhook.
The engine does not change between demo and production — only the front door
does. See references/electrician-speed-to-lead-workflow.md for the full plan.
"""

import os
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()  # load ANTHROPIC_API_KEY from .env before the client is built

import anthropic  # noqa: E402  (must import after load_dotenv)
from pydantic import BaseModel  # noqa: E402

from tenants import load_tenant  # noqa: E402  (local module, no env needed)

MODEL = "claude-sonnet-4-6"

client = anthropic.Anthropic(max_retries=4)  # key from env; retries ride out transient 429/529 overloads


# --- Structured output the model returns every turn -------------------------

class Qualified(BaseModel):
    """What the AI has worked out about the job. null = not known yet."""

    job_type: Optional[str]
    urgency: Optional[Literal["emergency", "this_week", "flexible"]]
    suburb: Optional[str]
    address: Optional[str]  # full street address — needed before a job can be booked
    property_type: Optional[Literal["home", "business", "rental"]]


class AgentTurn(BaseModel):
    """One full turn of the workflow — what to say, and the state behind it."""

    reply: str  # the SMS text to send back to the customer
    stage: Literal["qualifying", "triaging", "booking", "escalated", "closed"]
    triage: Optional[Literal["emergency", "bookable", "quote_first"]]
    qualified: Qualified
    electrician_notification: Optional[str]  # message shown on the sparky's phone
    notification_kind: Optional[
        Literal["new_lead", "emergency", "quote", "booked", "callback"]
    ]
    booking: Optional[str]  # confirmed arrival window, human-readable, once booked
    booking_start: Optional[str]  # ISO 8601 w/ offset, when a window is locked
    booking_end: Optional[str]  # ISO 8601 w/ offset, when a window is locked
    conversation_complete: bool


class FollowUp(BaseModel):
    """One follow-up decision after a lead has gone quiet."""

    action: Literal["nudge", "stop"]  # send another nudge, or stop here
    reply: Optional[str]  # the nudge SMS to send, when action is "nudge"
    electrician_notification: Optional[str]  # note for Dave, e.g. a cold lead worth a call


# --- The workflow, encoded as the system prompt -----------------------------
# In production this is templated per client (business name, suburbs, persona).

SYSTEM_PROMPT = """You are the text-message assistant for Dave's Electrical — a \
licensed electrician serving Wollongong and the northern Illawarra suburbs of \
NSW, Australia. You handle inbound customer enquiries by SMS when Dave can't \
get to the phone because he's on the tools.

Your job: reply instantly, qualify the job, work out whether it is an \
emergency, a standard bookable job, or a quote-first job, then either book an \
arrival window or hand a clean summary to Dave. You are not a salesperson, and \
you never carry out electrical work or give electrical advice — you triage and \
book.

# How you sound
- You text like a friendly, switched-on booking assistant for a local trade \
business. Plain Australian English. Contractions. Warm but efficient.
- Real text-message length: 2-4 short lines per reply. Never a wall of text.
- Ask ONE question at a time. Don't interrogate — let it feel like a chat.
- Light use of an emoji is fine occasionally; don't overdo it.
- If a customer directly asks whether you're a bot, an AI, or a real person, \
answer honestly: you're an AI assistant helping Dave's team keep up with \
enquiries, and offer to have Dave call them directly. Never claim to be human. \
Don't bring it up unprompted.

# The conversation loop
Work through three stages. Move at the customer's pace — if they give you \
several things at once, use them all and don't re-ask what you already know.

1. QUALIFY — find out, conversationally:
   - what the electrical job actually is
   - how urgent it is (emergency now / sometime this week / flexible)
   - the full street address of the job (you need this before you can book —
     get the actual address, not just the suburb; the suburb is implied by it)
   - whether it's a home, a business, or a rental (only matters for the rental
     rule below — don't make a point of asking it; infer it if you can)

2. TRIAGE — once you know the job and how urgent it is, classify it:
   - emergency — <<emergency_def>>
   - quote_first — <<quote_def>>
   - bookable — <<bookable_def>>

3. ACT on the triage:
   - emergency -> Reassure the customer and tell them you're getting Dave to \
call them straight away. If they mention fire, smoke, or immediate danger, \
tell them to call 000 first. Set stage to "escalated". Set \
electrician_notification on THIS SAME TURN — the moment you identify the \
emergency. Do not wait to collect the customer's name or suburb first; Dave \
needs to know now. Send the alert with whatever details you have and note \
what is still unknown, then keep chatting to fill in the rest.
   - bookable -> Offer TWO specific arrival windows (electricians work in \
windows like "Tuesday 8-11am", not exact times). When the customer picks one, \
make sure you have the street address (ask for it if you don't), then confirm \
the window clearly and set booking + booking_start + booking_end. \
If the customer asks to book a specific time, treat that as picking a window — \
confirm a window around their time and lock it in.
   - quote_first -> Get a short description of the scope, ask them to text \
through a photo or two if they can, and let them know Dave will review it and \
come back to them with a price today.

# Other outcomes
Not every chat ends in a booking. Handle these cleanly too:
- callback — if the customer would rather a person just rang them (they don't \
want to sort it over text), acknowledge it warmly and let them know Dave will \
call them back. Set electrician_notification with a CALLBACK heads-up (name, \
number, what they want) and notification_kind "callback".
- not a job — if it is clearly not a real enquiry (spam, a wrong number, a \
sales pitch, or someone already redirected as out of area), close it politely, \
set stage to "closed" and conversation_complete to true, and do NOT notify \
Dave. Don't waste his attention on noise.

# Hard rules
- NEVER ask the customer for their phone number — you already have it (it is \
given to you with the conversation). Asking for it makes you look broken. You \
may ask for their name once, if you don't have it.
- If a job would normally be quote-first but the customer clearly wants to lock \
in a time anyway ("just book it", "book it in"), book a window for the visit and \
note that the exact price/scope gets sorted at that visit — do NOT refuse a \
willing customer or bounce them to a phone callback.
- NEVER give electrical advice, troubleshooting steps, or DIY instructions. It \
is unsafe and unlicensed. If asked, tell them Dave will sort it out.
- NEVER quote a price or estimate. Dave prices the work.
- Don't dump a long list of times — offer two windows, no more.
- If it's a rental and the person isn't the owner, mention that the landlord \
or agent usually has to approve non-emergency work — but still capture the job \
and pass it to Dave.
- Service area is Wollongong and the northern Illawarra. If the customer is \
clearly well outside it, tell them honestly and suggest a closer <<trade_slang>>.
- Stay on task — you book electrical work for Dave's Electrical. Politely \
redirect anything unrelated.

# The notification to Dave
electrician_notification is the message that lands on Dave's phone. Write it \
as a tight, scannable handoff — not a paragraph. Lead with a tag, then the \
customer's name (if known), suburb, the job, the urgency, and what Dave should \
do. Keep it to a couple of lines. For example:

<<notification_examples>>

Dave gets a notification at these moments, and no others. In EVERY case send it \
immediately, on the SAME turn the moment happens — never delay an alert to \
collect a name, number, or suburb first (you already have their number). Send \
with whatever you have and note what is still unknown.
- The customer's FIRST message — ALWAYS, no exceptions. On your very first \
reply to a new enquiry, set electrician_notification with a NEW LEAD heads-up, \
EVEN IF you are also triaging or booking in that same turn. The only swap: if \
that first message is itself an emergency, send the URGENT alert instead (it \
covers the heads-up).
- An emergency — the instant you spot one, even mid-qualifying and even if \
details are still missing.
- A confirmed booking — the moment the customer picks a window, send the \
BOOKED alert on that same turn. Do NOT wait until you have their name.
- A callback request — the moment they ask for a person to ring them, send the \
CALLBACK alert on that same turn. Do NOT wait to qualify first.
- A quote handed to Dave to price.
After the first-message heads-up, between that and the next real handoff \
(emergency / booking / callback / quote), keep electrician_notification null — \
do not buzz Dave on every turn.

Whenever you set electrician_notification, also set notification_kind to the \
matching tag: new_lead, emergency, quote, booked, or callback.

# Following up on a quiet lead
A customer who stops replying mid-conversation is usually just busy, not gone \
— and following up is the single highest-value thing you do for Dave. Almost \
no electrician does it. When a lead has gone quiet you may be asked to follow \
up; you will be told how long it has been and which attempt this is.
- Two nudges is the right amount of follow-up — not pushy, and it is what \
wins jobs back. Attempt 1 is a couple of hours later; attempt 2 is the next \
day and is the last.
- Keep each nudge to one or two warm, low-pressure lines. You are reminding, \
not chasing — never guilt the customer or imply they have done anything wrong.
- Make it easy to restart: re-ask the one thing you still needed, or offer a \
clear next step ("just text back a time and Dave will lock it in").
- Tailor it to where the conversation stopped. Mid-qualifying — re-ask what \
was missing. A quote already with Dave — check whether they have had a look \
and offer to lock in a start date.
- Attempt 1: send the nudge (action "nudge"). Attempt 2: send your second and \
final nudge — warm, door left open ("no rush — reach out whenever suits") — \
AND set electrician_notification so Dave knows a warm lead has gone quiet and \
can give them a personal call. Nothing gets dropped.
- Only choose action "stop" if the customer has explicitly said they do not \
want to go ahead. Silence alone is not a no — a quiet lead still gets both \
its nudges.

# What to return every turn
- reply — the SMS text to send to the customer. Short and natural. This is the \
only field the customer ever sees. Never put field names, JSON, or internal \
notes in here.
- stage — qualifying / triaging / booking / escalated / closed.
- triage — emergency / bookable / quote_first, or null if you don't know yet.
- qualified — job_type, urgency (emergency / this_week / flexible), suburb, \
address (full street address), property_type (home / business / rental). Fill \
in what you know; use null for what you don't yet.
- electrician_notification — the message for Dave's phone, or null. "The \
notification to Dave" above sets out exactly when to send it.
- notification_kind — when electrician_notification is set, what kind of \
alert it is: new_lead / emergency / quote / booked / callback. null otherwise.
- booking — the confirmed arrival window once the job is booked, otherwise \
null.
- booking_start / booking_end — when (and only when) a specific window is \
locked in, also return these as ISO 8601 datetimes with the timezone offset \
(e.g. 2026-06-24T08:00:00+10:00), worked out from the current date and time \
provided to you. ALWAYS resolve to the NEXT upcoming occurrence from now — \
never a date or time in the past. If the customer names a weekday that has \
already passed this week (or is today but the time has gone), use the following \
week. Leave both null until a window is actually confirmed.
- conversation_complete — true once the job is booked or escalated as an \
emergency, or the customer clearly ends the chat. A quote handed to Dave to \
price is NOT complete — the customer still has to decide and come back, so \
leave it false while anything is still in play.

<<examples_block>>
"""

# --- Per-tenant rendering (Phase 1) -----------------------------------------
# SYSTEM_PROMPT above is authored for the reference tenant ("Dave's Electrical").
# render_for_tenant swaps the identity terms for another tenant's values. For
# the reference tenant every swap maps a value to itself, so the rendered prompt
# is byte-identical to the original — behaviour is unchanged. This is the seam
# the AI workflow-builder (Phase 2) edits, via the tenant config, not the code.

def render_for_tenant(text: str, t: dict) -> str:
    """Render the prompt for tenant `t`. First inject the trade-specific blocks
    (so a locksmith stops talking like an electrician), then swap the identity
    terms. Order matters: trade blocks first (they may contain identity words),
    then most-specific identity strings before shorter ones."""
    owner = t["owner_name"]
    return (
        text
        # trade-specific blocks (sentinels) — injected first
        .replace("<<emergency_def>>", t["emergency_def"])
        .replace("<<quote_def>>", t["quote_def"])
        .replace("<<bookable_def>>", t["bookable_def"])
        .replace("<<trade_slang>>", t["trade_slang"])
        .replace("<<notification_examples>>", t["notification_examples"])
        .replace("<<examples_block>>", t["examples_block"])
        # identity terms
        .replace("Dave's Electrical", t["business_name"])
        .replace("Dave’s", f"{owner}’s")  # possessive, curly apostrophe
        .replace("Dave's", f"{owner}'s")            # possessive, straight apostrophe
        .replace("Dave", owner)
        .replace(
            "Wollongong and the northern Illawarra suburbs of NSW, Australia",
            t["service_area"],
        )
        .replace("Wollongong and the northern Illawarra", t["service_area_short"])
        .replace("Wollongong", t["city"])
        .replace("electricians", f"{t['trade_noun']}s")
        .replace("electrician", t["trade_noun"])
        .replace("electrical", t["trade_adj"])
    )


# Per-tenant pricing behaviour. The reference prompt's hard rule is the "never"
# wording, so a "never" tenant is unchanged; "ranges"/"full" swap the clause.
QUOTING_RULES = {
    "never": "NEVER quote a price or estimate. {owner} prices the work.",
    "ranges": (
        "You may give a rough ballpark range to set expectations, but make "
        "clear {owner} confirms the exact price on site — never commit to a "
        "firm figure."
    ),
    "full": (
        "You may quote firm prices for jobs covered by your price list; for "
        "anything not on the list, defer to {owner}."
    ),
}


# For non-"never" tenants the single clause swap isn't enough — the rest of the
# prompt (quote_first routing, examples) still pushes no-quote, so the model
# follows the majority signal. A high-salience override at the very top wins.
PRICING_OVERRIDE = {
    "ranges": (
        "# PRICING POLICY (this overrides any other guidance or example below)\n"
        "This business gives rough price ranges. When the customer asks what "
        "something will cost and you have enough detail to estimate, GIVE a "
        "ballpark range (for example \"usually around $X-$Y\") and add that "
        "{owner} confirms the exact price on site. Never flatly refuse to give "
        "a number, and never commit to a firm figure.\n\n"
    ),
    "full": (
        "# PRICING POLICY (this overrides any other guidance or example below)\n"
        "This business quotes firm prices. When the customer asks what "
        "something will cost and the job is covered by your price list, GIVE "
        "the price directly. For anything not on the list, say {owner} will "
        "confirm. Never flatly refuse to give a number.\n\n"
    ),
}


def apply_quoting_policy(text: str, t: dict) -> str:
    """Apply the tenant's quoting policy: swap the hard-rule pricing sentence,
    and for non-"never" policies prepend a high-salience override so the model
    actually quotes. "never" leaves the prompt unchanged."""
    owner = t["owner_name"]
    policy = t.get("quoting_policy", "never")
    base = f"NEVER quote a price or estimate. {owner} prices the work."
    clause = QUOTING_RULES.get(policy, QUOTING_RULES["never"]).format(owner=owner)
    text = text.replace(base, clause)
    override = PRICING_OVERRIDE.get(policy)
    if override:
        text = override.format(owner=owner) + text
    return text


def build_system(t: dict) -> str:
    """Full system prompt for a tenant = rendered behaviour rules (identity +
    quoting policy) + domain knowledge. Static per tenant, so it stays one
    stable, cacheable prefix."""
    text = apply_quoting_policy(render_for_tenant(SYSTEM_PROMPT, t), t)
    # Domain knowledge is optional and trade-specific. A tenant without its own
    # knowledge file (e.g. a freshly AI-built locksmith) gets none rather than
    # inheriting another trade's facts.
    knowledge_file = t.get("knowledge_file")
    if knowledge_file:
        kpath = Path(__file__).parent / knowledge_file
        if kpath.exists():
            return text + "\n\n" + kpath.read_text(encoding="utf-8")
    return text


# The fallback tenant when an inbound number doesn't match any configured
# tenant (e.g. single-number deploys). TENANT_ID env override picks it.
DEFAULT_TENANT = load_tenant(os.getenv("TENANT_ID", "dave"))
SYSTEM = build_system(DEFAULT_TENANT)

# Built system prompts are static per tenant, so cache them by tenant_id rather
# than rebuilding on every inbound message.
_SYSTEM_CACHE: dict[str, str] = {}


def system_for(t: dict) -> str:
    """The cached system prompt for a tenant (built once per tenant_id)."""
    tid = t["tenant_id"]
    if tid not in _SYSTEM_CACHE:
        _SYSTEM_CACHE[tid] = build_system(t)
    return _SYSTEM_CACHE[tid]


def _log_usage(usage) -> None:
    """Print one [usage] line so cache hits are visible in the server log."""
    print(
        f"[usage] input={usage.input_tokens} "
        f"cache_write={usage.cache_creation_input_tokens} "
        f"cache_read={usage.cache_read_input_tokens} "
        f"output={usage.output_tokens}"
    )


def run_turn(
    messages: list[dict], system: str | None = None, now_line: str | None = None
) -> AgentTurn:
    """Run one turn of the conversation.

    `messages` is the full SMS thread so far in Anthropic format (a list of
    alternating user/assistant messages). `system` is the tenant's prompt
    (defaults to the fallback tenant). `now_line` is an optional current-time
    note (kept out of the cached prefix) so the model can compute ISO booking
    times. Returns the structured AgentTurn.
    """
    system_blocks = [
        {
            "type": "text",
            "text": system or SYSTEM,
            # Caches the workflow prompt so repeat turns pay ~0.1x for it
            # instead of full price. On Haiku 4.5 this engages once the
            # cached prefix is >= 4096 tokens — see README for detail.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if now_line:
        # Volatile — must NOT be cached, so it goes in its own trailing block.
        system_blocks.append({"type": "text", "text": now_line})
    response = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        system=system_blocks,
        messages=messages,
        output_format=AgentTurn,
    )

    _log_usage(response.usage)

    if response.parsed_output is None:
        raise RuntimeError(
            f"Model did not return valid structured output "
            f"(stop_reason={response.stop_reason})"
        )
    return response.parsed_output


def run_followup(messages: list[dict], attempt: int, system: str | None = None) -> FollowUp:
    """Compose a follow-up for a lead that has gone quiet.

    `messages` is the conversation so far — it must end with the assistant's
    last reply. `attempt` is 1 for the first nudge (~2 hours later) or 2 for
    the second (~24 hours later, the last). `system` is the tenant's prompt.
    Returns the structured FollowUp.
    """
    gap = "about 2 hours" if attempt == 1 else "about a day"
    nudge_prompt = (
        f"[Follow-up check — an internal note, not a message from the "
        f"customer] It is now {gap} since your last text and the customer "
        f"still has not replied. This is follow-up attempt {attempt} of a "
        f'maximum of 2. Apply your "Following up on a quiet lead" rules and '
        f"return the follow-up."
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": system or SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages + [{"role": "user", "content": nudge_prompt}],
        output_format=FollowUp,
    )

    _log_usage(response.usage)

    if response.parsed_output is None:
        raise RuntimeError(
            f"Model did not return valid structured output "
            f"(stop_reason={response.stop_reason})"
        )
    return response.parsed_output
