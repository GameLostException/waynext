#!/usr/bin/env python3
"""
waynext — Wayland GTK4 Google Calendar popup for waybar.

Shows upcoming meetings and colour-codes the waybar icon based on
whether the next meeting has attendees outside a configured domain.

Architecture:
    - GTK main thread: UI updates only, never blocks
    - CalWorker thread: all Google API network calls
    - Communication: worker → UI via GLib.idle_add()
    - IPC: Unix socket server (GLib.io_add_watch)

Subcommands:
    toggle   Show/hide popup (default)
    show     Show popup
    hide     Hide popup
    status   Print waybar JSON to stdout (used by waybar exec)
    reload   Re-read config and re-fetch calendar
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── Layer-shell preload ──────────────────────────────────────────────────────
# gtk4-layer-shell must be preloaded before GTK is imported.

def _find_layer_shell_lib() -> Optional[str]:
    candidates = [
        "/usr/lib/libgtk4-layer-shell.so",
        "/usr/lib64/libgtk4-layer-shell.so",
        "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so",
        "/usr/local/lib/libgtk4-layer-shell.so",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

_LAYER_SHELL_LIB = _find_layer_shell_lib()

if _LAYER_SHELL_LIB and _LAYER_SHELL_LIB not in os.environ.get("LD_PRELOAD", ""):
    os.execve(
        sys.executable,
        [sys.executable] + sys.argv,
        {
            **os.environ,
            "LD_PRELOAD": _LAYER_SHELL_LIB,
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-1"),
            "XDG_RUNTIME_DIR": os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
            ),
        },
    )

# ── GTK imports (after layer-shell preload) ──────────────────────────────────

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Gtk4LayerShell, Pango

import yaml

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [waynext] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("waynext")

# ── Paths & constants ────────────────────────────────────────────────────────

APP_ID        = "io.github.waynext"
CONFIG_DIR    = Path.home() / ".config" / "waynext"
CONFIG_FILE   = CONFIG_DIR / "config.yaml"
SOCKET_PATH   = Path(f"/tmp/waynext-{os.getuid()}.sock")

SCOPES        = ["https://www.googleapis.com/auth/calendar.readonly"]
CACHE_TTL_SEC = 60          # re-fetch at most once per minute
POPUP_WIDTH   = 320
MARGIN_TOP    = 0

# ── OAuth client secret ───────────────────────────────────────────────────────
# Never committed to git — only client_secret.template.json is tracked.
#
# To set up authentication:
#   cp client_secret.template.json ~/.config/waynext/client_secret.json
#   Fill in client_id and client_secret from your Google Cloud project.
#   Then click "Sign in with Google" in the waynext settings panel.
#
# The refresh token is stored in TOKEN_FILE after first auth and reused
# automatically. It grants read-only calendar access and can be revoked
# at any time via https://myaccount.google.com/permissions
#
# See README.md § Authentication for full step-by-step instructions.
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"
TOKEN_FILE         = CONFIG_DIR / "token.json"

# Visio provider patterns → Nerd Font icons
VISIO_PATTERNS: list[tuple[str, str]] = [
    (r"meet\.google\.com",              "Meet"),
    (r"zoom\.us",                       "Zoom"),
    (r"teams\.microsoft\.com",          "Teams"),
    (r"webex\.com|whereby\.com|bluejeans\.com", "Video"),
]
VISIO_URL_RE = re.compile(r"https?://[^\s\"'<>]+")

# ── Default config ────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "lookahead_hours": 4,
    "internal_domain": "@example.com",
    "colours": {
        "no_meetings":    "#3daee9",
        "internal_only":  "#f0a500",
        "has_externals":  "#cc3333",
        "not_configured": "#888888",
    },
}

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Meeting:
    event_id:  str
    summary:   str
    start:     datetime
    end:       datetime
    html_link: str
    attendees: list[dict] = field(default_factory=list)
    visio_url: Optional[str] = None
    visio_icon: str = "Video"


@dataclass
class CalState:
    status:   str = "not_configured"   # not_configured | ok | error
    account:  str = ""
    meetings: list[Meeting] = field(default_factory=list)
    fetched_at: float = 0.0


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.yaml, filling missing keys with defaults."""
    if not CONFIG_FILE.exists():
        return _deep_merge(DEFAULT_CONFIG, {})
    try:
        with CONFIG_FILE.open() as f:
            data = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULT_CONFIG, data)
    except Exception as e:
        log.warning("Failed to load config: %s — using defaults", e)
        return _deep_merge(DEFAULT_CONFIG, {})


