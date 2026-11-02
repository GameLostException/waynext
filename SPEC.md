# WayNext — Specification

A Waybar tray widget that shows upcoming Google Calendar meetings and
colour-codes the icon based on attendee composition of the next meeting.

---

## 1. Overview

WayNext is a persistent GTK4 daemon that:

- Polls Google Calendar (via OAuth2) for events starting within the next N hours
- Exposes a coloured icon in the Waybar tray via a `custom/` module
- Shows a popup panel on click with meeting list and inline settings

It follows the same architecture as **wayblue**: a single Python daemon with a
Unix socket IPC, GTK4 + gtk4-layer-shell popup, and a lightweight waybar
`custom/` module that drives icon colour via CSS classes.

---

## 2. Repository

- **Location:** `~/Lab/waynext/`
- **Remote:** `https://github.com/GameLostException/waynext` (public)
- **Branch strategy:** `main` is protected; all work on feature branches
  (`feat/`, `fix/`, `chore/`), merged via PR
- **Security:** no credentials, tokens, domain names, or organisational
  identifiers may appear in source, commits, or CI config

---

## 3. Waybar Integration

### 3.1 Module type

`custom/waynext` — a `custom/` module, not a built-in module.

Waybar calls the `exec` script every `interval` seconds. The script outputs
a single JSON line:

```json
{"text": "󰃭", "class": "blue", "tooltip": ""}
```

The `class` field drives CSS colouring. Possible values: `blue`, `orange`, `red`, `grey`.

### 3.2 Waybar config snippet (main monitor only)

```json
"custom/waynext": {
    "exec": "~/.local/bin/waynext status",
    "return-type": "json",
    "interval": 120,
    "on-click": "~/.local/bin/waynext toggle",
    "tooltip": false
}
```

Add `"custom/waynext"` to the modules list of the main monitor config only.

### 3.3 Waybar CSS

```css
#custom-waynext.blue   { color: <configurable, default #3daee9>; }
#custom-waynext.orange { color: <configurable, default #f0a500>; }
#custom-waynext.red    { color: <configurable, default #cc3333>; }
#custom-waynext.grey   { color: <configurable, default #888888>; }
```

Colours are written to the CSS by the daemon whenever settings change.

---

## 4. Icon Colour Logic

