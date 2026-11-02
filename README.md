# waynext

A Waybar tray widget that connects to Google Calendar and shows your upcoming
meetings, colour-coded by attendee composition.

## Features

- **Coloured icon** in the Waybar tray:
  - Grey — not configured or no data yet
  - Blue — no meetings within the lookahead window
  - Orange — next meeting has attendees within your organisation only
  - Red — next meeting has at least one external attendee
- **Countdown** — minutes until the next meeting shown next to the icon
- **Popup meeting list** — click the icon to see upcoming meetings
  - Time, title, and conference link per row
  - Click a row → opens the Google Calendar event in your browser
  - Click the link icon → opens the video call (Google Meet, Zoom, Teams, …)
  - Click the copy icon → copies the conference URL to the clipboard
- **Inline settings panel** — configure lookahead window, domain, refresh
  interval, and colours without leaving the popup
- **Help button** — renders this README in your browser

## Requirements

- Wayland compositor (tested on Hyprland)
- [Waybar](https://github.com/Alexays/Waybar)
- [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell)
- Python 3.11+
- A Google account with Calendar access

## Installation

### 1. Install dependencies

**Arch Linux:**
```bash
sudo pacman -S gtk4 libadwaita gtk4-layer-shell python-gobject python-yaml
pip install --user google-api-python-client google-auth-oauthlib google-auth-httplib2
```

**Debian / Ubuntu:**
```bash
sudo apt install libgtk-4-dev libadwaita-1-dev libgtk4-layer-shell-dev \
    python3-gi python3-yaml
pip install --user google-api-python-client google-auth-oauthlib google-auth-httplib2
```

### 2. Install waynext

```bash
git clone https://github.com/GameLostException/waynext.git
cd waynext
make install
```

This installs `waynext` and `waynext-status` to `~/.local/bin/`.

### 3. Set up Google Calendar access

See **§ Authentication** below.

## Authentication

waynext uses Google OAuth2. You need an OAuth 2.0 client credentials file
(`client_secret.json`) from Google Cloud Console. The file is placed locally
on your machine and never shared.

### Step 1 — Create OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable the **Google Calendar API**: *APIs & Services → Enable APIs & Services*
4. Create credentials: *APIs & Services → Credentials → Create Credentials →
   OAuth 2.0 Client ID*
   - Application type: **Desktop app**
   - Name: anything (e.g. "waynext")
5. Download the JSON file

### Step 2 — Place the credentials file

```bash
mkdir -p ~/.config/waynext
cp ~/Downloads/client_secret_*.json ~/.config/waynext/client_secret.json
```

### Step 3 — Sign in

Open the waynext popup (click the icon in Waybar), open the settings panel
(⚙), and click **Sign in with Google**. A browser window opens for Google's
consent screen. After approving, a refresh token is saved to
`~/.config/waynext/token.json` and reused automatically from then on.

### Security

- `client_secret.json` and `token.json` are listed in `.gitignore` — they stay
  on your machine and are never committed to version control
- OAuth scope is **read-only** (`calendar.readonly`) — waynext cannot create,
  modify, or delete calendar events
- Access can be revoked at any time at
  https://myaccount.google.com/permissions

## Waybar configuration

waynext uses two executables:

| Executable | Role |
|---|---|
| `waynext` | GTK4 daemon — handles the popup and calendar polling |
| `waynext-status` | Lightweight status script — called by Waybar every 30 s |

Add to your Waybar config:

```json
"custom/waynext": {
    "exec": "waynext-status",
    "return-type": "json",
    "interval": 30,
    "on-click": "waynext toggle",
    "tooltip": false
}
```

Add to your Waybar CSS:

```css
#custom-waynext        { padding: 0 6px; }
#custom-waynext.blue   { color: #3daee9; }
#custom-waynext.orange { color: #f0a500; }
#custom-waynext.red    { color: #cc3333; }
#custom-waynext.grey   { color: #888888; }
```

The colours match the defaults and can be changed in the settings panel.

## Autostart

The `waynext` daemon must be running for the popup and polling to work.
`waynext-status` reads a local state file and does not require the daemon,
but the icon will turn grey if the daemon has not run recently.

### Hyprland

Add to `~/.config/hypr/hyprland.conf`:

```ini
exec-once = ~/.local/bin/waynext toggle
```

`exec-once` is the idiomatic Hyprland way to start a background process at
compositor launch. Using `toggle` opens the popup on first start; replace with
`show` or omit the argument to start silently.

> **Note:** Hyprland does not process XDG autostart `.desktop` files natively,
> so `exec-once` is the correct approach.

### systemd user service

For automatic restart on crash, create
`~/.config/systemd/user/waynext.service`:

```ini
[Unit]
Description=waynext — Google Calendar tray daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=%h/.local/bin/waynext toggle
Restart=on-failure
RestartSec=3
Environment=WAYLAND_DISPLAY=wayland-1
Environment=XDG_RUNTIME_DIR=/run/user/%U
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=graphical-session.target
```

Enable it:

```bash
systemctl --user enable --now waynext.service
```

## Configuration

`~/.config/waynext/config.yaml` is created with defaults on first run:

```yaml
lookahead_hours: 4          # how far ahead to look for meetings
refresh_interval_min: 2     # how often to poll Google Calendar (minutes)
internal_domain: "@example.com"  # attendees at this domain are "internal"

colours:
  no_meetings: "#3daee9"
  internal_only: "#f0a500"
  has_externals: "#cc3333"
  not_configured: "#888888"
```

All settings are editable via the ⚙ icon in the popup — no need to edit the
file directly.

## Commands

```
waynext toggle    Show/hide popup (default)
waynext show      Show popup
waynext hide      Hide popup
waynext reload    Re-read config and re-fetch calendar immediately
waynext restart   Kill daemon and restart
```

`waynext-status` is called by Waybar and is not intended for direct use.
