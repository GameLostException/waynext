"""
pytest configuration for waynext tests.

waynext.py re-execs itself with LD_PRELOAD=libgtk4-layer-shell.so when
imported without that preload set (to ensure GTK initialises correctly).
During tests there is no display and no need for layer-shell, so we
patch os.execve before import to suppress the re-exec.
"""
from __future__ import annotations

from unittest.mock import patch

# Suppress the LD_PRELOAD re-exec so the module can be imported in a
# headless test environment without triggering a process restart.
with patch("os.execve"):
    import waynext  # noqa: F401 — ensure module is loaded once with patch active
