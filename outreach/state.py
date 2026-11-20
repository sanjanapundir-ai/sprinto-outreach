"""The central state object.

Defined up front, before any conversation starts. Every node reads from it and
writes back to it; LangGraph merges each node's returned dict into the state and
checkpoints the result after every step.

The SIX FIXED FIELDS are the workflow's memory and are the only things the
routing functions are allowed to branch on (plus the parsed reply intent and the
escalation flag). Everything else is payload: data the nodes need to do their
job, kept out of the routing logic on purpose.
"""
import operator
from typing import Annotated, List, Literal, Optional, TypedDict

ReplyStatus = Literal["no_reply", "interested", "not_interested"]
MeetingStatus = Literal["unscheduled", "pending_confirmation", "confirmed"]


class OutreachState(TypedDict, total=False):
    # ---- The six fixed fields -------------------------------------------
    reply_status: ReplyStatus          # result of Sequence 1
    calendar_checked: bool             # True once the slots we hold are verified free
    times_proposed: List[str]          # ISO slots in the most recent proposal email
    reply_round: int                   # 0 = nothing proposed yet, 1 = first proposal, 2+ = retries
    meeting_status: MeetingStatus
    crm_logged: bool

    # ---- Payload (not used for routing except where noted) ---------------
    prospect: dict
    free_slots: List[str]              # verified-free slots ready to propose
    requested_slot: Optional[str]      # prospect's counter-proposal
    selected_slot: Optional[str]       # slot we are about to book
    reply_intent: Optional[str]        # confirm | counter | silence | declined | unclear (routing)
    last_reply: Optional[str]          # raw text of the latest reply
    escalation: Optional[str]          # set => route to human_handoff (routing)
    human_action: Optional[str]        # book | propose | close (routing, after handoff)
    booking: Optional[dict]
    outcome: Optional[str]             # final CRM outcome
    events: Annotated[List[str], operator.add]   # append-only audit trail


def initial_sequence2_state(prospect: dict) -> OutreachState:
    """State handed from Sequence 1 to Sequence 2 (only for `interested`)."""
    return OutreachState(
        reply_status="interested",
        calendar_checked=False,
        times_proposed=[],
        reply_round=0,
        meeting_status="unscheduled",
        crm_logged=False,
        prospect=prospect,
        free_slots=[],
        requested_slot=None,
        selected_slot=None,
        reply_intent=None,
        last_reply=None,
        escalation=None,
        human_action=None,
        booking=None,
        outcome=None,
        events=[],
    )


CORE_FIELDS = (
    "reply_status",
    "calendar_checked",
    "times_proposed",
    "reply_round",
    "meeting_status",
    "crm_logged",
)
