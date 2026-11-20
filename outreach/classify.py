"""Reply classification.

Rule-based so the project runs offline and the tests are deterministic. The
function signatures are the contract: replace either body with an LLM call
(structured output constrained to the same labels) without touching the graph.
"""
import re
from typing import List, Optional, Tuple

NEGATIVE = ("not interested", "no thanks", "no thank you", "unsubscribe", "remove me",
            "not a fit", "not a priority", "stop emailing", "please don't")
POSITIVE = ("interested", "sounds good", "let's talk", "lets talk", "happy to chat",
            "tell me more", "set up a call", "book a call", "sure", "yes", "keen", "open to")
DECLINE_LATER = ("never mind", "nevermind", "no longer", "changed our mind", "going another way",
                 "cancel")
ANY_SLOT = ("any of them", "any works", "either works", "whichever", "all work")
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})")
OPTION_RE = re.compile(r"option\s*#?\s*(\d)", re.I)


def classify_initial_reply(text: Optional[str]) -> str:
    """Sequence 1. Returns no_reply | interested | not_interested | unclear."""
    if text is None or not text.strip():
        return "no_reply"
    t = text.lower()
    if any(k in t for k in NEGATIVE):
        return "not_interested"
    if any(k in t for k in POSITIVE):
        return "interested"
    return "unclear"


def classify_scheduling_reply(text: Optional[str], proposed: List[str]) -> Tuple[str, Optional[str]]:
    """Sequence 2. Returns (intent, slot).

    intent: confirm (slot = accepted proposal) | counter (slot = their time) |
            silence | declined | unclear
    """
    if text is None or not text.strip():
        return "silence", None
    t = text.lower()
    if any(k in t for k in DECLINE_LATER):
        return "declined", None

    m = ISO_RE.search(text)
    if m:
        slot = f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}"
        return ("confirm", slot) if slot in proposed else ("counter", slot)

    m = OPTION_RE.search(text)
    if m and 1 <= int(m.group(1)) <= len(proposed):
        return "confirm", proposed[int(m.group(1)) - 1]
    for word, n in ORDINALS.items():
        if re.search(rf"\b{word}\b", t) and n <= len(proposed):
            return "confirm", proposed[n - 1]
    if proposed and any(k in t for k in ANY_SLOT):
        return "confirm", proposed[0]
    return "unclear", None
