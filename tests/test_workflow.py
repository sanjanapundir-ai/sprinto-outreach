import pytest

from outreach.classify import classify_initial_reply, classify_scheduling_reply
from outreach.icp import qualify
from outreach.pipeline import resume_prospect, run_prospect
from outreach.retry import MAX_REPLY_ROUNDS, RETRY_TABLE, run_tool
from outreach.decider import make_auto_decider
from outreach.scenarios import PROSPECTS, SCENARIOS, make_world
from outreach.sequence2 import build_sequence2
from outreach.state import CORE_FIELDS, OutreachState
from outreach.tools import TOOL_IMPLS, TOOL_SPECS
from outreach.world import World


# ---------------------------------------------------------------- scenarios
@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenario_outcome(sc):
    world = make_world(sc)
    final = run_prospect(PROSPECTS[sc["prospect"]], world)   # auto decisions
    assert final["outcome"] == sc["expect"]
    assert not final.get("paused")


def run(name):
    sc = next(s for s in SCENARIOS if s["name"] == name)
    world = make_world(sc)
    return run_prospect(PROSPECTS[sc["prospect"]], world), world


def test_booked_meeting_state_and_side_effects():
    final, world = run("happy_path")
    assert final["meeting_status"] == "confirmed" and final["crm_logged"] is True
    assert final["reply_round"] == 1 and final["calendar_checked"] is True
    assert [m["kind"] for m in world.outbox] == ["cold_outreach", "propose_times"]
    assert world.calendar_events[0]["start"] == final["times_proposed"][1]
    assert world.crm["p01"]["outcome"] == "meeting_booked"


def test_counter_proposal_loops_back_with_new_slots():
    final, world = run("counter_busy")
    proposals = [m for m in world.outbox if m["kind"] == "propose_times"]
    assert len(proposals) == 2 and final["reply_round"] == 2
    assert "2026-11-20T14:00" not in final["times_proposed"]          # busy counter never offered
    assert world.calendar_events[0]["start"] == "2026-11-20T16:00"


def test_free_counter_is_booked_without_new_proposal():
    final, world = run("counter_free")
    assert final["reply_round"] == 1
    assert world.calendar_events[0]["start"] == "2026-11-24T11:30"


def test_silence_respects_round_cap_and_never_repeats_slots():
    final, world = run("silence_after_interest")
    proposals = [m["body"] for m in world.outbox if m["kind"] == "propose_times"]
    assert len(proposals) == MAX_REPLY_ROUNDS
    assert len(set(proposals)) == MAX_REPLY_ROUNDS
    assert final["meeting_status"] == "unscheduled" and not world.calendar_events


# ---------------------------------------------------------------- retry table
def test_retry_table_covers_every_tool():
    assert set(RETRY_TABLE) == set(TOOL_IMPLS) == {t["name"] for t in TOOL_SPECS}
    for p in RETRY_TABLE.values():
        assert p.on_transient_exhausted in {"skip", "escalate", "abort"}
        assert p.on_permanent in {"skip", "escalate", "abort"}


def test_transient_retries_then_succeeds():
    w = World(failures={"check_calendar": ["transient", "transient"]})
    res = run_tool(w, "check_calendar", slots=["2026-11-19T10:00"])
    assert res.ok and res.attempts == 3


def test_transient_exhausted_follows_policy():
    w = World(failures={"check_calendar": ["transient"] * 10})
    res = run_tool(w, "check_calendar", slots=["2026-11-19T10:00"])
    assert not res.ok and res.action == "escalate"
    assert res.attempts == RETRY_TABLE["check_calendar"].max_retries + 1


def test_permanent_failure_is_never_retried():
    w = World(bounces={"x@y.com"})
    res = run_tool(w, "send_email", to="x@y.com", subject="s", body="b")
    assert not res.ok and res.attempts == 1 and res.action == "abort"


def test_crm_failure_goes_to_dead_letter():
    final, world = run("no_reply_and_crm_down")
    assert final["crm_logged"] is False and world.dead_letter[0]["prospect_id"] == "p06"


# ---------------------------------------------------------------- checkpointing / handoff
def test_handoff_pauses_and_resumes_from_checkpoint():
    sc = next(s for s in SCENARIOS if s["name"] == "counter_cap_handoff")
    world = make_world(sc)
    graph = build_sequence2(world)
    paused = run_prospect(PROSPECTS["p08"], world, human=None, graph=graph)

    assert paused["paused"] and "cap" in paused["interrupt"]["reason"]
    snapshot = graph.get_state({"configurable": {"thread_id": "p08"}})
    assert snapshot.next == ("human_handoff",)
    assert snapshot.values["reply_round"] == MAX_REPLY_ROUNDS
    assert not world.calendar_events                    # nothing booked while paused

    final = resume_prospect(graph, "p08", {"action": "book", "slot": "2026-11-23T15:00"})
    assert final["meeting_status"] == "confirmed" and final["crm_logged"]
    assert world.calendar_events[-1]["start"] == "2026-11-23T15:00"


def test_unclear_reply_escalates():
    world = World(replies={"p01": ["Sure!", "hmm let me think about it"]})
    final = run_prospect(PROSPECTS["p01"], world, human=lambda p: {"action": "close"})
    assert final["outcome"] == "closed_by_human"
    assert any("DECISION by human: close" in e for e in final["events"])


def test_declined_after_interest():
    world = World(replies={"p01": ["Yes, let's talk", "Actually never mind, we're going another way"]})
    final = run_prospect(PROSPECTS["p01"], world)
    assert final["outcome"] == "declined_after_interest" and final["crm_logged"]