Colour is determined by the **next single upcoming meeting** (first meeting that hasn't started yet) within the lookahead window:

| State | Condition | Default colour |
|---|---|---|
| `blue` | No meetings in the next N hours, **or** not authenticated | #3daee9 |
| `orange` | Next meeting has attendees exclusively within domain X | #f0a500 |
| `red` | Next meeting has at least one attendee outside domain X | #cc3333 |

- N and X are user-configurable (see §7).
- "Not authenticated" maps to `blue` so the icon is non-alarming; the status
  row in the popup makes the auth state explicit.
- Declined meetings are excluded from the list and colour logic.

---

## 5. Popup Panel

Opened by clicking the waybar icon (`waynext toggle`). Dismissed by clicking
outside (mouse-leave auto-hide, same as wayblue) or pressing Escape.

### 5.1 Layout (top to bottom)

```
┌─────────────────────────────────────────┐
│ ● Connected as user@example.com    [⚙]  │  ← status row
├─────────────────────────────────────────┤
│ 14:30  Sync call                   [󰍩] │  ← meeting row
│ 15:00  Product review              [󰍩] │
│ 16:30  External partner call       [󰍩] │
└─────────────────────────────────────────┘
```

#### Status row

- Left: a status indicator dot + short text:
  - `● Connected as user@example.com` (green dot)
  - `● Not configured` (grey dot)
  - `● Auth error` (red dot)
  - `● No upcoming meetings` (blue dot, when auth OK but list empty)
- Right: gear icon button `[⚙]` — toggles the settings panel inline
  (replaces the meeting list, same popup window)

#### Meeting rows

Each row contains:

| Element | Detail |
|---|---|
| Time | `HH:MM` in the user's local timezone |
| Topic | Event summary, truncated with ellipsis if too long |
| Visio icon | Nerd Font icon linking to the visio URL; greyed out and non-clickable if no link found; clicking opens URL in default browser |

Clicking anywhere on a meeting row (not the visio icon) opens the Google
Calendar event page in the default browser.

Visio link detection order:
1. `hangoutLink` field (Google Meet)
2. `location` field — scan for `https://` URLs containing known patterns:
   `meet.google.com`, `zoom.us`, `teams.microsoft.com`, `webex.com`,
   `whereby.com`, `bluejeans.com`
3. `description` field — same URL scan

First match wins. Icon used per provider:

| Provider | Icon |
|---|---|
| Google Meet | `󰊭` |
| Zoom | `󰖙` |
| Teams | `󰊻` |
| Webex / other | `󰍩` (generic video) |

### 5.2 Settings panel (inline, replaces meeting list)

Revealed when gear icon is clicked. The status row remains visible.
Two buttons at the bottom: **Cancel** (discards changes) and **Save**
(persists to `~/.config/waynext/config.yaml` and refreshes the daemon).

Settings fields:

| Label | Type | Default |
|---|---|---|
| Lookahead window (hours) | Integer spinner | 4 |
| Internal domain | Text entry | `@example.com` |
| Colour — no meetings | Colour picker | #3daee9 |
| Colour — internal only | Colour picker | #f0a500 |
| Colour — has externals | Colour picker | #cc3333 |

---

## 6. Google Calendar Integration

### 6.1 OAuth2 flow

- Credentials file: `~/.config/waynext/credentials.json`
  (downloaded from Google Cloud Console, **never committed to git**)
- Token file: `~/.config/waynext/token.json`
  (written after first auth, **never committed to git**)
- Scopes: `https://www.googleapis.com/auth/calendar.readonly`
- First run with no token triggers a browser-based OAuth2 consent flow
- Token is refreshed automatically using the refresh token

### 6.2 Calendar query

- Calendar: `primary`
- Time range: `now` → `now + N hours`
- Fields fetched per event: `id`, `summary`, `start`, `end`, `attendees`,
  `hangoutLink`, `location`, `description`, `htmlLink`
- Results sorted by start time ascending
- Declined events (`attendee.self == true && attendee.responseStatus == "declined"`)
  are excluded

### 6.3 Caching

Events are cached in memory. The `status` subcommand (called by waybar every
120s) uses the cache if the last fetch was less than 60s ago, otherwise
re-fetches. The `toggle` command always uses the current cache (no blocking
network call on click).

---

## 7. Configuration

File: `~/.config/waynext/config.yaml`

```yaml
# WayNext configuration
lookahead_hours: 4
internal_domain: "@example.com"

colours:
  no_meetings: "#3daee9"
  internal_only: "#f0a500"
  has_externals: "#cc3333"
  not_configured: "#888888"
```

File is created with defaults on first run if absent. Written atomically
(write to `.tmp`, then rename) to prevent corruption.

---

## 8. IPC

Socket: `/tmp/waynext-<uid>.sock`

Commands (sent as plain strings):

| Command | Effect |
|---|---|
| `toggle` | Show popup if hidden, hide if visible |
| `show` | Show popup |
| `hide` | Hide popup |
| `status` | Print waybar JSON to stdout and exit (used by `exec` script) |
| `reload` | Re-read config and re-fetch calendar |

If no daemon is running, the launcher starts one and then processes the command.

---

## 9. Architecture

```
waynext (bash launcher)
    │
    ├── sends IPC command to socket → existing daemon handles it
    │
    └── starts daemon if not running:
            WayNextApp (Adw.Application)
                ├── WayNextWindow (GTK4 + gtk4-layer-shell popup)
                │       ├── StatusRow
                │       ├── MeetingList  ─── MeetingRow × N
                │       └── SettingsPanel (Revealer, inline)
                ├── CalWorker (thread)
                │       ├── Google Calendar API polling
                │       └── posts results to GTK thread via GLib.idle_add()
                └── IPCServer (Unix socket, GLib.io_add_watch)
```

Threading rules (same as wayblue):
- GTK main thread: UI updates only, never blocks
- CalWorker thread: all network/API calls
- Bridge: `GLib.idle_add()` from worker → GTK thread

---

## 10. File Layout

```
waynext/
├── waynext              # bash launcher
├── waynext.py           # main application
├── requirements.txt
├── Makefile
├── SPEC.md
├── README.md
├── .github/
│   └── workflows/
│       └── ci.yaml      # lint + test
├── tests/
│   ├── test_calendar.py
│   ├── test_config.py
│   └── test_ipc.py
└── .gitignore
```

### .gitignore (mandatory entries)

```
__pycache__/
*.pyc
*.pyo
.venv/
# Credentials — never commit
credentials.json
token.json
~/.config/waynext/
```

---

## 11. CI/CD (GitHub Actions)

Trigger: push and pull_request on all branches.

Pipeline (`.github/workflows/ci.yaml`):

1. **Lint** — `ruff check .` + `ruff format --check .`
2. **Type check** — `mypy waynext.py` (strict mode)
3. **Tests** — `pytest tests/ -v --tb=short`

No build artefacts or deployment steps at this stage.

Python version matrix: 3.11, 3.12.

All CI steps run without GTK display (tests mock GTK and the Google API client).

---

## 12. Security Checklist

Before every commit and before any push:

- [ ] No credentials files (`credentials.json`, `token.json`) staged
- [ ] No hardcoded domain names, email addresses, or org-specific strings
- [ ] No tokens or secrets in environment variables in CI config
- [ ] `.gitignore` covers all credential paths
- [ ] OAuth scopes are read-only (`calendar.readonly`)
