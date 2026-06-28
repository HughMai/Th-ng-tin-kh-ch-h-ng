"""Electrician speed-to-lead conversation engine — VOICE front door.

Same brain as `workflow.py`, different mouth. Where the SMS engine returns one
structured `AgentTurn` per text, a live phone call needs two things the SMS
shape can't give:

  1. Streaming — the spoken reply has to start playing through text-to-speech
     before the model has finished thinking, or the caller hears dead air.
  2. Tool calls — booking a window or alerting the owner happens mid-call as a
     side-effect, not as fields on a one-shot reply.

So this module reuses the tenant's full trade brain (`workflow.system_for`) and
prepends a high-salience VOICE MODE override — the same trick `workflow.py`
already uses for pricing policy — that neutralises the SMS I/O instructions and
swaps in: speak naturally, and use tools (alert_owner / book_job / end_call).
All the trade logic below the override (triage, two-window booking, emergency
handling, persona, safety rules) is unchanged.

The media bridge (`voice_server.py`) streams the spoken text out to ElevenLabs
and fires the returned actions (owner alert, booking) through the same paths the
SMS engine already uses in `app.py`.
"""

import os
from typing import Callable, Literal, Optional

from dotenv import load_dotenv

load_dotenv()  # load ANTHROPIC_API_KEY from .env before the client is built

import anthropic  # noqa: E402  (must import after load_dotenv)
from pydantic import BaseModel, ValidationError  # noqa: E402

from workflow import DEFAULT_TENANT, system_for  # noqa: E402  (reuse the brain)

# Voice wants speed over polish — a fast model keeps turn-taking natural. Default
# to Haiku; override with VOICE_MODEL (e.g. claude-sonnet-4-6) if a tenant wants
# the extra quality and can wear the latency.
MODEL = os.getenv("VOICE_MODEL", "claude-haiku-4-5-20251001")

client = anthropic.Anthropic(max_retries=4)  # key from env; retries ride out transient overloads


# --- The voice-mode override ------------------------------------------------
# Prepended above the tenant's SMS brain. High salience so the model follows the
# spoken-call I/O contract here and ignores the text-channel instructions below,
# while keeping every trade rule. Mirrors workflow.PRICING_OVERRIDE's approach.

