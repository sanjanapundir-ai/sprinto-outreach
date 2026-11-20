"""Local dashboard for the outreach agent.

    .venv/bin/python -m outreach.dashboard            # http://localhost:8765
    .venv/bin/python -m outreach.dashboard --port 9000

Standard library only. The page (outreach/static/dashboard.html) calls:
    GET  /api/meta            scenarios, prospects, retry table, tools, decision policy
    POST /api/run             {"scenario": "<name>" | "all"}   -> list of runs
    POST /api/custom          {"prospect_id", "replies": [str|null], "failures": {tool: [kind]},
                               "busy": [iso], "bounce": bool}   -> one run
Every run executes the real agent (ICP gate, Sequence 1, LangGraph Sequence 2,
retry table, automatic decider) against a fresh in-memory World.
"""
import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .decider import DECISION_POLICY
from .icp import ICP
from .pipeline import run_prospect
from .retry import GRAPH_RECURSION_LIMIT, MAX_REPLY_ROUNDS, RETRY_TABLE
from .scenarios import BASE_BUSY, PROSPECTS, SCENARIOS, make_world
from .state import CORE_FIELDS
from .tools import TOOL_SPECS
from .world import World

PAGE = Path(__file__).parent / "static" / "dashboard.html"
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]


def execute(prospect: dict, world: World, name: str, about: str = "", expect: str = None) -> dict:
    final = run_prospect(prospect, world)
    pid = prospect["id"]
    return {
        "name": name,
        "about": about,
        "expect": expect,
        "passed": None if expect is None else final.get("outcome") == expect,
        "prospect": prospect,
        "outcome": final.get("outcome"),
        "crm_logged": final.get("crm_logged"),
        "core": {k: final.get(k) for k in CORE_FIELDS},
        "path": final.get("path", []),
        "decisions": final.get("decisions", []),
        "events": final.get("events", []),
        "booking": final.get("booking"),
        "outbox": [m for m in world.outbox if m["to"] == prospect.get("email")],
        "calendar_events": world.calendar_events,
        "crm": world.crm.get(pid),
        "dead_letter": world.dead_letter,
        "tool_calls": world.tool_calls,
    }


def run_scenarios(which: str) -> list:
    chosen = [s for s in SCENARIOS if which in ("all", s["name"])]
    if not chosen:
        raise ValueError(f"unknown scenario {which!r}")
    return [execute(PROSPECTS[s["prospect"]], make_world(s), s["name"], s["about"], s["expect"]) for s in chosen]


def run_custom(body: dict) -> dict:
    pid = body.get("prospect_id")
    if pid not in PROSPECTS:
        raise ValueError(f"unknown prospect {pid!r}")
    prospect = PROSPECTS[pid]
    replies = [None if r is None or not str(r).strip() else str(r) for r in body.get("replies", [])]
    failures = {}
    for tool, kinds in (body.get("failures") or {}).items():
        if tool not in TOOL_NAMES:
            raise ValueError(f"unknown tool {tool!r}")
        if any(k not in ("transient", "permanent") for k in kinds):
            raise ValueError("failure kinds must be 'transient' or 'permanent'")
        failures[tool] = list(kinds)[:10]
    world = World(
        busy=set(BASE_BUSY) | set(body.get("busy") or []),
        bounces={prospect["email"]} if body.get("bounce") else set(),
        replies={pid: replies[:12]},
        failures=failures,
    )
    return execute(prospect, world, name="custom run", about="Your own replies and failures")


def meta() -> dict:
    return {
        "scenarios": [{**{k: s[k] for k in ("name", "prospect", "about", "expect")},
                       "company": PROSPECTS[s["prospect"]]["company"]} for s in SCENARIOS],
        "prospects": list(PROSPECTS.values()),
        "core_fields": list(CORE_FIELDS),
        "retry_table": [p.__dict__ for p in RETRY_TABLE.values()],
        "caps": {"max_reply_rounds": MAX_REPLY_ROUNDS, "recursion_limit": GRAPH_RECURSION_LIMIT},
        "tools": TOOL_SPECS,
        "decision_policy": DECISION_POLICY,
        "icp_rules": [{"rule": r, "reason": why} for r, why in ICP["short_circuit"]],
        "base_busy": sorted(BASE_BUSY),
        "today": "2026-11-17T09:00",
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    @property
    def route(self) -> str:
        """Request path without query string or trailing slash (Vercel may add either)."""
        return urlparse(self.path).path.rstrip("/") or "/"

    def do_GET(self):
        if self.route in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.route == "/api/meta":
            self._json(200, meta())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.route == "/api/run":
                self._json(200, run_scenarios(body.get("scenario", "all")))
            elif self.route == "/api/custom":
                self._json(200, run_custom(body))
            else:
                self._json(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError) as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="outreach.dashboard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    url = f"http://localhost:{a.port}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError as exc:
        if exc.errno in (48, 98):  # address already in use (macOS, Linux)
            print(f"Port {a.port} is already in use - the dashboard may already be running at {url}.\n"
                  f"Open that address, stop the other copy (Ctrl-C in its terminal), or pick another port:\n"
                  f"  .venv/bin/python -m outreach.dashboard --port {a.port + 1}")
            raise SystemExit(1)
        raise
    print(f"Outreach agent dashboard running at {url}  (Ctrl-C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
