"""ICP parameters: variables, where they come from, valid answers, short-circuits.

The prospect records in data/prospects.json are the *cleaned* output of the data
gathering step (APIs / data providers first, scraping last). This module decides
which of them are worth an email at all - a disqualified prospect never enters
Sequence 1, which saves sends, tokens and sender reputation.
"""
from typing import Tuple

ICP = {
    "variables": {
        "industry": {
            "source": "Apollo company record; fallback: homepage /about (scrape)",
            "target_pages": ["/", "/about"],
            "valid": ["B2B SaaS", "Fintech", "Healthtech", "HR Tech"],
        },
        "employee_count": {
            "source": "Apollo / LinkedIn company page",
            "valid_range": [20, 1000],
        },
        "compliance_status": {
            "source": "Trust center or security page (scrape, browser-based for JS pages)",
            "target_pages": ["/security", "/trust", "/compliance"],
            "valid": ["none", "in_progress", "certified"],
        },
        "sells_to_enterprise": {
            "source": "Customer logos / pricing page",
            "target_pages": ["/customers", "/pricing"],
            "valid": [True, False],
        },
        "contact_role": {
            "source": "Apollo / Hunter contact lookup",
            "valid": ["CTO", "VP Engineering", "Head of Security", "CISO", "Founder", "CEO", "COO"],
        },
    },
    # Evaluated in order; the first rule that fires stops the prospect.
    "short_circuit": [
        ("missing_email", "No verified email address"),
        ("too_small", "Fewer than 20 employees - no compliance budget yet"),
        ("too_large", "More than 1000 employees - enterprise motion, not SDR outreach"),
        ("wrong_industry", "Industry outside ICP"),
        ("already_on_competitor", "Already certified on a competing automation platform"),
        ("wrong_role", "Contact is not a buyer for compliance tooling"),
    ],
}


def qualify(p: dict) -> Tuple[bool, str]:
    v = ICP["variables"]
    checks = {
        "missing_email": not p.get("email"),
        "too_small": p.get("employee_count", 0) < v["employee_count"]["valid_range"][0],
        "too_large": p.get("employee_count", 0) > v["employee_count"]["valid_range"][1],
        "wrong_industry": p.get("industry") not in v["industry"]["valid"],
        "already_on_competitor": p.get("compliance_status") == "certified" and bool(p.get("competitor_tool")),
        "wrong_role": p.get("contact_role") not in v["contact_role"]["valid"],
    }
    for rule, reason in ICP["short_circuit"]:
        if checks[rule]:
            return False, f"{rule}: {reason}"
    return True, "qualified"