def save_config(cfg: dict) -> None:
    """Write config atomically."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".yaml.tmp")
    with tmp.open("w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    tmp.rename(CONFIG_FILE)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ── Calendar business logic ───────────────────────────────────────────────────

def extract_visio(event: dict) -> tuple[Optional[str], str]:
    """Return (url, icon) for first visio link found, or (None, greyed icon)."""
    candidates: list[str] = []
    if event.get("hangoutLink"):
        candidates.append(event["hangoutLink"])
    if event.get("location"):
        candidates.append(event["location"])
    if event.get("description"):
        candidates.append(event["description"])

    for text in candidates:
        for url in VISIO_URL_RE.findall(text):
            for pattern, icon in VISIO_PATTERNS:
                if re.search(pattern, url):
                    return url, icon
    return None, "󰍩"


def is_declined(event: dict) -> bool:
    """Return True if the authenticated user declined this event."""
    for a in event.get("attendees", []):
        if a.get("self") and a.get("responseStatus") == "declined":
            return True
    return False


def classify_next_meeting(meetings: list[Meeting], domain: str) -> str:
    """Return waybar CSS class based on next meeting's attendees."""
    if not meetings:
        return "blue"
    m = meetings[0]
    if not m.attendees:
        return "orange"
    external = [
        a for a in m.attendees
        if domain not in a.get("email", "")
        and not a.get("self")
        and a.get("responseStatus") != "declined"
    ]
    return "red" if external else "orange"


def waybar_json(state: CalState, cfg: dict) -> str:
    """Produce the JSON line waybar reads from exec."""
    if state.status == "not_configured":
        css_class = "grey"
    elif state.status == "error":
        css_class = "grey"
    else:
        css_class = classify_next_meeting(state.meetings, cfg["internal_domain"])
        if css_class == "blue":
            css_class = "blue"     # no meetings
    return json.dumps({"text": "󰃭", "class": css_class, "tooltip": ""}, ensure_ascii=False)


# ── Google Calendar worker ────────────────────────────────────────────────────

