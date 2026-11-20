"""Components map: every moving part of the agent, where it lives, and how parts connect.

This is the single source for the dashboard's "Components" tab and its PDF export.
tests/test_workflow.py checks that every file listed here exists and every edge
points at a real component, so the map cannot drift away from the code.
"""

LAYERS = [
    {"id": "data", "title": "Data & fit", "step": "1", "band": False,
     "subtitle": "Who is worth an email at all"},
    {"id": "seq1", "title": "Send & wait", "step": "2", "band": False,
     "subtitle": "Sequence 1 · plain Python"},
    {"id": "seq2", "title": "Schedule the call", "step": "3", "band": False,
     "subtitle": "Sequence 2 · LangGraph state machine"},
    {"id": "tools", "title": "Tools", "step": "4", "band": False,
     "subtitle": "The only way the agent touches anything"},
    {"id": "ext", "title": "Outside world", "step": "5", "band": False,
     "subtitle": "Simulated today, real APIs later"},
    {"id": "brain", "title": "Shared brain", "step": "", "band": True,
     "subtitle": "Used by every step above"},
    {"id": "delivery", "title": "Delivery", "step": "", "band": True,
     "subtitle": "How people run and see the agent"},
]

C = lambda **kw: kw  # noqa: E731  (keeps the table below readable)

