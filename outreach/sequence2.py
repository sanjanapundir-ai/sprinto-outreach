"""Sequence 2 - Schedule the Call, as a LangGraph state machine.

Why LangGraph here: interdependent state values, loop-backs (a counter-proposal
must remember what we already offered), retries, and a human handoff that has to
pause the run and resume later. LangGraph gives us the shared state object with
read/write discipline, conditional edges, and checkpointing + interrupt().

    START -> check_calendar -> propose_times -> wait_for_reply
                ^   |               ^                |
                |   +--(counter     |                +-- confirm  -> book_meeting -> update_crm -> END
                |       free)-------|--------------->+-- counter  -> check_calendar (loop)
                |                   |                +-- silence  -> check_calendar (nudge) | update_crm (cap)
                +-------------------+                +-- declined -> update_crm
                                                     +-- unclear / cap / tool failure -> human_handoff
    human_handoff -> book_meeting | propose_times | update_crm
"""
from typing import Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .classify import classify_scheduling_reply
from .retry import MAX_REPLY_ROUNDS, run_tool
from .state import OutreachState
from .world import World, alternatives_near, candidate_slots, fmt_slot


def build_sequence2(world: World, checkpointer=None):
    # ---------------------------------------------------------------- nodes
    def check_calendar(state: OutreachState) -> dict:
        requested = state.get("requested_slot")
        already = set(state.get("times_proposed") or [])
        rnd = state.get("reply_round", 0)
        if requested:
            candidates = [requested] + alternatives_near(requested, world.now)
        else:
            # Nudge after silence: look further out each round so we never repeat ourselves.
            candidates = candidate_slots(world.now, days=5 + 2 * rnd)
        res = run_tool(world, "check_calendar", slots=candidates[:20])
        if not res.ok:
            return {"calendar_checked": False,
                    "escalation": f"calendar_failed: check_calendar {res.error_kind} error: {res.error}",
                    "events": [f"check_calendar FAILED after {res.attempts} attempt(s) -> {res.action}"]}

        free = [s["start"] for s in res.value["slots"] if s["free"]]
        update = {"calendar_checked": True, "selected_slot": None, "events": []}
        if requested and requested in free:
            update.update(selected_slot=requested, free_slots=[])
            update["events"].append(f"check_calendar: counter {requested} is FREE -> book it")
            return update

        pool = [s for s in free if s != requested]
        if not requested:
            pool = pool[3 * rnd:]                     # round N offers the next block of free slots
        fresh = [s for s in pool if s not in already]
        alts = (fresh if len(fresh) >= 2 else pool)[:3]
        update["free_slots"] = alts
        note = f"counter {requested} is BUSY; " if requested else ""
        update["events"].append(f"check_calendar ({res.attempts} attempt(s)): {note}free -> {alts}")
        if len(alts) < 2:
            update["escalation"] = "no_free_slots: fewer than 2 free slots available to propose"
        return update

    def propose_times(state: OutreachState) -> dict:
        slots = state["free_slots"][:3]
        rnd = state.get("reply_round", 0) + 1
        res = run_tool(world, "propose_times", to=state["prospect"]["email"], slots=slots, round=rnd)
        if not res.ok:
            return {"escalation": f"propose_failed: propose_times {res.error_kind} error: {res.error}",
                    "events": [f"propose_times FAILED -> {res.action}"]}
        return {
            "times_proposed": res.value["slots"],
            "reply_round": rnd,
            "meeting_status": "pending_confirmation",
            "requested_slot": None,
            "free_slots": [],
            "events": [f"propose_times round {rnd}: " + ", ".join(fmt_slot(s) for s in slots)],
        }

    def wait_for_reply(state: OutreachState) -> dict:
        # Production: the graph would interrupt() here and an inbox webhook would
        # resume it days later from the checkpoint. The mock inbox answers instantly.
        text = world.next_reply(state["prospect"]["id"])
        intent, slot = classify_scheduling_reply(text, state["times_proposed"])
        rnd = state["reply_round"]
        update = {"last_reply": text, "reply_intent": intent,
                  "events": [f"reply round {rnd}: {text!r} -> {intent}" + (f" {slot}" if slot else "")]}

        if intent == "confirm":
            update["selected_slot"] = slot
        elif intent == "counter":
            update.update(requested_slot=slot, calendar_checked=False)   # must re-check
            if rnd >= MAX_REPLY_ROUNDS:
                update["escalation"] = f"counter_cap: counter-proposal after {rnd} rounds (cap {MAX_REPLY_ROUNDS})"
        elif intent == "silence":
            update["calendar_checked"] = False
            if rnd >= MAX_REPLY_ROUNDS:
                update.update(outcome="no_response", meeting_status="unscheduled")
        elif intent == "declined":
            update.update(outcome="declined_after_interest", meeting_status="unscheduled")
        else:  # unclear
            update["escalation"] = "unclear_reply: reply could not be classified"
        return update

    def book_meeting(state: OutreachState) -> dict:
        slot = state["selected_slot"]
        p = state["prospect"]
        res = run_tool(world, "book_meeting", slot=slot, attendee_email=p["email"],
                       title=f"Sprinto x {p['company']} - intro call")
        if not res.ok:
            return {"escalation": f"booking_failed: book_meeting {res.error_kind} error: {res.error}",
                    "events": [f"book_meeting {slot} FAILED after {res.attempts} attempt(s) -> {res.action}"]}
        return {"meeting_status": "confirmed", "booking": res.value, "outcome": "meeting_booked",
                "events": [f"book_meeting: {fmt_slot(slot)} ({res.value['event_id']}), invite sent"]}

    def human_handoff(state: OutreachState) -> dict:
        # Pauses the graph. State is checkpointed; resume with Command(resume=decision).
        decision = interrupt({
            "kind": (state.get("escalation") or "").split(":")[0],
            "reason": state.get("escalation"),
            "requested_slot": state.get("requested_slot"),
            "selected_slot": state.get("selected_slot"),
            "prospect": state["prospect"]["id"],
            "reply_round": state.get("reply_round"),
            "times_proposed": state.get("times_proposed"),
            "last_reply": state.get("last_reply"),
            "options": {"book": {"slot": "ISO"}, "propose": {"slots": ["ISO", "ISO"]},
                        "close": {"outcome": "closed_by_human"}},
        })
        action = decision["action"]
        update = {"escalation": None, "human_action": action,
                  "events": [f"DECISION by {decision.get('by', 'human')}: {action}"
                             + (f" {decision.get('slot') or decision.get('slots') or decision.get('outcome')}")
                             + (f" - {decision['why']}" if decision.get("why") else "")]}
        if action == "book":
            update["selected_slot"] = decision["slot"]
        elif action == "propose":
            update["free_slots"] = decision["slots"]   # human vouches for these slots
        else:
            update.update(outcome=decision.get("outcome", "closed_by_human"), meeting_status="unscheduled")
        return update

    def update_crm(state: OutreachState) -> dict:
        p = state["prospect"]
        outcome = state.get("outcome") or "closed_by_human"
        fields = {
            "reply_status": state.get("reply_status"),
            "meeting_status": state.get("meeting_status"),
            "reply_round": state.get("reply_round"),
            "times_proposed": state.get("times_proposed"),
            "meeting_start": (state.get("booking") or {}).get("start"),
        }
        res = run_tool(world, "update_crm", prospect_id=p["id"], outcome=outcome, fields=fields)
        if not res.ok:  # policy: skip -> dead-letter queue, nothing downstream depends on it
            world.dead_letter.append({"prospect_id": p["id"], "outcome": outcome, "fields": fields,
                                      "error": res.error})
            return {"crm_logged": False, "outcome": outcome,
                    "events": [f"update_crm FAILED -> {res.action} (queued in dead-letter)"]}
        return {"crm_logged": True, "outcome": outcome,
                "events": [f"update_crm: outcome={outcome}"]}

    # --------------------------------------------------------------- routers
    # Routers are pure: they only read state and name the next node.
    def after_calendar(state: OutreachState) -> str:
        if state.get("escalation"):
            return "human_handoff"
        if state.get("selected_slot") and state.get("calendar_checked") and not state.get("free_slots"):
            return "book_meeting"
        return "propose_times"

    def after_propose(state: OutreachState) -> str:
        return "human_handoff" if state.get("escalation") else "wait_for_reply"

    def after_reply(state: OutreachState) -> str:
        if state.get("escalation"):
            return "human_handoff"
        intent = state["reply_intent"]
        if intent == "confirm":
            return "book_meeting"
        if intent == "counter":
            return "check_calendar"
        if intent == "silence":
            return "update_crm" if state.get("outcome") else "check_calendar"
        return "update_crm"   # declined

    def after_book(state: OutreachState) -> str:
        return "human_handoff" if state.get("escalation") else "update_crm"

    def after_human(state: OutreachState) -> str:
        return {"book": "book_meeting", "propose": "propose_times"}.get(state["human_action"], "update_crm")

    # ----------------------------------------------------------------- graph
    g = StateGraph(OutreachState)
    for name, fn in [("check_calendar", check_calendar), ("propose_times", propose_times),
                     ("wait_for_reply", wait_for_reply), ("book_meeting", book_meeting),
                     ("human_handoff", human_handoff), ("update_crm", update_crm)]:
        g.add_node(name, fn)

    g.add_edge(START, "check_calendar")
    g.add_conditional_edges("check_calendar", after_calendar,
                            ["propose_times", "book_meeting", "human_handoff"])
    g.add_conditional_edges("propose_times", after_propose, ["wait_for_reply", "human_handoff"])
    g.add_conditional_edges("wait_for_reply", after_reply,
                            ["book_meeting", "check_calendar", "human_handoff", "update_crm"])
    g.add_conditional_edges("book_meeting", after_book, ["update_crm", "human_handoff"])
    g.add_conditional_edges("human_handoff", after_human,
                            ["book_meeting", "propose_times", "update_crm"])
    g.add_edge("update_crm", END)

    return g.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())
