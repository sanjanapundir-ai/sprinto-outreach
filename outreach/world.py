"""External systems the agent touches: mail, inbox, calendar, CRM.

These are in-memory stand-ins so the workflow runs offline and deterministically.
Swap `World` for real adapters (Gmail / Google Calendar / HubSpot, or their MCP
servers) without touching the graph: the tools in `tools.py` are the only code
that talks to it.

Failures can be injected per tool to exercise the retry table:
    World(failures={"check_calendar": ["transient", "transient"]})
"""
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Optional, Set


class ToolError(Exception):
    """Base class for tool failures."""


class TransientToolError(ToolError):
    """Trying again could plausibly help (timeout, 429, 5xx)."""


class PermanentToolError(ToolError):
    """Trying again changes nothing (bounce, 404, invalid input, slot taken)."""


BUSINESS_HOURS = (9, 18)          # [start, end) local hours
PROPOSAL_HOURS = (10, 14, 16)     # hours we like to offer


def parse_slot(slot: str) -> datetime:
    return datetime.fromisoformat(slot)


def fmt_slot(slot: str) -> str:
    return parse_slot(slot).strftime("%a %d %b, %H:%M")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def next_business_days(start: datetime, n: int) -> List[datetime]:
    days, d = [], start
    while len(days) < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    return days


@dataclass
class World:
    now: datetime = field(default_factory=lambda: datetime(2026, 11, 17, 9, 0))
    busy: Set[str] = field(default_factory=set)
    bounces: Set[str] = field(default_factory=set)
    # prospect_id -> queue of reply texts; None means "no reply in the wait window"
    replies: Dict[str, List[Optional[str]]] = field(default_factory=dict)
    # tool name -> queue of "transient" | "permanent" to raise on the next calls
    failures: Dict[str, List[str]] = field(default_factory=dict)
    backoff_seconds: float = 0.0

    # observable side effects
    outbox: List[dict] = field(default_factory=list)
    calendar_events: List[dict] = field(default_factory=list)
    crm: Dict[str, dict] = field(default_factory=dict)
    dead_letter: List[dict] = field(default_factory=list)
    tool_calls: List[dict] = field(default_factory=list)

    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1), repr=False)

    # ------------------------------------------------------------------
    def maybe_fail(self, tool: str) -> None:
        queue = self.failures.get(tool)
        if queue:
            kind = queue.pop(0)
            if kind == "transient":
                raise TransientToolError(f"{tool}: upstream timeout (simulated)")
            raise PermanentToolError(f"{tool}: permanent failure (simulated)")

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):04d}"

    # ---- mail ---------------------------------------------------------
    def send(self, to: str, subject: str, body: str, kind: str) -> dict:
        if to in self.bounces:
            raise PermanentToolError(f"550 mailbox unavailable: {to}")
        msg = {"id": self.new_id("msg"), "to": to, "subject": subject, "body": body, "kind": kind}
        self.outbox.append(msg)
        return msg

    def next_reply(self, prospect_id: str) -> Optional[str]:
        queue = self.replies.get(prospect_id, [])
        return queue.pop(0) if queue else None

    # ---- calendar -----------------------------------------------------
    def is_free(self, slot: str) -> bool:
        dt = parse_slot(slot)
        return (
            dt > self.now
            and dt.weekday() < 5
            and BUSINESS_HOURS[0] <= dt.hour < BUSINESS_HOURS[1]
            and slot not in self.busy
        )

    def book(self, slot: str, attendee: str, title: str) -> dict:
        if not self.is_free(slot):
            raise PermanentToolError(f"slot {slot} is no longer free")
        self.busy.add(slot)
        event = {"event_id": self.new_id("evt"), "start": slot, "attendee": attendee,
                 "title": title, "invite_sent": True}
        self.calendar_events.append(event)
        return event

    # ---- CRM ----------------------------------------------------------
    def upsert_crm(self, prospect_id: str, fields: dict) -> dict:
        record = self.crm.setdefault(prospect_id, {"record_id": self.new_id("crm")})
        record.update(fields)
        return record


# ---- slot generation (pure helpers, no I/O) --------------------------
def candidate_slots(now: datetime, days: int = 5) -> List[str]:
    return [iso(d.replace(hour=h, minute=0)) for d in next_business_days(now, days) for h in PROPOSAL_HOURS]


def alternatives_near(requested: str, now: datetime) -> List[str]:
    """Same day at our usual hours first, then the following business days."""
    req = parse_slot(requested)
    same_day = [iso(req.replace(hour=h, minute=0)) for h in PROPOSAL_HOURS]
    later = candidate_slots(req.replace(hour=0, minute=0), days=3)
    return [s for s in same_day + later if s != requested]