# ---------------------------------------------------------------- design contracts
def test_state_schema_has_the_six_fixed_fields():
    assert set(CORE_FIELDS) <= set(OutreachState.__annotations__)
    assert len(CORE_FIELDS) == 6


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=[t["name"] for t in TOOL_SPECS])
def test_tool_descriptions_follow_four_golden_rules(spec):
    d = spec["description"]
    positions = [d.find(h) for h in ("WHAT IT DOES:", "WHEN TO USE:", "WHEN NOT TO USE:", "RETURNS:")]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    assert spec["input_schema"]["type"] == "object"


@pytest.mark.parametrize("text,expected", [
    (None, "no_reply"), ("", "no_reply"),
    ("Not interested, thanks", "not_interested"),
    ("Sounds good, let's talk", "interested"),
    ("Who is this?", "unclear"),
])
def test_initial_classifier(text, expected):
    assert classify_initial_reply(text) == expected


def test_scheduling_classifier():
    P = ["2026-11-18T14:00", "2026-11-18T16:00", "2026-11-19T10:00"]
    assert classify_scheduling_reply("Option 3", P) == ("confirm", P[2])
    assert classify_scheduling_reply("the second works", P) == ("confirm", P[1])
    assert classify_scheduling_reply("2026-11-18 16:00 is fine", P) == ("confirm", P[1])
    assert classify_scheduling_reply("can we do 2026-11-25 9:30", P) == ("counter", "2026-11-25T09:30")
    assert classify_scheduling_reply(None, P) == ("silence", None)


def test_icp_short_circuits():
    assert qualify(PROSPECTS["p01"]) == (True, "qualified")
    assert qualify(PROSPECTS["p12"])[1].startswith("too_small")
    assert qualify(PROSPECTS["p13"])[1].startswith("already_on_competitor")
    assert qualify({**PROSPECTS["p01"], "email": ""})[1].startswith("missing_email")


# ---------------------------------------------------------------- automatic decisions
def test_auto_counter_cap_books_closest_slot_at_preferred_time():
    final, world = run("counter_cap_handoff")
    assert world.calendar_events[-1]["start"] == "2026-11-23T15:00"     # Sun 15:00 -> Mon 15:00
    assert any("DECISION by auto: book" in e for e in final["events"])


def test_auto_booking_conflict_books_same_time_next_day():
    final, world = run("booking_conflict")
    assert world.calendar_events[-1]["start"] == "2026-11-19T14:00"


def test_auto_calendar_down_never_books_blind():
    final, world = run("calendar_down_handoff")
    assert final["outcome"] == "needs_review" and not world.calendar_events


def test_auto_unclear_reply_reproposes_then_books():
    world = World(replies={"p01": ["Sure!", "hmm, maybe?", "Option 1 works"]})
    final = run_prospect(PROSPECTS["p01"], world)
    assert final["outcome"] == "meeting_booked" and final["reply_round"] == 2
    assert any("DECISION by auto: propose" in e for e in final["events"])


def test_auto_unclear_reply_at_cap_closes():
    decide = make_auto_decider(World())
    d = decide({"kind": "unclear_reply", "reply_round": MAX_REPLY_ROUNDS, "times_proposed": []})
    assert d["action"] == "close" and d["outcome"] == "needs_review"


def test_auto_decider_closes_when_calendar_check_fails():
    decide = make_auto_decider(World(failures={"check_calendar": ["transient"] * 10}))
    d = decide({"kind": "counter_cap", "requested_slot": "2026-11-22T15:00"})
    assert d["action"] == "close"


# ---------------------------------------------------------------- dashboard API
def test_dashboard_runs_all_scenarios_as_json():
    import json
    from outreach.dashboard import meta, run_scenarios
    runs = run_scenarios("all")
    assert len(runs) == len(SCENARIOS) and all(r["passed"] for r in runs)
    json.dumps(runs), json.dumps(meta())                      # must be serialisable for the page
    cap = next(r for r in runs if r["name"] == "counter_cap_handoff")
    assert cap["path"][-3:] == ["human_handoff", "book_meeting", "update_crm"]
    assert cap["decisions"][0]["decision"]["by"] == "auto"


def test_dashboard_custom_run_and_validation():
    from outreach.dashboard import run_custom
    r = run_custom({"prospect_id": "p02", "replies": ["Yes, let's talk.", None, "Option 1"],
                    "failures": {"check_calendar": ["transient"]}})
    assert r["outcome"] == "meeting_booked" and r["core"]["reply_round"] == 2
    for bad in ({"prospect_id": "nope"}, {"prospect_id": "p01", "failures": {"rm_rf": ["transient"]}},
                {"prospect_id": "p01", "failures": {"send_email": ["explode"]}}):
        with pytest.raises(ValueError):
            run_custom(bad)


# ---------------------------------------------------------------- components map
def test_components_map_matches_the_code():
    from pathlib import Path
    from outreach.components import COMPONENTS, EDGES, LAYERS
    root = Path(__file__).resolve().parent.parent
    ids = [c["id"] for c in COMPONENTS]
    assert len(ids) == len(set(ids)), "component ids must be unique"
    layer_ids = {l["id"] for l in LAYERS}
    for c in COMPONENTS:
        assert (root / c["file"]).is_file(), f"{c['id']}: {c['file']} does not exist"
        assert c["layer"] in layer_ids
        step = c["related"].get("step")
        assert step is None or step in {"send_email", "check_calendar", "propose_times", "wait_for_reply",
                                         "book_meeting", "human_handoff", "update_crm"}
        assert c["related"].get("tool") in (None, *TOOL_IMPLS)
    for a, b, _ in EDGES:
        assert a in ids and b in ids, f"edge {a}->{b} points at an unknown component"
    tool_cards = {c["name"] for c in COMPONENTS if c["layer"] == "tools"}
    assert tool_cards == set(TOOL_IMPLS), "every tool appears on the map exactly once"
