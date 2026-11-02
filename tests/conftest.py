"""
pytest configuration for waynext tests.

waynext.py re-execs itself with LD_PRELOAD=libgtk4-layer-shell.so when
imported without that preload set (to ensure GTK initialises correctly).
During tests there is no display and no need for layer-shell, so we
patch os.execve before import to suppress the re-exec.

PyGObject (gi) is not available in headless CI environments. We mock the
entire gi namespace before importing waynext so the GTK import block is
satisfied without a real display.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch


def _make_gi_mock() -> None:
    """Inject stub gi.* modules so waynext can be imported without PyGObject."""
    if "gi" in sys.modules:
        return  # already available (local dev with PyGObject installed)

    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = MagicMock()  # type: ignore[attr-defined]
    sys.modules["gi"] = gi_mod

    # gi.repository sub-modules used by waynext
    repo = types.ModuleType("gi.repository")
    for name in ("Gtk", "Gdk", "GLib", "Adw", "Gtk4LayerShell", "Pango"):
        mod = MagicMock()
        setattr(repo, name, mod)
        sys.modules[f"gi.repository.{name}"] = mod
    sys.modules["gi.repository"] = repo

    # yaml is a real dependency but may not be installed in minimal CI
    if "yaml" not in sys.modules:
        try:
            import yaml  # noqa: F401
        except ImportError:
            sys.modules["yaml"] = MagicMock()  # type: ignore[assignment]


_make_gi_mock()

# Suppress the LD_PRELOAD re-exec so the module can be imported in a
# headless test environment without triggering a process restart.
with patch("os.execve"):
    import waynext  # noqa: F401 — ensure module is loaded once with patch active
