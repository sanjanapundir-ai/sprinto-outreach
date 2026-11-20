"""Scripted scenarios: one per branch of the workflow and per row of the retry table.

"Today" is Tue 17 Nov 2026 09:00. The AE is already busy Wed 18 Nov 10:00.
"""
import json
from pathlib import Path
from typing import Dict, List

from .world import World

PROSPECTS: Dict[str, dict] = {
    p["id"]: p for p in json.loads((Path(__file__).parent.parent / "data" / "prospects.json").read_text())
}
BASE_BUSY = {"2026-11-18T10:00"}

SCENARIOS: List[dict] = [
    dict(name="happy_path", prospect="p01",
         about="Interested -> picks Option 2 -> booked",
         replies=["Sounds good, happy to chat.", "Option 2 works for me."],
         expect="meeting_booked"),
    dict(name="counter_busy", prospect="p02",
         about="Counter-proposes a busy time -> new options near it -> accepts",
         busy={"2026-11-20T14:00"},
         replies=["Yes, let's talk.", "None of those work - could we do 2026-11-20 14:00?", "The second one is great."],
         expect="meeting_booked"),
    dict(name="counter_free", prospect="p03",
         about="Counter-proposes a free time -> booked directly",
         replies=["Interested - tell me more.", "Could we do 2026-11-24 11:30 instead?"],
         expect="meeting_booked"),
    dict(name="silence_after_interest", prospect="p04",
         about="Interested, then silent through 3 proposals -> no_response",
         replies=["Sure, open to a call.", None, None, None],
         expect="no_response"),
    dict(name="not_interested", prospect="p05",
         about="Sequence 1 only -> not_interested",
         replies=["Thanks, but not interested right now."],
         expect="not_interested"),
    dict(name="no_reply_and_crm_down", prospect="p06",
         about="No reply; CRM write fails 4x -> skipped to dead-letter queue",
         replies=[], failures={"update_crm": ["transient"] * 4},
         expect="no_reply"),
    dict(name="bounce", prospect="p07",
         about="Hard bounce on the cold email -> permanent -> abort, no retry",
         bounces={"sara@tallyho.co"},
         expect="bounced"),
    dict(name="counter_cap_handoff", prospect="p08",
         about="Keeps counter-proposing unavailable times -> cap hit -> auto-books closest slot at their time",
         replies=["Yes, keen.", "How about 2026-11-18 10:00?", "Then 2026-11-21 10:00?", "OK, 2026-11-22 15:00?"],
         expect="meeting_booked"),
    dict(name="flaky_calendar", prospect="p09",
         about="Calendar times out twice, third attempt succeeds",
         failures={"check_calendar": ["transient", "transient"]},
         replies=["Sure.", "The first one works."],
         expect="meeting_booked"),
    dict(name="calendar_down_handoff", prospect="p10",
         about="Calendar fails 4x (cap 3 retries) -> escalate -> auto-closes as needs_review",
         failures={"check_calendar": ["transient"] * 4},
         replies=["Yes, interested."],
         expect="needs_review"),
    dict(name="booking_conflict", prospect="p11",
         about="Slot taken between proposal and booking -> permanent -> auto-books same time next day",
         failures={"book_meeting": ["permanent"]},
         replies=["Happy to chat.", "Option 1 please."],
         expect="meeting_booked"),
    dict(name="disqualified_small", prospect="p12",
         about="ICP short-circuit: too small, never emailed", expect="disqualified"),
    dict(name="disqualified_competitor", prospect="p13",
         about="ICP short-circuit: already certified on a competitor", expect="disqualified"),
]


def make_world(sc: dict) -> World:
    return World(
        busy=set(BASE_BUSY) | set(sc.get("busy", ())),
        bounces=set(sc.get("bounces", ())),
        replies={sc["prospect"]: list(sc.get("replies", []))},
        failures={k: list(v) for k, v in sc.get("failures", {}).items()},
    )

