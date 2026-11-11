"""
Shared utilities for graph nodes.
"""

import os
from datetime import datetime

import httpx

# Government API is mounted on the same server as the Custom LLM proxy
# (agent/server.py), so nodes reach it over localhost.
GOV_API_BASE = "http://localhost:8080"

# Must match api/security.py — kept as a literal so nodes don't import the API package.
INTERNAL_KEY_HEADER = "X-Internal-Key"


def gov_api_request(method: str, path: str, *, timeout: float = 5.0, **kwargs) -> httpx.Response:
    """Call the government API with the internal key attached.

    Every node that talks to the government API goes through here, so the
    X-Internal-Key header is set in one place. The key is read per call so a
    missing key shows up as a 403 (logged by the caller) instead of a crash.
    Raises httpx.RequestError on network failure, same as httpx itself.
    """
    headers = {INTERNAL_KEY_HEADER: os.getenv("INTERNAL_API_KEY", "")}
    return httpx.request(method, f"{GOV_API_BASE}{path}", headers=headers, timeout=timeout, **kwargs)


def format_date_spoken(date_str: str) -> str:
    """Convert YYYY-MM-DD to spoken format: 'April sixteenth, twenty twenty-six'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{dt.strftime('%B')} {day}{suffix}"
    except (ValueError, TypeError):
        return date_str


def format_time_spoken(time_str: str) -> str:
    """Convert HH:MM to spoken format: '8 AM', '2 PM'."""
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        hour = dt.hour
        if hour == 0:
            return "twelve AM"
        elif hour < 12:
            return f"{hour} AM"
        elif hour == 12:
            return "twelve PM"
        else:
            return f"{hour - 12} PM"
    except (ValueError, TypeError):
        return time_str


def get_honorific(profile: dict, language: str = "en") -> str:
    """Build honorific name from citizen profile.

    Returns "Mr. John" / "Ms. Sarah" for English,
    "Ahmet Bey" / "Fatma Hanim" for Turkish.
    """
    first_name = profile.get("first_name", "")
    gender = profile.get("gender", "M")

    if language == "en":
        title = "Mr." if gender == "M" else "Ms."
        return f"{title} {first_name}"
    else:
        suffix = "Bey" if gender == "M" else "Hanim"
        return f"{first_name} {suffix}"


def mark_completed(state: dict, intent: str) -> list:
    """Add intent to completed list if not already there."""
    completed = list(state.get("completed_intents", []))
    if intent not in completed:
        completed.append(intent)
    return completed
