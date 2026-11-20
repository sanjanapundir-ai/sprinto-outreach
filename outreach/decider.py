"""Automatic handoff decisions.

When Sequence 2 escalates, the graph pauses with a payload describing the
situation (kind, reason, requested/selected slot, round, times proposed, last
reply). This module reads that payload and returns the same decision a person
would type: book / propose / close. Each decision carries a `why` that is
written to the audit trail and the CRM-visible events.

Policy (fixed up front, like the retry table):

| kind            | decision                                                                  |
|-----------------|---------------------------------------------------------------------------|
| counter_cap     | Book the requested time if free, else the closest free slot at the        |
|                 | prospect's preferred time of day. The invite lets them accept or move it. |
| booking_failed  | Slot was lost: book the closest free slot at the same time of day.        |
| unclear_reply   | Under the round cap: re-propose fresh checked slots. At the cap: close.   |
| calendar_failed | Close as needs_review - we cannot verify any slot, so never book blind.   |
| propose_failed  | Close as needs_review - we cannot reach the prospect.                     |
| no_free_slots   | Close as needs_review - nothing valid to offer.                           |

Every slot it books or proposes is verified with check_calendar first (under
the retry table); if that check fails, it closes as needs_review.
"""
from datetime import timedelta
from typing import Callable, List, Optional

from .retry import MAX_REPLY_ROUNDS, run_tool
from .world import World, alternatives_near, candidate_slots, fmt_slot, iso, parse_slot


DECISION_POLICY = [
    {"kind": "counter_cap", "decision": "book",
     "rule": "Book the requested time if free, else the closest free slot at the prospect's preferred time of day."},
    {"kind": "booking_failed", "decision": "book",
     "rule": "The accepted slot was taken: book the closest free slot at the same time of day."},
    {"kind": "unclear_reply", "decision": "propose / close",
     "rule": "Under the round cap: re-propose fresh checked slots. At the cap: close as needs_review."},
    {"kind": "calendar_failed", "decision": "close",
     "rule": "Calendar unreachable after retries: close as needs_review, never book unchecked slots."},
    {"kind": "propose_failed", "decision": "close",
     "rule": "Proposal email could not be sent after retries: close as needs_review."},
    {"kind": "no_free_slots", "decision": "close",
     "rule": "Fewer than 2 free slots to offer: close as needs_review."},
]


def _same_time_next_days(slot: str, days: int = 5) -> List[str]:
    dt, out = parse_slot(slot), []
    while len(out) < days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            out.append(iso(dt))
    return out


def _free(world: World, candidates: List[str]) -> Optional[List[str]]:
    """Free slots among candidates (order kept), or None if the calendar is unreachable."""
    seen, ordered = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    res = run_tool(world, "check_calendar", slots=ordered[:20])
    if not res.ok:
        return None
    return [s["start"] for s in res.value["slots"] if s["free"]]


def _close(why: str) -> dict:
    return {"action": "close", "outcome": "needs_review", "by": "auto", "why": why}


def make_auto_decider(world: World) -> Callable[[dict], dict]:
    def nearest_free(target: str, exclude: List[str]) -> Optional[str]:
        candidates = [target] + _same_time_next_days(target) + alternatives_near(target, world.now)
        free = _free(world, [c for c in candidates if c not in exclude])
        return free[0] if free else None

    def decide(p: dict) -> dict:
        kind = p.get("kind")

        if kind == "counter_cap":
            req = p.get("requested_slot")
            slot = nearest_free(req, exclude=[]) if req else None
            if not slot:
                return _close("round cap reached and no free slot near the prospect's request")
            note = "their requested time is free" if slot == req else \
                f"{fmt_slot(req)} is unavailable; closest free slot at their preferred time"
            return {"action": "book", "slot": slot, "by": "auto",
                    "why": f"round cap reached; {note} - invite lets them accept or move it"}

        if kind == "booking_failed":
            lost = p.get("selected_slot")
            slot = nearest_free(lost, exclude=[lost]) if lost else None
            if not slot:
                return _close("accepted slot was lost and no replacement is free")
            return {"action": "book", "slot": slot, "by": "auto",
                    "why": f"accepted slot {fmt_slot(lost)} was taken; booking closest free slot at the same time"}

        if kind == "unclear_reply":
            rnd = p.get("reply_round") or 0
            if rnd >= MAX_REPLY_ROUNDS:
                return _close(f"reply unclear and round cap ({MAX_REPLY_ROUNDS}) reached")
            already = set(p.get("times_proposed") or [])
            free = _free(world, candidate_slots(world.now, days=5 + 2 * rnd))
            fresh = [s for s in (free or []) if s not in already][:3]
            if len(fresh) < 2:
                return _close("reply unclear and no fresh free slots to re-propose")
            return {"action": "propose", "slots": fresh, "by": "auto",
                    "why": "reply unclear; re-proposing fresh checked slots"}

        reasons = {
            "calendar_failed": "calendar unreachable after retries - never book unchecked slots",
            "propose_failed": "could not send the proposal email after retries",
            "no_free_slots": "fewer than 2 free slots to offer",
        }
        return _close(reasons.get(kind, f"unhandled escalation: {p.get('reason')}"))

    return decide