VOICE_MODE = """# LIVE PHONE CALL — VOICE MODE (this overrides any SMS or text-message instruction below)
You are NOT texting. You are SPEAKING on a live phone call: the customer just \
rang {business} and you answered the phone. Everything below about SMS, \
text-message length, "the SMS text to \
send", emoji and "what to return every turn" describes the text channel — \
IGNORE that input/output contract. Your persona, trade knowledge, triage rules, \
booking rules and safety rules below all still apply exactly.

# SPEED-TO-LEAD: what to collect on a call (OVERRIDES any form-filling or \
address-collection step below)
- Your job is to capture the lead FAST and book a window — not to fill out a \
form. Collect only THREE things: what they need, the suburb (to confirm you \
service the area), and a preferred time of day. Then OFFER two arrival windows \
and book the one they pick.
- EMERGENCIES (sparks, smoke, burning smell, electric shock, water near wiring): \
treat as urgent and ALWAYS say two things in your first reply — the owner will \
call them straight away, AND if there is any fire or smoke they should call 000 \
immediately and get well clear. Never omit the 000 mention for fire or smoke. \
Give NO repair or troubleshooting steps, and call alert_owner with kind \
"emergency" on that same turn.
- Do NOT ask for the street address, the caller's name, a phone number, or a \
callback time on the call — {owner} grabs all of that when he rings them back. \
If you are about to ask for an address, STOP and offer arrival windows instead.
- NEVER ask anyone to spell anything out. Phone lines are patchy; if you didn't \
catch a word, make a reasonable guess or just move on. Never loop on a word.
- Service area is Wollongong and the northern Illawarra ONLY. If a suburb sounds \
outside it — Bowral, the Southern Highlands, Sydney, the Sutherland Shire — say \
honestly that you don't service their area, don't book, and close politely. If \
you didn't clearly catch the suburb or don't recognise the name, confirm before \
booking — "Is that around Wollongong?" — and if it isn't, don't book.
- When the caller gives a suburb, locality, or address-like place, call \
check_service_area with the raw words you heard before deciding whether it is in \
area. Use the tool's canonical_suburb in alerts and bookings. If status is \
"confirm", ask only "Did you mean <canonical_suburb>?" If status is "unknown", \
ask once "Is that around Wollongong and the Illawarra?" If status is \
"out_of_area", say the service area and close politely. Never freeze, hang up, \
or keep repeating because a place name was unclear.

# How you speak on this call
- You are Syanna, the AI front desk for {business}. The caller already heard: \
"Hi, I'm Syanna from {business}. How can I help you today?" Treat that as \
already spoken. Do NOT repeat the greeting or introduce yourself again unless \
the caller asks who you are.
- The customer rang {business} and you picked up, so let them tell you what \
they need and respond to that. Do NOT say you're "ringing them back" or that \
they "couldn't get through" — they're on the line with you right now.
- Speak like a calm local receptionist, not a script. Use contractions and \
plain words. Every sentence must be precise, short, and useful. Default to \
one sentence under ten words, then one question if needed.
- No compliments, encouragement, filler, or praise. Avoid "great", "perfect", \
"amazing", "gotcha", "no worries", "happy to help", and "that sounds good". \
Do not add softener phrases unless safety-critical.
- NO lists, no bullet points, no emoji, no formatting of any kind: every word \
you output is read aloud by a text-to-speech voice. NEVER use asterisks, \
underscores, hashes or quote-marks for emphasis — the text-to-speech voice \
literally reads them out ("asterisk asterisk don't touch it"). Plain words \
only. Example — BAD: **don't touch it**  GOOD: don't touch it.
- Say numbers, times and dates the way a person says them out loud ("Tuesday \
morning, between eight and eleven"), never as digits-only or ISO strings.
- Ask ONE question at a time and leave space for the caller to answer. Never \
stack suburb, address, name and booking window into one turn. Never read out \
internal notes, field names, or JSON.
- If the caller asks whether you're a bot, answer honestly that you're Syanna, \
{business}'s AI assistant, and offer to have {owner} call them personally.
- If speech is unclear, accented, noisy, or badly transcribed, recover with one \
short clarifying question based on what you need next. Do NOT apologise more \
than once, do NOT ask them to spell, and do NOT call end_call because you did \
not understand.

# Taking actions on the call — use TOOLS, not speech
- Call the report_state tool EVERY turn, before any other action, with what you \
have captured so far (triage, suburb, agreement) and what you intend to do next. \
The server logs it; it is never spoken to the caller.
- When you reach a moment {owner} needs to know about, call the alert_owner \
tool. Do NOT speak the notification aloud — it goes to {owner}'s phone, not the \
caller's ear. Send the first-lead alert (kind "new_lead") on your very first \
turn, exactly as the rules below require — unless the call opens as an \
emergency, in which case send kind "emergency" instead.
- Use check_service_area whenever the suburb/locality is unclear or newly \
mentioned. The caller never hears the tool result; speak only the next short \
question or answer.
- When the caller agrees to an arrival window, call book_job with the window in \
plain words plus the start and end as ISO 8601 with the timezone offset. \
book_job notifies {owner} for you — you do not also need a "booked" alert.
- When the conversation is genuinely finished — the job is booked, the \
emergency is escalated, or the caller is done — say a short goodbye and \
THEN call end_call.

# IMPORTANT - live-call tool policy
- Call alert_owner EARLY. The whole point of this line is {owner} learns about \
the lead fast — so on your FIRST turn, as soon as you hear it's a real service \
enquiry, call alert_owner with kind "new_lead" (or "emergency" if it \
sounds like a safety emergency). The caller never hears it, and you can speak \
to them AND call the tool in the same turn. This matches the first-lead rule \
above — do not defer it.
- You can call alert_owner again near the end if something new matters (a quote \
to price, a callback request). A confirmed booking uses book_job instead. The \
server queues every alert and sends one consolidated owner summary after the \
call ends; the caller never hears any of it.
- The MOMENT the caller agrees to a window ("Tuesday works", "book it", "yeah \
that's good"), you MUST call the book_job tool — saying "great, see you then" \
is NOT enough; the tool call is what records the booking. For start_iso and \
end_iso, use the current date/time shown above to resolve their window to ISO \
8601 with the timezone offset (e.g. the next Tuesday, 08:00 to 11:00). You do \
NOT need a street address — book with the suburb and window you have; {owner} \
grabs the exact address when he rings them back.
- Before ANY goodbye, you MUST ask "Anything else I can help you with?" — on \
every call, even a quick one. Once the job is booked, the emergency is \
escalated, or the caller has what they need, ask that question; only after they \
reply (yes -> keep helping; no / "that's all" / "no thanks" -> goodbye) do you \
say a short goodbye ("Thanks for calling. Bye.") and call end_call \
in the SAME turn. Do not ask "anything else?" more than once, and do not keep \
talking after the goodbye.
- Do NOT repeat yourself. Never re-ask a question you already asked, and never \
restate the same point twice in different words. If the caller didn't answer \
something after one ask, move on and work with what you have. Every turn must \
move the call forward. Do not recycle stock phrases across turns.

"""


