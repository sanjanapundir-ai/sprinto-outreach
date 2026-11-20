"""The retry table - decided before the agent runs, never improvised mid-run.

Each tool is scored on two questions:
  Q1. Would trying again plausibly help?        -> transient vs permanent
  Q2. Does anything after this step depend on it? -> what to do when retries run out

Actions:
  retry    - try again with exponential backoff, up to max_retries extra attempts
  skip     - record the failure (dead-letter queue) and carry on; nothing downstream needs it
  escalate - pause the graph and hand off to a human (LangGraph interrupt)
  abort    - stop this prospect's run and log the reason
"""
import time
from dataclasses import dataclass
from typing import Any, Optional

from .tools import TOOL_IMPLS
from .world import PermanentToolError, TransientToolError, World

# Global iteration caps - hard limits against runaway loops / token burn.
MAX_REPLY_ROUNDS = 3        # proposals per prospect (1 first proposal + 2 retries/counters)
GRAPH_RECURSION_LIMIT = 40  # LangGraph super-steps per invocation


@dataclass(frozen=True)
class RetryPolicy:
    tool: str
    max_retries: int                 # extra attempts after the first, for transient errors
    on_transient_exhausted: str      # skip | escalate | abort
    on_permanent: str                # skip | escalate | abort
    downstream_dependency: str       # Q2, documented for humans
    example_transient: str
    example_permanent: str


RETRY_TABLE = {
    p.tool: p
    for p in [
        RetryPolicy("send_email", 2, "abort", "abort",
                    "Yes - the whole sequence starts from this email",
                    "SMTP timeout / 429", "Hard bounce (550)"),
        RetryPolicy("check_calendar", 3, "escalate", "escalate",
                    "Yes - we must never propose or book unchecked slots",
                    "Calendar API timeout", "Calendar revoked / 403"),
        RetryPolicy("propose_times", 2, "escalate", "escalate",
                    "Yes - the prospect is waiting on times",
                    "SMTP timeout", "Thread deleted / recipient blocked"),
        RetryPolicy("book_meeting", 3, "escalate", "escalate",
                    "Yes - a confirmed prospect must get an invite",
                    "Calendar API 503", "Slot taken since check / invalid attendee"),
        RetryPolicy("update_crm", 3, "skip", "skip",
                    "No - it is the last step; failed writes go to a dead-letter queue",
                    "CRM rate limit", "Record locked / field validation error"),
    ]
}


@dataclass
class ToolResult:
    ok: bool
    value: Any = None
    error: Optional[str] = None
    error_kind: Optional[str] = None   # transient | permanent
    action: Optional[str] = None       # skip | escalate | abort  (only when ok is False)
    attempts: int = 0


def run_tool(world: World, name: str, **kwargs) -> ToolResult:
    """Call a tool under its retry policy. Never raises tool errors."""
    policy = RETRY_TABLE[name]
    fn = TOOL_IMPLS[name]
    attempt = 0
    while True:
        attempt += 1
        try:
            value = fn(world, **kwargs)
            world.tool_calls.append({"tool": name, "attempt": attempt, "ok": True})
            return ToolResult(ok=True, value=value, attempts=attempt)
        except TransientToolError as exc:
            world.tool_calls.append({"tool": name, "attempt": attempt, "ok": False, "kind": "transient"})
            if attempt > policy.max_retries:
                return ToolResult(False, error=str(exc), error_kind="transient",
                                  action=policy.on_transient_exhausted, attempts=attempt)
            if world.backoff_seconds:
                time.sleep(world.backoff_seconds * 2 ** (attempt - 1))
        except PermanentToolError as exc:
            world.tool_calls.append({"tool": name, "attempt": attempt, "ok": False, "kind": "permanent"})
            return ToolResult(False, error=str(exc), error_kind="permanent",
                              action=policy.on_permanent, attempts=attempt)
