"""Tests for WayNext config module."""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Helpers — inline the config logic so tests don't need GTK
# ---------------------------------------------------------------------------

DEFAULTS = {
    "lookahead_hours": 4,
    "refresh_interval_min": 2,
    "internal_domain": "@example.com",
    "colours": {
        "no_meetings": "#3daee9",
        "internal_only": "#f0a500",
        "has_externals": "#cc3333",
        "not_configured": "#888888",
    },
}


def load_config(path: Path) -> dict:
    """Load config from YAML, filling missing keys with defaults."""
    if not path.exists():
        return dict(DEFAULTS)
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    # Merge top-level defaults
    result = dict(DEFAULTS)
    result.update(data)
    if "colours" in data:
        result["colours"] = {**DEFAULTS["colours"], **data["colours"]}
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg["lookahead_hours"] == 4
    assert cfg["internal_domain"] == "@example.com"
    assert cfg["colours"]["no_meetings"] == "#3daee9"


def test_partial_override(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent("""\
        lookahead_hours: 8
        internal_domain: "@corp.example.com"
    """)
    )
    cfg = load_config(p)
    assert cfg["lookahead_hours"] == 8
    assert cfg["internal_domain"] == "@corp.example.com"
    # Colours should still be defaults
    assert cfg["colours"]["no_meetings"] == "#3daee9"


def test_colour_override(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent("""\
        colours:
          no_meetings: "#0000ff"
    """)
    )
    cfg = load_config(p)
    assert cfg["colours"]["no_meetings"] == "#0000ff"
    # Other colours unaffected
    assert cfg["colours"]["internal_only"] == "#f0a500"


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("")
    cfg = load_config(p)
    assert cfg["lookahead_hours"] == 4


def test_refresh_interval_default(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg["refresh_interval_min"] == 2


def test_refresh_interval_override(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("refresh_interval_min: 5\n")
    cfg = load_config(p)
    assert cfg["refresh_interval_min"] == 5