# --- Tools the model uses to act during the call ----------------------------

TOOLS = [
    {
        "name": "alert_owner",
        "description": (
            "Queue a short post-call notification for the business owner's "
            "phone. The caller does NOT hear this. Use near the end of the call "
            "for a new lead, an emergency, a quote to price, or a callback "
            "request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["new_lead", "emergency", "quote", "booked", "callback"],
                },
                "message": {
                    "type": "string",
                    "description": "Tight, scannable handoff line(s) for the owner's phone.",
                },
            },
            "required": ["kind", "message"],
        },
    },
    {
        "name": "check_service_area",
        "description": (
            "Normalize a suburb/locality heard on the call and check whether it "
            "is inside the tenant's service area. Use this before booking when "
            "a place name is newly mentioned, unclear, accented, or likely "
            "mis-transcribed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_suburb": {
                    "type": "string",
                    "description": (
                        "The exact suburb/locality words heard from the caller, "
                        "even if they look misspelled by speech-to-text."
                    ),
                }
            },
            "required": ["raw_suburb"],
        },
    },
    {
        "name": "book_job",
        "description": (
            "Lock in a confirmed arrival window. Also notifies the owner — no "
            "separate 'booked' alert is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window_text": {
                    "type": "string",
                    "description": "The window in plain spoken words, e.g. 'Tuesday between 8 and 11am'.",
                },
                "start_iso": {
                    "type": "string",
                    "description": "Window start, ISO 8601 with timezone offset, e.g. 2026-06-30T08:00:00+10:00.",
                },
                "end_iso": {
                    "type": "string",
                    "description": "Window end, ISO 8601 with timezone offset.",
                },
                "job_type": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["window_text", "start_iso", "end_iso"],
        },
    },
    {
        "name": "end_call",
        "description": (
            "End the phone call after a closing line. Use only when the "
            "conversation is genuinely complete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": [],
        },
    },
    {
        # Shadow-FSM signal (Tier 1). The LLM reports what it captured + its
        # intended next step every turn; the server logs it (never spoken to the
        # caller, same as check_service_area) and the FSM flags divergence between
        # this claim and what it observed. All fields optional — Haiku-on-Deepgram
        # occasionally drops fields, and a tool error would break the call.
        "name": "report_state",
        "description": (
            "Call this EVERY turn, alongside any spoken reply, to report what you "
            "have captured and what you intend to do next. The server logs it and "
            "may steer your next step. It is NOT spoken to the caller. Call it "
            "once per turn, before any other action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "triage": {
                    "type": "string",
                    "enum": ["emergency", "bookable", "quote_first", "callback", "not_a_job"],
                    "description": "Your read of what kind of call this is.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["emergency", "this_week", "flexible"],
                },
                "job_type": {"type": "string"},
                "suburb_raw": {
                    "type": "string",
                    "description": "The exact suburb words you heard, before normalization.",
                },
                "area_status": {
                    "type": "string",
                    "enum": ["in_area", "confirm", "out_of_area", "unknown"],
                },
                "offered_two_windows": {"type": "boolean"},
                "agreement": {
                    "type": "string",
                    "enum": ["yes", "no", "unsure"],
                    "description": "Did the caller agree to a window this turn?",
                },
                "proposed_next": {
                    "type": "string",
                    "enum": ["gather", "confirm_area", "offer", "confirm_book", "book", "close", "escalate", "fallback"],
                    "description": "What you intend to do on your next turn.",
                },
                "confidence": {"type": "number"},
                "ask_clarification": {"type": "string"},
            },
            "required": [],
        },
    },
]


# Keep the live tool menu aligned with the revised post-call behavior. This
# override also avoids depending on older prompt text that may still exist below.
for _tool in TOOLS:
    if _tool["name"] == "book_job":
        _tool["description"] = (
            "Queue a confirmed arrival window. The server creates the calendar "
            "event and sends one consolidated owner summary after the call ends."
        )
    elif _tool["name"] == "end_call":
        _tool["description"] = (
            "End the phone call after asking if there is anything else and "
            "giving a short closing line. The server waits about two seconds "
            "before hanging up."
        )