class CalWorker:
    """Background thread for Google Calendar API calls."""

    def __init__(self, on_update: callable) -> None:  # type: ignore[type-arg]
        self._on_update = on_update
        self._state = CalState()
        self._lock = threading.Lock()
        self._cfg: dict = load_config()

    def reload_config(self) -> None:
        with self._lock:
            self._cfg = load_config()

    def get_state(self) -> CalState:
        with self._lock:
            return self._state

    def fetch(self) -> None:
        """Fetch events in a background thread and post result to GTK."""
        threading.Thread(target=self._do_fetch, daemon=True).start()

    def authenticate(self, on_done: callable) -> None:  # type: ignore[type-arg]
        """Run OAuth flow in a background thread.

        on_done(success: bool, message: str) is called on the GTK thread when done.
        """
        def _do():
            try:
                _service, account = self._build_service()
                GLib.idle_add(on_done, True, account)
                # Immediately fetch events now that we're authed
                self._do_fetch()
            except FileNotFoundError:
                GLib.idle_add(on_done, False, "client_secret.json not found — see README § Authentication")
            except Exception as e:
                GLib.idle_add(on_done, False, str(e))
        threading.Thread(target=_do, daemon=True).start()

    def fetch_if_stale(self) -> None:
        with self._lock:
            age = time.time() - self._state.fetched_at
        if age > CACHE_TTL_SEC:
            self.fetch()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_fetch(self) -> None:
        try:
            service, account = self._build_service()
        except FileNotFoundError:
            new_state = CalState(status="not_configured")
            self._post(new_state)
            return
        except Exception as e:
            log.error("Auth error: %s", e)
            new_state = CalState(status="error", account="Auth error")
            self._post(new_state)
            return

        with self._lock:
            cfg = dict(self._cfg)

        try:
            meetings = self._fetch_events(service, cfg)
        except Exception as e:
            log.error("Calendar fetch error: %s", e)
            new_state = CalState(status="error", account=account)
            self._post(new_state)
            return

        new_state = CalState(
            status="ok",
            account=account,
            meetings=meetings,
            fetched_at=time.time(),
        )
        self._post(new_state)

    def _build_service(self):  # type: ignore[return]
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds: Optional[Credentials] = None

        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CLIENT_SECRET_FILE.exists():
                    raise FileNotFoundError(
                        f"client_secret.json not found at {CLIENT_SECRET_FILE}\n"
                        f"Copy client_secret.template.json → {CLIENT_SECRET_FILE} "
                        f"and fill in your client_id and client_secret."
                    )
                # Ensure browser-related env vars are set — the daemon may have
                # been started with a stripped environment (nohup, systemd, etc.)
                # without WAYLAND_DISPLAY or DISPLAY, which prevents xdg-open
                # from finding a browser.
                os.environ.setdefault("WAYLAND_DISPLAY", "wayland-1")
                os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
                os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS",
                                      f"unix:path=/run/user/{os.getuid()}/bus")
                flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
                creds = flow.run_local_server(port=0, open_browser=True)

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(creds.to_json())

        service = build("calendar", "v3", credentials=creds)

        # Resolve account email
        try:
            me = service.calendars().get(calendarId="primary").execute()
            account = me.get("id", "")
        except Exception:
            account = ""

        return service, account

    def _fetch_events(self, service, cfg: dict) -> list[Meeting]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=cfg["lookahead_hours"])

        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                fields="items(id,summary,start,end,attendees,hangoutLink,location,description,htmlLink)",
            )
            .execute()
        )

        meetings: list[Meeting] = []
        for ev in result.get("items", []):
            if is_declined(ev):
                continue

            start_raw = ev.get("start", {})
            end_raw   = ev.get("end", {})

            start = _parse_dt(start_raw)
            end   = _parse_dt(end_raw)
            if start is None:
                continue

            visio_url, visio_icon = extract_visio(ev)

            meetings.append(Meeting(
                event_id   = ev.get("id", ""),
                summary    = ev.get("summary", "(No title)"),
                start      = start,
                end        = end or start,
                html_link  = ev.get("htmlLink", ""),
                attendees  = ev.get("attendees", []),
                visio_url  = visio_url,
                visio_icon = visio_icon,
            ))

        return meetings

    def _post(self, state: CalState) -> None:
        with self._lock:
            self._state = state
        # Write state file for the waybar exec subcommand (cmd_status).
        # Runs in the worker thread — write atomically to avoid partial reads.
        try:
            state_file = Path(f"/tmp/waynext-state-{os.getuid()}.json")
            payload = {
                "status":  state.status,
                "account": state.account,
                "meetings": [
                    {
                        "id":        m.event_id,
                        "summary":   m.summary,
                        "start":     m.start.isoformat(),
                        "html_link": m.html_link,
                        "attendees": m.attendees,
                    }
                    for m in state.meetings
                ],
            }
            tmp = state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False))
            tmp.rename(state_file)
        except Exception as e:
            log.warning("Failed to write state file: %s", e)
        GLib.idle_add(self._on_update, state)


