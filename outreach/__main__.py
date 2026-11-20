"""CLI.

    python -m outreach                     run every scenario; handoffs decided automatically
    python -m outreach -s counter_busy     run one scenario
    python -m outreach -s counter_cap_handoff --interactive   you are the human at the handoff
    python -m outreach --tools             print the tool descriptions the model sees
    python -m outreach --retry-table       print the retry table
    python -m outreach --mermaid           print the Sequence 2 graph as Mermaid
"""
import argparse
import json
import sys

from .pipeline import run_prospect
from .retry import MAX_REPLY_ROUNDS, RETRY_TABLE
from .scenarios import PROSPECTS, SCENARIOS, make_world
from .sequence2 import build_sequence2
from .state import CORE_FIELDS
from .tools import TOOL_SPECS
from .world import World


def interactive_human(payload: dict) -> dict:
    print("\n  >>> HUMAN HANDOFF  (graph paused; state is checkpointed)")
    print("  " + json.dumps(payload, indent=2).replace("\n", "\n  "))
    while True:
        raw = input('  decision JSON, e.g. {"action":"book","slot":"2026-11-23T15:00"}: ').strip()
        try:
            d = json.loads(raw)
            if d.get("action") in ("book", "propose", "close"):
                return d
        except json.JSONDecodeError:
            pass
        print("  invalid - action must be book | propose | close")


def run_scenario(sc: dict, interactive: bool) -> bool:
    world = make_world(sc)
    human = interactive_human if interactive else "auto"
    final = run_prospect(PROSPECTS[sc["prospect"]], world, human=human)
    ok = final.get("outcome") == sc["expect"]
    print(f"\n=== {sc['name']}  [{sc['prospect']} {PROSPECTS[sc['prospect']]['company']}]  {sc['about']}")
    for e in final.get("events", []):
        print(f"   - {e}")
    core = {k: final.get(k) for k in CORE_FIELDS if k in final}
    if core:
        print(f"   state: {core}")
    print(f"   outcome: {final.get('outcome')}  crm_logged={final.get('crm_logged')}  "
          f"dead_letter={len(world.dead_letter)}  {'PASS' if ok else 'FAIL (expected ' + sc['expect'] + ')'}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="outreach")
    ap.add_argument("-s", "--scenario")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--retry-table", action="store_true")
    ap.add_argument("--mermaid", action="store_true")
    a = ap.parse_args(argv)

    if a.tools:
        for t in TOOL_SPECS:
            print(f"## {t['name']}\n{t['description']}\n")
        return 0
    if a.retry_table:
        print(f"{'tool':<15}{'retries':<9}{'transient exhausted':<21}{'permanent':<11}downstream dependency")
        for p in RETRY_TABLE.values():
            print(f"{p.tool:<15}{p.max_retries:<9}{p.on_transient_exhausted:<21}{p.on_permanent:<11}{p.downstream_dependency}")
        print(f"\nCaps: MAX_REPLY_ROUNDS={MAX_REPLY_ROUNDS}")
        return 0
    if a.mermaid:
        print(build_sequence2(World()).get_graph().draw_mermaid())
        return 0

    chosen = [s for s in SCENARIOS if not a.scenario or s["name"] == a.scenario]
    if not chosen:
        print("unknown scenario; choose from: " + ", ".join(s["name"] for s in SCENARIOS))
        return 2
    results = [run_scenario(s, a.interactive) for s in chosen]
    print(f"\n{sum(results)}/{len(results)} scenarios reached the expected outcome")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
