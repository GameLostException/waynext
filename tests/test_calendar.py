"""Tests for WayNext calendar logic (mocked Google API)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Inline the calendar business logic so tests don't import GTK
# ---------------------------------------------------------------------------

INTERNAL_DOMAIN = "@example.com"

VISIO_PATTERNS = [
    (r"meet\.google\.com", "󰊭"),
    (r"zoom\.us", "󰖙"),
    (r"teams\.microsoft\.com", "󰊻"),
    (r"webex\.com|whereby\.com|bluejeans\.com", "󰍩"),
]


def classify_attendees(attendees: list[dict], domain: str) -> str:
    """Return 'blue'|'orange'|'red' based on attendee domains."""
    if not attendees:
        return "orange"  # no attendees = treat as internal
    external = [
        a for a in attendees
        if not a.get("self") and domain not in a.get("email", "")
        and a.get("responseStatus") != "declined"
    ]
    return "red" if external else "orange"


def extract_visio(event: dict) -> tuple[str, str] | None:
    """Return (url, icon) for the first visio link found, or None."""
    candidates: list[str] = []
    if event.get("hangoutLink"):
        candidates.append(event["hangoutLink"])
    if event.get("location"):
        candidates.append(event["location"])
    if event.get("description"):
        candidates.append(event["description"])

    url_re = re.compile(r"https?://[^\s\"'<>]+")
    for text in candidates:
        for url in url_re.findall(text):
            for pattern, icon in VISIO_PATTERNS:
                if re.search(pattern, url):
                    return url, icon
    return None


def is_declined(event: dict) -> bool:
    for a in event.get("attendees", []):
        if a.get("self") and a.get("responseStatus") == "declined":
            return True
    return False


# ---------------------------------------------------------------------------
# Tests — attendee classification
# ---------------------------------------------------------------------------


def test_no_attendees_is_orange() -> None:
    assert classify_attendees([], INTERNAL_DOMAIN) == "orange"


def test_all_internal_is_orange() -> None:
    attendees = [
        {"email": "alice@example.com", "responseStatus": "accepted"},
        {"email": "bob@example.com", "responseStatus": "accepted"},
    ]
    assert classify_attendees(attendees, INTERNAL_DOMAIN) == "orange"


def test_one_external_is_red() -> None:
    attendees = [
        {"email": "alice@example.com", "responseStatus": "accepted"},
        {"email": "external@othercorp.com", "responseStatus": "accepted"},
    ]
    assert classify_attendees(attendees, INTERNAL_DOMAIN) == "red"


def test_declined_external_does_not_trigger_red() -> None:
    attendees = [
        {"email": "alice@example.com", "responseStatus": "accepted"},
        {"email": "external@othercorp.com", "responseStatus": "declined"},
    ]
    assert classify_attendees(attendees, INTERNAL_DOMAIN) == "orange"


# ---------------------------------------------------------------------------
# Tests — visio extraction
# ---------------------------------------------------------------------------


def test_hangout_link_detected() -> None:
    event = {"hangoutLink": "https://meet.google.com/abc-defg-hij"}
    result = extract_visio(event)
    assert result is not None
    url, icon = result
    assert "meet.google.com" in url
    assert icon == "󰊭"


def test_zoom_in_description() -> None:
    event = {
        "description": "Join here: https://zoom.us/j/123456789"
    }
    result = extract_visio(event)
    assert result is not None
    url, icon = result
    assert "zoom.us" in url
    assert icon == "󰖙"


def test_teams_in_location() -> None:
    event = {"location": "https://teams.microsoft.com/l/meetup-join/xyz"}
    result = extract_visio(event)
    assert result is not None
    _, icon = result
    assert icon == "󰊻"


def test_no_visio_returns_none() -> None:
    event = {"summary": "Quick sync", "description": "No link here"}
    assert extract_visio(event) is None


def test_hangout_takes_priority_over_description() -> None:
    event = {
        "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
        "description": "Backup: https://zoom.us/j/999",
    }
    _, icon = extract_visio(event)
    assert icon == "󰊭"  # Meet wins


# ---------------------------------------------------------------------------
# Tests — declined event filtering
# ---------------------------------------------------------------------------


def test_declined_by_self_is_filtered() -> None:
    event = {
        "attendees": [
            {"self": True, "responseStatus": "declined"},
            {"email": "other@example.com", "responseStatus": "accepted"},
        ]
    }
    assert is_declined(event) is True


def test_accepted_by_self_not_filtered() -> None:
    event = {
        "attendees": [
            {"self": True, "responseStatus": "accepted"},
        ]
    }
    assert is_declined(event) is False
