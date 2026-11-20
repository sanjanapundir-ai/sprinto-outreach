"""The five agent tools: plain-English descriptions + implementations.

The model never sees the Python below - it sees TOOL_SPECS on every run. Each
description follows the four golden rules, in this fixed order:
    WHAT IT DOES / WHEN TO USE / WHEN NOT TO USE / RETURNS

TOOL_SPECS uses the JSON-schema tool format accepted by the Claude API
(`tools=[...]`) and by LangChain's `bind_tools`, so the same text can be handed
to a model unchanged if you replace any rule-based step with an LLM call.
"""
from typing import Callable, Dict, List

from .world import World, fmt_slot

TOOL_SPECS: List[dict] = [
    {
        "name": "send_email",
        "description": (
            "WHAT IT DOES: Sends the first cold outreach email to one prospect from the "
            "SDR's mailbox and starts a new thread.\n"
            "WHEN TO USE: Exactly once per prospect, at the start of Sequence 1, after the "
            "prospect has passed ICP qualification and the draft has been approved.\n"
            "WHEN NOT TO USE: Never to reply inside an existing thread, never to propose "
            "meeting times (use propose_times), never to a prospect whose reply_status is "
            "already set, and never to an address that previously bounced.\n"
            "RETURNS: JSON {\"message_id\": str, \"status\": \"sent\"}. Raises a permanent "
            "error if the address bounces (do not retry) or a transient error on timeout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Prospect email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "check_calendar",
        "description": (
            "WHAT IT DOES: Checks whether specific dates and times are free on the account "
            "executive's calendar (weekdays, 09:00-18:00, not already booked, in the future).\n"
            "WHEN TO USE: Before every proposal email, and whenever the prospect suggests "
            "their own time (counter-proposal), so we never offer or book a slot we do not have.\n"
            "WHEN NOT TO USE: Not to find out the prospect's availability (we cannot see "
            "their calendar), not to create or hold events (use book_meeting), and not for "
            "more than 20 slots in one call.\n"
            "RETURNS: JSON {\"slots\": [{\"start\": \"YYYY-MM-DDTHH:MM\", \"free\": bool}, ...]} "
            "in the same order as the input."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slots": {"type": "array", "items": {"type": "string"}, "maxItems": 20,
                          "description": "ISO local datetimes, e.g. 2026-11-19T14:00"},
            },
            "required": ["slots"],
        },
    },
    {
        "name": "propose_times",
        "description": (
            "WHAT IT DOES: Replies in the existing thread (send_email, propose-times mode) "
            "offering the prospect 2-3 specific 30-minute slots, numbered Option 1..3.\n"
            "WHEN TO USE: After the prospect replied 'interested', after a counter-proposal "
            "we could not accept, or as a nudge after silence - and only with slots that "
            "check_calendar just reported as free.\n"
            "WHEN NOT TO USE: Not for the first cold email (use send_email), not with fewer "
            "than 2 or more than 3 slots, not with unchecked slots, and not once reply_round "
            "has reached the cap (hand off to a human instead).\n"
            "RETURNS: JSON {\"message_id\": str, \"status\": \"sent\", \"slots\": [str]} - the "
            "exact slots sent, which must be stored in times_proposed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "slots": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
                "round": {"type": "integer", "description": "reply_round this proposal belongs to"},
            },
            "required": ["to", "slots", "round"],
        },
    },
    {
        "name": "book_meeting",
        "description": (
            "WHAT IT DOES: Creates a 30-minute event on the AE's calendar and sends the "
            "calendar invite to the prospect.\n"
            "WHEN TO USE: Only when the prospect has explicitly accepted one specific slot "
            "(one we proposed, or their own counter-proposal that check_calendar confirmed "
            "free), or a human approved a slot during handoff.\n"
            "WHEN NOT TO USE: Not to 'hold' tentative times, not when the reply is vague "
            "('sometime next week'), not for a slot that was not checked, and never twice "
            "for the same prospect.\n"
            "RETURNS: JSON {\"event_id\": str, \"start\": str, \"invite_sent\": true}. Raises a "
            "permanent error if the slot has been taken in the meantime."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slot": {"type": "string"},
                "attendee_email": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["slot", "attendee_email", "title"],
        },
    },
    {
        "name": "update_crm",
        "description": (
            "WHAT IT DOES: Writes the final outcome of the outreach (and meeting details if "
            "any) to the prospect's CRM record, creating it if needed.\n"
            "WHEN TO USE: Exactly once, as the last step of every run - booked, declined, "
            "no reply, bounced, disqualified or closed by a human.\n"
            "WHEN NOT TO USE: Not for intermediate progress (the graph checkpoint holds that), "
            "not to store email bodies, and not before the outcome is final.\n"
            "RETURNS: JSON {\"record_id\": str, \"updated_fields\": [str]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_id": {"type": "string"},
                "outcome": {"type": "string",
                            "enum": ["meeting_booked", "not_interested", "no_reply", "no_response",
                                     "declined_after_interest", "bounced", "disqualified",
                                     "closed_by_human", "needs_review", "send_failed"]},
                "fields": {"type": "object"},
            },
            "required": ["prospect_id", "outcome"],
        },
    },
]


# ---- implementations (what the descriptions promise) --------------------
def send_email(world: World, to: str, subject: str, body: str) -> dict:
    world.maybe_fail("send_email")
    msg = world.send(to, subject, body, kind="cold_outreach")
    return {"message_id": msg["id"], "status": "sent"}


def check_calendar(world: World, slots: List[str]) -> dict:
    world.maybe_fail("check_calendar")
    if len(slots) > 20:
        raise ValueError("check_calendar accepts at most 20 slots")
    return {"slots": [{"start": s, "free": world.is_free(s)} for s in slots]}


def propose_times(world: World, to: str, slots: List[str], round: int) -> dict:
    world.maybe_fail("propose_times")
    if not 2 <= len(slots) <= 3:
        raise ValueError("propose_times needs 2-3 slots")
    lines = "\n".join(f"  Option {i}: {fmt_slot(s)}" for i, s in enumerate(slots, 1))
    opener = "Great to hear from you!" if round == 1 else "Following up with a few more options:"
    body = f"{opener} Would any of these work for a 30-minute call?\n{lines}\nJust reply with the option number."
    msg = world.send(to, "Re: SOC 2 in weeks, not months", body, kind="propose_times")
    return {"message_id": msg["id"], "status": "sent", "slots": list(slots)}


def book_meeting(world: World, slot: str, attendee_email: str, title: str) -> dict:
    world.maybe_fail("book_meeting")
    return world.book(slot, attendee_email, title)


def update_crm(world: World, prospect_id: str, outcome: str, fields: dict = None) -> dict:
    world.maybe_fail("update_crm")
    payload = {"outcome": outcome, **(fields or {})}
    record = world.upsert_crm(prospect_id, payload)
    return {"record_id": record["record_id"], "updated_fields": sorted(payload)}


TOOL_IMPLS: Dict[str, Callable] = {
    "send_email": send_email,
    "check_calendar": check_calendar,
    "propose_times": propose_times,
    "book_meeting": book_meeting,
    "update_crm": update_crm,
}
