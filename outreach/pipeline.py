"""End-to-end run for one prospect: ICP gate -> Sequence 1 -> (if interested) Sequence 2.

Every result also carries:
  path       - the steps the run went through, in order (Sequence 1 steps + graph nodes)
  decisions  - each handoff: what the graph reported and what was decided
"""
from typing import Callable, List, Optional, Union

from langgraph.types import Command

from .decider import make_auto_decider
from .icp import qualify
from .retry import GRAPH_RECURSION_LIMIT, run_tool
from .sequence1 import run_sequence1
from .sequence2 import build_sequence2
from .state import initial_sequence2_state
from .world import World

HumanFn = Callable[[dict], dict]


def _log_crm(world: World, prospect: dict, outcome: str, events: list, path: list, **fields) -> dict:
    res = run_tool(world, "update_crm", prospect_id=prospect["id"], outcome=outcome, fields=fields)
    if not res.ok:
        world.dead_letter.append({"prospect_id": prospect["id"], "outcome": outcome, "error": res.error})
        events.append(f"update_crm FAILED -> {res.action} (queued in dead-letter)")
    else:
        events.append(f"update_crm: outcome={outcome}")
    return {"outcome": outcome, "crm_logged": res.ok, "events": events, "prospect": prospect,
            "path": path + ["update_crm"], "decisions": [], "paused": False, **fields}


def run_prospect(prospect: dict, world: World, human: Union[str, HumanFn, None] = "auto", graph=None) -> dict:
    """Returns the final state dict.

    human="auto" (default): escalations are decided automatically (decider.py).
    human=callable: that function decides (e.g. a person at the console).
    human=None: the run pauses and returns `paused=True` with the interrupt payload;
    resume it later from the checkpoint with resume_prospect()."""
    if human == "auto":
        human = make_auto_decider(world)
    path = ["icp_gate"]
    ok, reason = qualify(prospect)
    if not ok:
        return _log_crm(world, prospect, "disqualified", [f"ICP short-circuit: {reason}"], path, reason=reason)

    s1 = run_sequence1(prospect, world)
    path += ["send_email"] if s1.outcome in ("bounced", "send_failed") else ["send_email", "await_reply"]
    if s1.outcome:  # Sequence 1 ended the run (bounced, not_interested, no_reply, needs_review)
        return _log_crm(world, prospect, s1.outcome, s1.events, path, reply_status=s1.reply_status)

    graph = graph or build_sequence2(world)
    config = {"configurable": {"thread_id": prospect["id"]}, "recursion_limit": GRAPH_RECURSION_LIMIT}
    state = initial_sequence2_state(prospect)
    state["events"] = s1.events
    return _drive(graph, config, state, human, path, [])


def resume_prospect(graph, prospect_id: str, decision: dict, human: Optional[HumanFn] = None) -> dict:
    config = {"configurable": {"thread_id": prospect_id}, "recursion_limit": GRAPH_RECURSION_LIMIT}
    return _drive(graph, config, Command(resume=decision), human, [], [])


def _stream(graph, inp, config, path: List[str]) -> Optional[dict]:
    """Run until the graph ends or pauses; record visited nodes; return the interrupt payload if paused."""
    paused = None
    for chunk in graph.stream(inp, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                paused = update[0].value
            else:
                path.append(node)
    return paused


def _drive(graph, config, inp, human, path, decisions):
    payload = _stream(graph, inp, config, path)
    while payload is not None:
        if human is None:
            return {**graph.get_state(config).values, "paused": True, "interrupt": payload,
                    "path": path, "decisions": decisions}
        decision = human(payload)
        decisions.append({"situation": payload, "decision": decision})
        payload = _stream(graph, Command(resume=decision), config, path)
    return {**graph.get_state(config).values, "paused": False, "path": path, "decisions": decisions}
