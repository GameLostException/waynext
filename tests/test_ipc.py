"""Tests for WayNext IPC logic."""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Inline minimal IPC send/receive logic for testing
# ---------------------------------------------------------------------------

def _run_server(sock_path: Path, received: list, stop: threading.Event) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(str(sock_path))
    srv.listen(1)
    srv.settimeout(2.0)
    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                data = conn.recv(64).decode().strip()
                received.append(data)
                conn.close()
            except socket.timeout:
                continue
    finally:
        srv.close()
        sock_path.unlink(missing_ok=True)


def send_command(sock_path: Path, cmd: str) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(sock_path))
        s.send(cmd.encode())
        s.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_send_and_receive(tmp_path: Path) -> None:
    sock_path = tmp_path / "waynext-test.sock"
    received: list[str] = []
    stop = threading.Event()

    t = threading.Thread(target=_run_server, args=(sock_path, received, stop), daemon=True)
    t.start()
    time.sleep(0.1)  # let server bind

    assert send_command(sock_path, "toggle") is True
    time.sleep(0.1)
    assert received == ["toggle"]

    stop.set()
    t.join(timeout=3)


def test_known_commands(tmp_path: Path) -> None:
    sock_path = tmp_path / "waynext-test2.sock"
    received: list[str] = []
    stop = threading.Event()

    t = threading.Thread(target=_run_server, args=(sock_path, received, stop), daemon=True)
    t.start()
    time.sleep(0.1)

    for cmd in ("toggle", "show", "hide", "reload"):
        send_command(sock_path, cmd)
        time.sleep(0.05)

    stop.set()
    t.join(timeout=3)
    assert received == ["toggle", "show", "hide", "reload"]


def test_send_to_missing_socket_returns_false(tmp_path: Path) -> None:
    sock_path = tmp_path / "nonexistent.sock"
    assert send_command(sock_path, "toggle") is False