COMPONENTS = [
    # ---- 1 · Data & fit -------------------------------------------------------
    C(id="prospects", layer="data", name="Prospect records", file="data/prospects.json",
      summary="Cleaned company and contact data from the research step: industry, size, compliance status, buyer role.",
      inputs=["Data providers first (Apollo, Hunter), scraping last", "Cleaning to cut tokens and cost"],
      outputs=["One record per prospect"], related={}),
    C(id="icp_gate", layer="data", name="ICP gate", file="outreach/icp.py",
      summary="Checks each prospect against Sprinto's ideal customer profile. Short-circuit rules run in order; the first one that fires stops the prospect before any email.",
      inputs=["Prospect record"], outputs=["qualified, or the rule that disqualified it"],
      related={"rule": "too_small"}),

    # ---- 2 · Send & wait ------------------------------------------------------
    C(id="sequence1", layer="seq1", name="Send & wait", file="outreach/sequence1.py",
      summary="Drafts and sends the cold email, waits for a reply, and sorts it into one of three answers. Flat logic with no loops, so it is hand-written, not a graph.",
      inputs=["Qualified prospect"], outputs=["reply_status: interested · not_interested · no_reply"],
      related={"step": "send_email"}),
    C(id="classifier", layer="seq1", name="Reply reader", file="outreach/classify.py",
      summary="Reads replies. First reply: interested, not interested, no reply or unclear. Scheduling replies: confirm, counter-proposal, silence, declined or unclear.",
      inputs=["Reply text", "Times already proposed"], outputs=["Intent and slot"],
      related={"rule": "unclear_reply"}),

    # ---- 3 · Schedule the call (LangGraph nodes) ------------------------------
    C(id="n_check_calendar", layer="seq2", name="Check calendar", file="outreach/sequence2.py",
      summary="Finds free slots, or checks the prospect's counter-proposal. A free counter is booked directly; a busy one gets nearby alternatives.",
      inputs=["requested_slot", "times_proposed", "reply_round"], outputs=["calendar_checked", "free slots"],
      related={"step": "check_calendar", "tool": "check_calendar"}),
    C(id="n_propose_times", layer="seq2", name="Propose times", file="outreach/sequence2.py",
      summary="Emails 2-3 checked slots as numbered options and starts a new reply round.",
      inputs=["Free slots"], outputs=["times_proposed", "reply_round + 1", "meeting_status = pending_confirmation"],
      related={"step": "propose_times", "tool": "propose_times"}),
    C(id="n_wait_for_reply", layer="seq2", name="Wait for reply", file="outreach/sequence2.py",
      summary="Reads the answer and routes: confirm → book, counter → re-check calendar, silence → nudge or close at the cap, unclear → decide.",
      inputs=["Inbox reply"], outputs=["Intent", "escalation flag when needed"],
      related={"step": "wait_for_reply"}),
    C(id="n_book_meeting", layer="seq2", name="Book meeting", file="outreach/sequence2.py",
      summary="Creates the calendar event and sends the invite for the accepted slot.",
      inputs=["selected_slot"], outputs=["meeting_status = confirmed", "event"],
      related={"step": "book_meeting", "tool": "book_meeting"}),
    C(id="n_decide", layer="seq2", name="Decide", file="outreach/sequence2.py",
      summary="Where the graph pauses (LangGraph interrupt) when it can't continue on its own. The automatic decider answers by default; a person can answer instead.",
      inputs=["Escalation: kind, reason, slots, round"], outputs=["book · propose · close"],
      related={"step": "human_handoff", "rule": "counter_cap"}),
    C(id="n_update_crm", layer="seq2", name="Update CRM", file="outreach/sequence2.py",
      summary="Writes the final outcome once, as the last step of every run.",
      inputs=["Outcome and meeting details"], outputs=["crm_logged"],
      related={"step": "update_crm", "tool": "update_crm"}),

    # ---- 4 · Tools ------------------------------------------------------------
    C(id="t_send_email", layer="tools", name="send_email", file="outreach/tools.py",
      summary="Sends the first cold email. Description follows the four golden rules: what, when, when not, returns.",
      inputs=["to, subject, body"], outputs=["message_id"], related={"tool": "send_email", "rule": "send_email"}),
    C(id="t_check_calendar", layer="tools", name="check_calendar", file="outreach/tools.py",
      summary="Says whether specific slots are free on the account executive's calendar.",
      inputs=["Up to 20 slots"], outputs=["free / busy per slot"], related={"tool": "check_calendar", "rule": "check_calendar"}),
    C(id="t_propose_times", layer="tools", name="propose_times", file="outreach/tools.py",
      summary="send_email in propose mode: replies in the thread with 2-3 checked options.",
      inputs=["to, slots, round"], outputs=["message_id, slots sent"], related={"tool": "propose_times", "rule": "propose_times"}),
    C(id="t_book_meeting", layer="tools", name="book_meeting", file="outreach/tools.py",
      summary="Creates the event and sends the invite. Fails permanently if the slot was taken meanwhile.",
      inputs=["slot, attendee, title"], outputs=["event_id"], related={"tool": "book_meeting", "rule": "book_meeting"}),
    C(id="t_update_crm", layer="tools", name="update_crm", file="outreach/tools.py",
      summary="Writes the outcome to the CRM record, creating it if needed.",
      inputs=["prospect_id, outcome, fields"], outputs=["record_id"], related={"tool": "update_crm", "rule": "update_crm"}),

    # ---- 5 · Outside world ----------------------------------------------------
    C(id="x_mail", layer="ext", name="Mail & inbox", file="outreach/world.py",
      summary="Sends emails and delivers replies. Simulated, with bounces and scripted replies; swap for Gmail or its MCP server.",
      inputs=["Emails from tools"], outputs=["Replies, bounces"], related={}),
    C(id="x_calendar", layer="ext", name="Calendar", file="outreach/world.py",
      summary="Working hours, busy slots and bookings. Simulated; swap for Google Calendar.",
      inputs=["Checks and bookings"], outputs=["Free/busy, events"], related={}),
    C(id="x_crm", layer="ext", name="CRM", file="outreach/world.py",
      summary="Stores the final record per prospect. Simulated; swap for HubSpot or its MCP server.",
      inputs=["Outcome writes"], outputs=["Records"], related={}),

    # ---- Shared brain ---------------------------------------------------------
    C(id="state", layer="brain", name="State object", file="outreach/state.py",
      summary="Six fixed fields defined before the run: reply_status, calendar_checked, times_proposed, reply_round, meeting_status, crm_logged. Every node reads and writes it.",
      inputs=["Node updates"], outputs=["The single source of truth for routing"], related={}),
    C(id="retry", layer="brain", name="Retry table & caps", file="outreach/retry.py",
      summary="Decided up front for each tool: how many retries for temporary failures, and what happens after (skip, escalate or abort). Caps: 3 proposal rounds, 40 graph steps.",
      inputs=["Tool errors"], outputs=["retry · skip · escalate · abort"], related={"rule": "check_calendar"}),
    C(id="decider", layer="brain", name="Automatic decider", file="outreach/decider.py",
      summary="Answers the Decide step with fixed rules: book the closest free slot at the prospect's time, re-propose on unclear replies, never book a slot it couldn't check.",
      inputs=["Escalation details"], outputs=["Decision + reason"], related={"rule": "counter_cap"}),
    C(id="checkpointer", layer="brain", name="Checkpointer", file="outreach/sequence2.py",
      summary="LangGraph saves the state after every step (MemorySaver), so a paused run resumes exactly where it stopped.",
      inputs=["State after each node"], outputs=["Resumable runs"], related={}),

    # ---- Delivery -------------------------------------------------------------
    C(id="pipeline", layer="delivery", name="Pipeline", file="outreach/pipeline.py",
      summary="Runs one prospect end to end: ICP gate → Sequence 1 → Sequence 2, records the path and every decision.",
      inputs=["Prospect", "World"], outputs=["Final state, path, decisions"], related={}),
    C(id="cli", layer="delivery", name="Command line", file="outreach/__main__.py",
      summary="python -m outreach runs all scenarios in the terminal; --interactive lets a person make the decisions.",
      inputs=["Scenario name"], outputs=["Printed trace"], related={}),
    C(id="api", layer="delivery", name="Dashboard API", file="outreach/dashboard.py",
      summary="Serves the page and three endpoints (meta, run, custom). Locally a Python server; on Vercel, serverless functions in api/.",
      inputs=["HTTP requests"], outputs=["Runs as JSON"], related={}),
    C(id="page", layer="delivery", name="Dashboard", file="outreach/static/dashboard.html",
      summary="This page: runs, playground, rules and this map. Every view is a shareable link.",
      inputs=["API data"], outputs=["What you're looking at"], related={}),
    C(id="tests", layer="delivery", name="Tests", file="tests/test_workflow.py",
      summary="Every scenario, retry rule, decision rule, tool description and this map are checked automatically.",
      inputs=["The code"], outputs=["Pass / fail"], related={}),
]

