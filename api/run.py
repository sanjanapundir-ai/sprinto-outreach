"""Vercel serverless function for /api/run. Logic lives in outreach/dashboard.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.dashboard import Handler  # noqa: E402


class handler(Handler):  # Vercel looks for a class named `handler`
    pass