# --- What one voice turn produces -------------------------------------------

class OwnerAlert(BaseModel):
    """A notification destined for the owner's phone (an alert_owner tool call)."""

    kind: Literal["new_lead", "emergency", "quote", "booked", "callback"]
    message: str


class Booking(BaseModel):
    """A confirmed arrival window (a book_job tool call)."""

    window_text: str
    start_iso: str
    end_iso: str
    job_type: Optional[str] = None
    address: Optional[str] = None


class VoiceTurn(BaseModel):
    """One full turn of the live call: what to say, plus the side-effects to fire.

    `reply_text` is the full spoken reply (also streamed live via the on_text
    callback). The orchestrator fires `alerts` and `booking` through the same
    owner-SMS / calendar paths app.py already uses, then hangs up if `end_call`.
    """

    reply_text: str
    alerts: list[OwnerAlert] = []
    booking: Optional[Booking] = None
    end_call: bool = False


# --- Per-tenant voice system prompt (override + the SMS brain) ---------------

def build_voice_system(t: dict) -> str:
    """The full voice system prompt for a tenant: the VOICE MODE override on top
    of the tenant's existing trade brain. The override wins on I/O; the brain
    below supplies all the trade rules unchanged."""
    override = VOICE_MODE.format(owner=t["owner_name"], business=t["business_name"])
    return override + system_for(t)


_VOICE_CACHE: dict[str, str] = {}


def voice_system_for(t: dict) -> str:
    """The cached voice system prompt for a tenant (built once per tenant_id)."""
    tid = t["tenant_id"]
    if tid not in _VOICE_CACHE:
        _VOICE_CACHE[tid] = build_voice_system(t)
    return _VOICE_CACHE[tid]


SYSTEM = build_voice_system(DEFAULT_TENANT)


def _log_usage(usage) -> None:
    """Print one [usage] line so cache hits are visible in the server log."""
    print(
        f"[voice usage] input={usage.input_tokens} "
        f"cache_write={usage.cache_creation_input_tokens} "
        f"cache_read={usage.cache_read_input_tokens} "
        f"output={usage.output_tokens}"
    )


def _parse_actions(content) -> tuple[list[OwnerAlert], Optional[Booking], bool]:
    """Pull the side-effects out of the model's tool_use blocks. A malformed tool
    call is logged and skipped rather than crashing the live call."""
    alerts: list[OwnerAlert] = []
    booking: Optional[Booking] = None
    end_call = False
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        try:
            if block.name == "alert_owner":
                alerts.append(OwnerAlert(**block.input))
            elif block.name == "book_job":
                booking = Booking(**block.input)
            elif block.name == "end_call":
                end_call = True
        except ValidationError as exc:
            print(f"[voice] dropped malformed {block.name} tool call: {exc}")
    return alerts, booking, end_call


def stream_voice_turn(
    messages: list[dict],
    system: str | None = None,
    now_line: str | None = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> VoiceTurn:
    """Run one turn of the live call.

    `messages` is the running transcript so far in Anthropic format (alternating
    user/assistant turns from the speech-to-text). `system` is the tenant's voice
    prompt (defaults to the fallback tenant). `now_line` is the current-time note
    so the model can resolve booking windows to ISO times — kept out of the
    cached prefix. `on_text` is called with each spoken-text delta as it streams,
    so the media bridge can feed text-to-speech with minimal latency.

    Returns the assembled VoiceTurn (full reply text + the side-effects to fire).
    """
    system_blocks = [
        {
            "type": "text",
            "text": system or SYSTEM,
            # Cache the (large, static) voice brain so repeat turns pay ~0.1x.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if now_line:
        # Volatile — must NOT be cached, so it goes in its own trailing block.
        system_blocks.append({"type": "text", "text": now_line})

    text_parts: list[str] = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=512,
        temperature=0.4,  # precise booking bot, not a creative writer
        system=system_blocks,
        tools=TOOLS,
        messages=messages,
    ) as stream:
        for event in stream:
            if (
                event.type == "content_block_delta"
                and event.delta.type == "text_delta"
            ):
                chunk = event.delta.text
                text_parts.append(chunk)
                if on_text:
                    on_text(chunk)
        final = stream.get_final_message()

    _log_usage(final.usage)
    alerts, booking, end_call = _parse_actions(final.content)
    return VoiceTurn(
        reply_text="".join(text_parts),
        alerts=alerts,
        booking=booking,
        end_call=end_call,
    )
