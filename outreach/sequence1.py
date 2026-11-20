"""Sequence 1 - Send & Wait.

Flat branch logic with no loops and no state that depends on earlier attempts,
so it is scripted by hand: no framework needed.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from .classify import classify_initial_reply
from .retry import run_tool
from .world import World


@dataclass
class Sequence1Result:
    reply_status: Optional[str]          # no_reply | interested | not_interested (None if aborted)
    outcome: Optional[str] = None        # set when the run ends here
    reply_text: Optional[str] = None
    events: List[str] = field(default_factory=list)


def draft_cold_email(p: dict) -> tuple:
    subject = "SOC 2 in weeks, not months"
    body = (
        f"Hi {p['first_name']},\n\n"
        f"Saw that {p['company']} is selling into larger customers - security reviews tend to "
        f"follow. Sprinto automates SOC 2 / ISO 27001 evidence collection so teams like yours "
        f"get audit-ready in weeks.\n\nWorth a 30-minute call?\n"
    )
    return subject, body


def run_sequence1(prospect: dict, world: World) -> Sequence1Result:
    subject, body = draft_cold_email(prospect)
    res = run_tool(world, "send_email", to=prospect["email"], subject=subject, body=body)
    if not res.ok:
        outcome = "bounced" if res.error_kind == "permanent" else "send_failed"
        return Sequence1Result(None, outcome=outcome,
                               events=[f"send_email failed ({res.error_kind}, {res.attempts} attempt(s)) -> {res.action}: {res.error}"])

    events = [f"send_email ok after {res.attempts} attempt(s)"]
    reply = world.next_reply(prospect["id"])        # production: wait N days for the inbox webhook
    status = classify_initial_reply(reply)
    events.append(f"reply: {reply!r} -> {status}")

    if status == "unclear":                          # not one of the three valid answers
        return Sequence1Result("no_reply", outcome="needs_review", reply_text=reply, events=events)
    if status != "interested":
        return Sequence1Result(status, outcome=status, reply_text=reply, events=events)
    return Sequence1Result("interested", reply_text=reply, events=events)
