"""Tests for WayNext calendar logic (mocked Google API)."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Inline the calendar business logic so tests don't import GTK
# ---------------------------------------------------------------------------

INTERNAL_DOMAIN = "@example.com"

VISIO_PATTERNS = [
    (r"meet\.google\.com", "Meet"),
    (r"zoom\.us", "Zoom"),
    (r"teams\.microsoft\.com", "Teams"),
    (r"webex\.com|whereby\.com|bluejeans\.com", "Video"),
]

URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def classify_attendees(attendees: list[dict], domain: str) -> str:
    """Return 'blue'|'orange'|'red' based on attendee domains."""
    if not attendees:
        return "orange"  # no attendees = treat as internal
    external = [
        a
        for a in attendees
        if not a.get("self")
        and domain not in a.get("email", "")
        and a.get("responseStatus") != "declined"
    ]
    return "red" if external else "orange"


def extract_visio(event: dict) -> tuple[str, str] | None:
    """Return (url, icon) for the first visio link found, or None."""
    # 1. conferenceData entryPoints
    for ep in event.get("conferenceData", {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            url = ep.get("uri", "")
            if url:
                for pattern, icon in VISIO_PATTERNS:
                    if re.search(pattern, url):
                        return url, icon
                return url, "Video"

    # 2. hangoutLink
    if event.get("hangoutLink"):
        url = event["hangoutLink"]
        for pattern, icon in VISIO_PATTERNS:
            if re.search(pattern, url):
                return url, icon
        return url, "Meet"

    # 3 & 4. location then description
    candidates: list[str] = []
    if event.get("location"):
        candidates.append(event["location"])
    if event.get("description"):
        candidates.append(event["description"])

    for text in candidates:
        for url in URL_RE.findall(text):
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
    assert icon == "Meet"


def test_zoom_in_description() -> None:
    event = {"description": "Join here: https://zoom.us/j/123456789"}
    result = extract_visio(event)
    assert result is not None
    url, icon = result
    assert "zoom.us" in url
    assert icon == "Zoom"


def test_teams_in_location() -> None:
    event = {"location": "https://teams.microsoft.com/l/meetup-join/xyz"}
    result = extract_visio(event)
    assert result is not None
    _, icon = result
    assert icon == "Teams"


def test_no_visio_returns_none() -> None:
    event = {"summary": "Quick sync", "description": "No link here"}
    assert extract_visio(event) is None


def test_hangout_takes_priority_over_description() -> None:
    event = {
        "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
        "description": "Backup: https://zoom.us/j/999",
    }
    _, icon = extract_visio(event)
    assert icon == "Meet"  # Meet wins


def test_conference_data_teams_detected() -> None:
    """Teams link in conferenceData.entryPoints should be detected."""
    event = {
        "conferenceData": {
            "entryPoints": [
                {
                    "entryPointType": "video",
                    "uri": "https://teams.microsoft.com/l/meetup-join/xyz",
                }
            ]
        }
    }
    url, icon = extract_visio(event)
    assert url is not None
    assert "teams.microsoft.com" in url
    assert icon == "Teams"


def test_conference_data_takes_priority_over_hangout() -> None:
    """conferenceData should be checked before hangoutLink."""
    event = {
        "conferenceData": {
            "entryPoints": [
                {
                    "entryPointType": "video",
                    "uri": "https://teams.microsoft.com/l/meetup-join/abc",
                }
            ]
        },
        "hangoutLink": "https://meet.google.com/zzz-zzzz-zzz",
    }
    url, _ = extract_visio(event)
    assert url is not None
    assert "teams.microsoft.com" in url  # conferenceData wins


def test_conference_data_non_video_entry_ignored() -> None:
    """Only video entryPoints should be picked up."""
    event = {
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+1234567890"},
                {"entryPointType": "video", "uri": "https://zoom.us/j/99999"},
            ]
        }
    }
    url, _ = extract_visio(event)
    assert url is not None
    assert "zoom.us" in url


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


# ---------------------------------------------------------------------------
# Tests — minutes_label skips past meetings
# ---------------------------------------------------------------------------


def _make_meeting(offset_minutes: int, attendees: list | None = None) -> dict:
    """Helper: create a minimal meeting dict offset_minutes from now."""
    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return {
        "start": start.isoformat(),
        "summary": f"Meeting at {offset_minutes:+d}m",
        "attendees": attendees or [],
        "html_link": "https://calendar.google.com/",
        "id": "test-id",
    }


def test_minutes_label_skips_past() -> None:
    """minutes_label should skip meetings already in progress."""
    meetings = [_make_meeting(-30), _make_meeting(15)]
    # Inline the waynext-status logic
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    nxt = next(
        (m for m in meetings if (datetime.fromisoformat(m["start"]) - now).total_seconds() > 0),
        None,
    )
    assert nxt is not None
    mins = int((datetime.fromisoformat(nxt["start"]) - now).total_seconds() / 60)
    assert 14 <= mins <= 15  # ~15m


def test_minutes_label_empty_when_all_past() -> None:
    """minutes_label should return '' when all meetings are past."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    meetings = [_make_meeting(-60), _make_meeting(-30)]
    nxt = next(
        (m for m in meetings if (datetime.fromisoformat(m["start"]) - now).total_seconds() > 0),
        None,
    )
    assert nxt is None
