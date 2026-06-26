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

# How you speak on this call
- The customer rang {business} and you picked up, so let them tell you what \
they need and respond to that. Do NOT say you're "ringing them back" or that \
they "couldn't get through" — they're on the line with you right now. If you \
open, keep it to a short warm greeting like "Hi, you've reached {business} — how \
can I help?" then listen.
- Speak in short, natural spoken sentences — one or two at a time. NO lists, no \
bullet points, no emoji, no asterisks, no formatting of any kind: every word \
you output is read aloud by a text-to-speech voice.
- Say numbers, times and dates the way a person says them out loud ("Tuesday \
morning, between eight and eleven"), never as digits-only or ISO strings.
- Ask ONE question at a time and leave space for the caller to answer. Never \
read out internal notes, field names, or JSON.
- If the caller asks whether you're a bot, answer honestly that you're \
{business}'s AI assistant, and offer to have {owner} call them personally.

# Taking actions on the call — use TOOLS, not speech
- When you reach a moment {owner} needs to know about, call the alert_owner \
tool. Do NOT speak the notification aloud — it goes to {owner}'s phone, not the \
caller's ear. Send the first-lead alert (kind "new_lead") on your very first \
turn, exactly as the rules below require — unless the call opens as an \
emergency, in which case send kind "emergency" instead.
- When the caller agrees to an arrival window, call book_job with the window in \
plain words plus the start and end as ISO 8601 with the timezone offset. \
book_job notifies {owner} for you — you do not also need a "booked" alert.
- When the conversation is genuinely finished — the job is booked, the \
emergency is escalated, or the caller is done — say a short warm goodbye and \
THEN call end_call.

"""


# --- Tools the model uses to act during the call ----------------------------

TOOLS = [
    {
        "name": "alert_owner",
        "description": (
            "Send a short notification to the business owner's phone. The caller "
            "does NOT hear this. Use it for a new lead, an emergency, a quote to "
            "price, or a callback request."
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
]


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
