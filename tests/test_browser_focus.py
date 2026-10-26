"""
Tests for browser focus helpers (_browser_addrs_before, _wait_for_browser_activation).

These functions are module-level so they can be tested without a GTK display.
The socket watcher is tested via a local Unix socket server that emits
synthetic Hyprland events.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from waynext import _browser_addrs_before, _wait_for_browser_activation


# ── _browser_addrs_before ────────────────────────────────────────────────────


def _make_clients(entries: list[dict]) -> str:
    return json.dumps(entries)


def test_browser_addrs_strips_0x_prefix():
    clients = _make_clients([
        {"address": "0xdeadbeef", "class": "google-chrome"},
        {"address": "0x12345678", "class": "google-chrome"},
    ])
    with patch("waynext.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=clients)
        result = _browser_addrs_before("google-chrome")
    assert result == {"deadbeef", "12345678"}


def test_browser_addrs_filters_by_class():
    clients = _make_clients([
        {"address": "0xaaaa0001", "class": "google-chrome"},
        {"address": "0xaaaa0002", "class": "kitty"},
        {"address": "0xaaaa0003", "class": "Google-chrome"},  # case-insensitive
    ])
    with patch("waynext.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=clients)
        result = _browser_addrs_before("google-chrome")
    assert result == {"aaaa0001", "aaaa0003"}


def test_browser_addrs_returns_empty_on_error():
    with patch("waynext.subprocess.run", side_effect=Exception("fail")):
        result = _browser_addrs_before("google-chrome")
    assert result == set()


def test_browser_addrs_empty_class_matches_all():
    """Empty browser_class matches everything — the guard is in _launch, not here."""
    clients = _make_clients([
        {"address": "0xaaaa0001", "class": "google-chrome"},
        {"address": "0xaaaa0002", "class": "kitty"},
    ])
    with patch("waynext.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=clients)
        result = _browser_addrs_before("")
    # Empty string matches all classes — both addresses returned
    assert result == {"aaaa0001", "aaaa0002"}


# ── _wait_for_browser_activation ─────────────────────────────────────────────


def _fake_socket_server(sock_path: str, events: list[str], delay_s: float = 0.05):
    """Serve synthetic Hyprland events over a Unix socket, then close."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(2.0)

    def _serve():
        try:
            conn, _ = srv.accept()
            time.sleep(delay_s)
            for ev in events:
                conn.sendall((ev + "\n").encode())
            time.sleep(0.05)
            conn.close()
        except Exception:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return t


def test_urgent_event_returns_address():
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, ["urgent>>abcd1234"])
        known = {"abcd1234"}
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=2.0)
    assert result == "0xabcd1234"


def test_windowtitle_event_fallback():
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, ["windowtitle>>beef0001"])
        known = {"beef0001"}
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=2.0)
    assert result == "0xbeef0001"


def test_openwindow_new_window():
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, ["openwindow>>cafe1234,9,google-chrome,New Tab"])
        known: set[str] = set()  # no prior windows
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=2.0)
    assert result == "0xcafe1234"


def test_urgent_from_unrelated_window_ignored():
    """urgent from a non-browser address should not trigger focus."""
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, ["urgent>>deadbeef"])
        known = {"abcd1234"}  # different address — unrelated window
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=0.5)
    assert result == ""


def test_timeout_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, [], delay_s=0.5)  # no events
        known = {"abcd1234"}
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=0.2)
    assert result == ""


def test_no_socket_returns_empty():
    with patch("waynext._hypr_socket2_path", return_value=None):
        result = _wait_for_browser_activation("google-chrome", {"abcd"}, timeout_s=1.0)
    assert result == ""


def test_openwindow_wrong_class_ignored():
    """openwindow for a different app class should not trigger focus."""
    with tempfile.TemporaryDirectory() as d:
        sock_path = os.path.join(d, "test.sock")
        _fake_socket_server(sock_path, ["openwindow>>1111aaaa,1,kitty,Terminal"])
        known: set[str] = set()
        with patch("waynext._hypr_socket2_path", return_value=sock_path):
            result = _wait_for_browser_activation("google-chrome", known, timeout_s=0.5)
    assert result == ""