def _parse_dt(raw: dict) -> Optional[datetime]:
    """Parse a Calendar API start/end dict to datetime."""
    if "dateTime" in raw:
        dt = datetime.fromisoformat(raw["dateTime"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if "date" in raw:
        # All-day event — treat as midnight UTC
        d = datetime.fromisoformat(raw["date"])
        return d.replace(tzinfo=timezone.utc)
    return None


# ── GTK4 popup window ─────────────────────────────────────────────────────────

CSS = """
.wn-main {
    background-color: @window_bg_color;
    border-radius: 8px;
}
.wn-status-row {
    padding: 8px 12px;
    border-bottom: 1px solid alpha(@borders, 0.5);
}
.wn-status-dot-ok     { color: #57c75a; font-size: 10px; }
.wn-status-dot-error  { color: #cc3333; font-size: 10px; }
.wn-status-dot-none   { color: #888888; font-size: 10px; }
.wn-status-text {
    font-size: 12px;
    color: @dim-label-color;
}
.wn-gear-btn {
    padding: 2px 4px;
    border-radius: 4px;
    min-width: 0;
    min-height: 0;
}
.wn-meeting-row {
    padding: 7px 12px;
    border-bottom: 1px solid alpha(@borders, 0.3);
}
.wn-meeting-row:hover {
    background-color: alpha(@accent_bg_color, 0.12);
}
.wn-meeting-left-btn {
    padding: 0;
    border-radius: 0;
}
.wn-meeting-left-btn:hover {
    background-color: alpha(@accent_bg_color, 0.15);
}
.wn-meeting-time {
    font-size: 12px;
    font-weight: bold;
    color: #81a2be;
    min-width: 42px;
}
.wn-meeting-topic {
    font-size: 12px;
    color: #c5c8c6;
}
.wn-visio-btn {
    padding: 0 4px;
    min-width: 0;
    min-height: 0;
    border-radius: 4px;
    font-size: 11px;
}
.wn-visio-btn-grey { color: alpha(#c5c8c6, 0.3); }
.wn-empty-label {
    padding: 14px 12px;
    font-size: 12px;
    color: @dim-label-color;
    font-style: italic;
}
.wn-settings-panel {
    padding: 10px 12px 6px 12px;
}
.wn-settings-label {
    font-size: 12px;
    color: @dim-label-color;
    min-width: 140px;
}
.wn-settings-btns {
    padding: 6px 12px 10px 12px;
    border-top: 1px solid alpha(@borders, 0.4);
}
.wn-auth-section {
    padding: 8px 12px;
    border-bottom: 1px solid alpha(@borders, 0.4);
}
.wn-auth-status {
    font-size: 11px;
    color: @dim-label-color;
    font-style: italic;
}
.wn-auth-status-ok    { color: #57c75a; font-style: normal; }
.wn-auth-status-error { color: #cc3333; font-style: normal; }
"""


class WayNextWindow(Gtk.Window):
    """GTK4 popup window for waynext."""

    def __init__(self) -> None:
        super().__init__()
        self._cfg: dict = load_config()
        self._settings_open = False
        self._settings_draft: dict = {}

        self._setup_layer_shell()
        self._setup_css()
        self._build_ui()

        self._cal = CalWorker(on_update=self._on_cal_update)
        self._cal.fetch()

    # ── Layer shell ───────────────────────────────────────────────────────────

    def _setup_layer_shell(self) -> None:
        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, MARGIN_TOP)
        Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT,
                                   self._calc_margin_right())
        Gtk4LayerShell.set_keyboard_mode(
            self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
        )
        self.set_default_size(POPUP_WIDTH, -1)

    def _calc_margin_right(self) -> int:
        """Return right margin so the popup sits flush against the right edge of the bar.

        We anchor to the RIGHT edge with a small fixed margin — the calendar
        icon is always near the right end of the bar, so the popup should
        always appear at the top-right of the monitor.
        """
        return 4

    # ── CSS ───────────────────────────────────────────────────────────────────

    def _setup_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.set_decorated(False)
        self.set_resizable(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("wn-main")
        self.set_child(outer)

        # Status row
        outer.append(self._build_status_row())

        # Meeting list (scrollable)
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("wn-meeting-list")
        outer.append(self._list_box)

        # Settings panel (Revealer)
        self._settings_revealer = Gtk.Revealer()
        self._settings_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._settings_revealer.set_transition_duration(150)
        outer.append(self._settings_revealer)

        # Auto-hide on mouse leave
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._cancel_hide())
        motion.connect("leave", lambda *_: self._schedule_hide())
        self.add_controller(motion)

        # Escape to close
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

        self._hide_timer: Optional[int] = None

    def _build_status_row(self) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("wn-status-row")

        self._status_dot = Gtk.Label(label="●")
        self._status_dot.add_css_class("wn-status-dot-none")
        row.append(self._status_dot)

        self._status_label = Gtk.Label(label="Not configured")
        self._status_label.add_css_class("wn-status-text")
        self._status_label.set_hexpand(True)
        self._status_label.set_xalign(0)
        self._status_label.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(self._status_label)

        gear_btn = Gtk.Button(label="⚙")
        gear_btn.add_css_class("wn-gear-btn")
        gear_btn.add_css_class("flat")
        gear_btn.connect("clicked", self._toggle_settings)
        row.append(gear_btn)

        return row

    def _build_meeting_row(self, m: Meeting) -> Gtk.ListBoxRow:
        lbr = Gtk.ListBoxRow()
        lbr.add_css_class("wn-meeting-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        local_time = m.start.astimezone().strftime("%H:%M")

        # ── Meeting row: two buttons side by side ────────────────────────────
        # Left button (time + topic) → opens Google Calendar event
        # Right button (visio label) → opens meeting link
        # Using two separate Buttons avoids all GTK4 gesture propagation issues.

        left_btn = Gtk.Button()
        left_btn.add_css_class("flat")
        left_btn.add_css_class("wn-meeting-left-btn")
        left_btn.set_hexpand(True)
        left_btn.connect("clicked", lambda _b, link=m.html_link: self._open_url(link))

        left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        time_lbl = Gtk.Label(label=local_time)
        time_lbl.add_css_class("wn-meeting-time")
        left_box.append(time_lbl)

        topic_lbl = Gtk.Label(label=m.summary)
        topic_lbl.add_css_class("wn-meeting-topic")
        topic_lbl.set_hexpand(True)
        topic_lbl.set_xalign(0)
        topic_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        left_box.append(topic_lbl)

        left_btn.set_child(left_box)
        box.append(left_btn)

        # Visio button
        visio_btn = Gtk.Button(label=m.visio_icon)
        visio_btn.add_css_class("wn-visio-btn")
        visio_btn.add_css_class("flat")
        if m.visio_url:
            visio_btn.connect("clicked", lambda _b, url=m.visio_url: self._open_url(url))
        else:
            visio_btn.add_css_class("wn-visio-btn-grey")
            visio_btn.set_sensitive(False)
        box.append(visio_btn)

        lbr.set_child(box)
        lbr.set_activatable(False)
        lbr.set_can_focus(False)

        return lbr

        return lbr

    # ── Settings panel ────────────────────────────────────────────────────────

    def _toggle_settings(self, _btn: Gtk.Button) -> None:
        if self._settings_open:
            self._close_settings(save=False)
        else:
            self._open_settings()

    def _open_settings(self) -> None:
        self._settings_open = True
        self._settings_draft = _deep_merge(self._cfg, {})

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # ── Google Account section ────────────────────────────────────────────
        auth_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        auth_box.add_css_class("wn-auth-section")

        auth_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        auth_title = Gtk.Label(label="Google Account")
        auth_title.add_css_class("wn-settings-label")
        auth_title.set_xalign(0)
        auth_title.set_hexpand(True)
        auth_header.append(auth_title)

        self._auth_btn = Gtk.Button(label="Sign in with Google")
        self._auth_btn.add_css_class("suggested-action")
        self._auth_btn.connect("clicked", self._on_auth_click)
        auth_header.append(self._auth_btn)

        auth_box.append(auth_header)

        # Auth status line — shows current account or error
        self._auth_status_lbl = Gtk.Label()
        self._auth_status_lbl.set_xalign(0)
        self._auth_status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._auth_status_lbl.add_css_class("wn-auth-status")
        state = self._cal.get_state()
        if state.status == "ok" and state.account:
            self._auth_status_lbl.set_text(f"Signed in as {state.account}")
            self._auth_status_lbl.remove_css_class("wn-auth-status-error")
            self._auth_status_lbl.add_css_class("wn-auth-status-ok")
            self._auth_btn.set_label("Re-authenticate")
        elif state.status == "error":
            self._auth_status_lbl.set_text(state.account or "Auth error")
            self._auth_status_lbl.remove_css_class("wn-auth-status-ok")
            self._auth_status_lbl.add_css_class("wn-auth-status-error")
        else:
            self._auth_status_lbl.set_text("Not signed in — place client_secret.json in ~/.config/waynext/")
        auth_box.append(self._auth_status_lbl)

        outer.append(auth_box)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("wn-settings-panel")

        def _row(label_text: str, widget: Gtk.Widget) -> Gtk.Box:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=label_text)
            lbl.add_css_class("wn-settings-label")
            lbl.set_xalign(0)
            r.append(lbl)
            r.append(widget)
            return r

        # Lookahead hours
        spin = Gtk.SpinButton()
        spin.set_adjustment(Gtk.Adjustment(
            value=self._cfg["lookahead_hours"],
            lower=1, upper=24, step_increment=1,
        ))
        spin.connect(
            "value-changed",
            lambda w: self._settings_draft.update({"lookahead_hours": int(w.get_value())}),
        )
        panel.append(_row("Lookahead (hours)", spin))

        # Internal domain
        domain_entry = Gtk.Entry()
        domain_entry.set_text(self._cfg["internal_domain"])
        domain_entry.connect(
            "changed",
            lambda w: self._settings_draft.update({"internal_domain": w.get_text()}),
        )
        panel.append(_row("Internal domain", domain_entry))

        # Colour pickers
        colours = self._cfg["colours"]
        colour_fields = [
            ("no_meetings",   "Colour — no meetings"),
            ("internal_only", "Colour — internal only"),
            ("has_externals", "Colour — has externals"),
        ]
        for key, label_text in colour_fields:
            btn = Gtk.ColorButton()
            rgba = Gdk.RGBA()
            rgba.parse(colours[key])
            btn.set_rgba(rgba)
            btn.connect(
                "color-set",
                lambda w, k=key: self._settings_draft["colours"].update(
                    {k: self._rgba_to_hex(w.get_rgba())}
                ),
            )
            panel.append(_row(label_text, btn))

        outer.append(panel)

        # Cancel / Save buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.add_css_class("wn-settings-btns")
        btn_row.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda _: self._close_settings(save=False))
        btn_row.append(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda _: self._close_settings(save=True))
        btn_row.append(save_btn)

        outer.append(btn_row)

        self._settings_revealer.set_child(outer)
        self._settings_revealer.set_reveal_child(True)
        # Hide meeting list while settings are open
        self._list_box.set_visible(False)

    def _on_auth_click(self, _btn: Gtk.Button) -> None:
        """Trigger OAuth flow in background thread."""
        self._auth_btn.set_label("Opening browser…")
        self._auth_btn.set_sensitive(False)
        self._auth_status_lbl.remove_css_class("wn-auth-status-ok")
        self._auth_status_lbl.remove_css_class("wn-auth-status-error")
        self._auth_status_lbl.add_css_class("wn-auth-status")
        self._auth_status_lbl.set_text("Waiting for browser consent…")

        def _on_done(success: bool, message: str) -> None:
            self._auth_btn.set_sensitive(True)
            if success:
                self._auth_btn.set_label("Re-authenticate")
                self._auth_status_lbl.remove_css_class("wn-auth-status-error")
                self._auth_status_lbl.add_css_class("wn-auth-status-ok")
                self._auth_status_lbl.set_text(f"Signed in as {message}")
            else:
                self._auth_btn.set_label("Sign in with Google")
                self._auth_status_lbl.remove_css_class("wn-auth-status-ok")
                self._auth_status_lbl.add_css_class("wn-auth-status-error")
                self._auth_status_lbl.set_text(f"Error: {message}")
            return False  # GLib.idle_add

        self._cal.authenticate(_on_done)

    def _close_settings(self, save: bool) -> None:
        self._settings_open = False
        self._settings_revealer.set_reveal_child(False)
        self._list_box.set_visible(True)

        if save:
            self._cfg = _deep_merge(self._cfg, self._settings_draft)
            save_config(self._cfg)
            self._cal.reload_config()
            self._cal.fetch()

    @staticmethod
    def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
        r = int(rgba.red   * 255)
        g = int(rgba.green * 255)
        b = int(rgba.blue  * 255)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── Calendar update callback (GTK thread) ─────────────────────────────────

    def _on_cal_update(self, state: CalState) -> None:
        self._refresh_status(state)
        self._refresh_meetings(state)
        return False  # GLib.idle_add must return False

    def _refresh_status(self, state: CalState) -> None:
        if state.status == "ok":
            self._status_dot.remove_css_class("wn-status-dot-none")
            self._status_dot.remove_css_class("wn-status-dot-error")
            self._status_dot.add_css_class("wn-status-dot-ok")
            if state.account:
                self._status_label.set_text(f"Connected as {state.account}")
            else:
                self._status_label.set_text("Connected")
        elif state.status == "error":
            self._status_dot.remove_css_class("wn-status-dot-none")
            self._status_dot.remove_css_class("wn-status-dot-ok")
            self._status_dot.add_css_class("wn-status-dot-error")
            self._status_label.set_text("Auth error")
        else:
            self._status_dot.remove_css_class("wn-status-dot-ok")
            self._status_dot.remove_css_class("wn-status-dot-error")
            self._status_dot.add_css_class("wn-status-dot-none")
            self._status_label.set_text("Not configured")

    def _refresh_meetings(self, state: CalState) -> None:
        # Clear existing rows
        while True:
            row = self._list_box.get_first_child()
            if row is None:
                break
            self._list_box.remove(row)

        if state.status != "ok":
            return

        if not state.meetings:
            lbr = Gtk.ListBoxRow()
            lbr.set_activatable(False)
            lbl = Gtk.Label(label="No upcoming meetings")
            lbl.add_css_class("wn-empty-label")
            lbr.set_child(lbl)
            self._list_box.append(lbr)
            return

        for m in state.meetings:
            self._list_box.append(self._build_meeting_row(m))

    # ── Auto-hide ─────────────────────────────────────────────────────────────

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        self._hide_timer = GLib.timeout_add(600, self._do_hide)

    def _cancel_hide(self) -> None:
        if self._hide_timer is not None:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = None

    def _do_hide(self) -> bool:
        self.set_visible(False)
        if self._settings_open:
            self._close_settings(save=False)
        return False

    def _on_key(self, _ctrl, keyval: int, *_args) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._do_hide()
            return True
        return False

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _open_url(self, url: str) -> None:
        log.info("_open_url: %s", url)
        # Gtk.show_uri handles Wayland focus correctly and raises the browser window.
        # Falls back to xdg-open in a detached thread if that fails.
        try:
            Gtk.show_uri(None, url, Gdk.CURRENT_TIME)
            log.info("Gtk.show_uri launched")
        except Exception as e:
            log.warning("Gtk.show_uri failed (%s) — falling back to xdg-open", e)
            env = {**os.environ}
            env.setdefault("WAYLAND_DISPLAY", "wayland-1")
            env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                           f"unix:path=/run/user/{os.getuid()}/bus")
            def _launch() -> None:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, env=env,
                                  start_new_session=True)
            threading.Thread(target=_launch, daemon=True).start()
        self._do_hide()

    def poll(self) -> None:
        """Trigger a calendar refresh (called on show)."""
        self._cal.fetch_if_stale()