# Main flow between components (drawn as the spine of the map).
EDGES = [
    ("prospects", "icp_gate", "records"),
    ("icp_gate", "sequence1", "qualified"),
    ("sequence1", "classifier", "reply text"),
    ("classifier", "n_check_calendar", "interested"),
    ("n_check_calendar", "n_propose_times", "free slots"),
    ("n_propose_times", "n_wait_for_reply", "email sent"),
    ("n_wait_for_reply", "n_check_calendar", "counter / silence"),
    ("n_wait_for_reply", "n_book_meeting", "confirmed"),
    ("n_wait_for_reply", "n_decide", "unclear / cap"),
    ("n_decide", "n_book_meeting", "book"),
    ("n_book_meeting", "n_update_crm", "booked"),
    ("n_check_calendar", "t_check_calendar", "calls"),
    ("n_propose_times", "t_propose_times", "calls"),
    ("n_book_meeting", "t_book_meeting", "calls"),
    ("n_update_crm", "t_update_crm", "calls"),
    ("sequence1", "t_send_email", "calls"),
    ("t_send_email", "x_mail", ""),
    ("t_propose_times", "x_mail", ""),
    ("t_check_calendar", "x_calendar", ""),
    ("t_book_meeting", "x_calendar", ""),
    ("t_update_crm", "x_crm", ""),
    ("n_decide", "decider", "asks"),
]


def components_map() -> dict:
    from .icp import ICP
    return {
        "layers": LAYERS,
        "components": COMPONENTS,
        "edges": [{"from": a, "to": b, "label": lbl} for a, b, lbl in EDGES],
        "icp_variables": [{"name": k, **v} for k, v in ICP["variables"].items()],
        "repo": "https://github.com/sanjanapundir-ai/sprinto-outreach",
    }