# ── IPC server ────────────────────────────────────────────────────────────────

class IPCServer:
    """Unix socket server for waybar / CLI control."""

    def __init__(self, window: WayNextWindow) -> None:
        self._window = window

        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(SOCKET_PATH))
        self._sock.listen(1)
        self._sock.setblocking(False)

        GLib.io_add_watch(self._sock, GLib.IO_IN, self._on_connection)

    def _on_connection(self, _source, _condition) -> bool:
        try:
            conn, _ = self._sock.accept()
            cmd = conn.recv(64).decode().strip()
            conn.close()
            GLib.idle_add(self._handle, cmd)
        except Exception:
            pass
        return True

    def _handle(self, cmd: str) -> None:
        win = self._window
        if cmd == "show":
            self._show()
        elif cmd == "hide":
            win._do_hide()
        elif cmd == "toggle":
            if win.is_visible():
                win._do_hide()
            else:
                self._show()
        elif cmd == "reload":
            win._cfg = load_config()
            win._cal.reload_config()
            win._cal.fetch()

    def _show(self) -> None:
        win = self._window
        win.set_visible(True)
        win.present()
        win.poll()


def send_ipc(cmd: str) -> bool:
    """Send command to a running daemon. Returns True if delivered."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(SOCKET_PATH))
        s.send(cmd.encode())
        s.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False


# ── Status subcommand (non-GTK, for waybar exec) ──────────────────────────────

def cmd_status() -> None:
    """Print waybar JSON and exit. Reads cached state from a temp file."""
    state_file = Path(f"/tmp/waynext-state-{os.getuid()}.json")
    cfg = load_config()

    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            status   = data.get("status", "not_configured")
            meetings_raw = data.get("meetings", [])

            # Reconstruct minimal Meeting objects for classify_next_meeting
            meetings: list[Meeting] = []
            for m in meetings_raw:
                start = datetime.fromisoformat(m["start"])
                meetings.append(Meeting(
                    event_id   = m.get("id", ""),
                    summary    = m.get("summary", ""),
                    start      = start,
                    end        = start,
                    html_link  = m.get("html_link", ""),
                    attendees  = m.get("attendees", []),
                ))

            if status != "ok":
                css_class = "grey"
            else:
                css_class = classify_next_meeting(meetings, cfg["internal_domain"])

            print(json.dumps({"text": "󰃭", "class": css_class, "tooltip": ""}, ensure_ascii=False))
            return
        except Exception:
            pass

    # No state file or error — show grey (not configured / daemon not running)
    print(json.dumps({"text": "󰃭", "class": "grey", "tooltip": ""}, ensure_ascii=False))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"

    if cmd == "status":
        cmd_status()
        return

    # Try running daemon
    if send_ipc(cmd):
        return

    # Start new daemon
    style_mgr_applied = False

    app = Adw.Application(application_id=APP_ID)

    def on_activate(_app: Adw.Application) -> None:
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

        window = WayNextWindow()
        window.set_application(app)
        app.add_window(window)

        IPCServer(window)

        # Always fetch on startup so the state file is written immediately —
        # waybar reads it via `waynext status` and needs it to colour the icon.
        window._cal.fetch()

        if cmd in ("show", "toggle"):
            window.set_visible(True)
            window.present()

    app.connect("activate", on_activate)
    app.run([])


if __name__ == "__main__":
    main()
