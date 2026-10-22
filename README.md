# waynext

A Waybar tray widget that shows upcoming Google Calendar meetings and
colour-codes the icon based on attendee composition of the next meeting.

## Features

- Blue icon: no meetings in the next N hours (or not yet configured)
- Orange icon: next meeting has attendees within your domain only
- Red icon: next meeting has at least one external attendee
- Click icon → popup with meeting list
- Each meeting row shows time, topic, and a visio link icon (Google Meet, Zoom, Teams…)
- Click meeting row → opens Google Calendar event in browser
- Click visio icon → opens the video call link directly
- Inline settings panel: configure lookahead window, domain, and colours

## Installation

### 1. Dependencies

**Arch:**
```bash
sudo pacman -S gtk4 libadwaita gtk4-layer-shell python-gobject python-yaml
pip install --user google-api-python-client google-auth-oauthlib google-auth-httplib2
```

**Debian/Ubuntu:**
```bash
sudo apt install libgtk-4-dev libadwaita-1-dev libgtk4-layer-shell-dev \
    python3-gi python3-yaml
pip install --user google-api-python-client google-auth-oauthlib google-auth-httplib2
```

### 2. Google Cloud credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable the **Google Calendar API**
3. Create OAuth 2.0 credentials (Desktop application)
4. Download `credentials.json` and place it at `~/.config/waynext/credentials.json`

### 3. Install

```bash
git clone https://github.com/GameLostException/waynext.git ~/Lab/waynext
cd ~/Lab/waynext
make install
```

### 4. First run (OAuth consent)

```bash
waynext toggle
```

A browser window will open for Google Calendar OAuth consent. After approving,
the token is stored at `~/.config/waynext/token.json` and refreshed automatically.

## Waybar configuration

Add to your waybar config (main monitor only):

```json
"custom/waynext": {
    "exec": "waynext status",
    "return-type": "json",
    "interval": 120,
    "on-click": "waynext toggle",
    "tooltip": false
}
```

Add to your waybar CSS:

```css
#custom-waynext.blue   { color: #3daee9; }
#custom-waynext.orange { color: #f0a500; }
#custom-waynext.red    { color: #cc3333; }
#custom-waynext.grey   { color: #888888; }
```

## Configuration

`~/.config/waynext/config.yaml` (created with defaults on first run):

```yaml
lookahead_hours: 4
internal_domain: "@example.com"

colours:
  no_meetings: "#3daee9"
  internal_only: "#f0a500"
  has_externals: "#cc3333"
  not_configured: "#888888"
```

All settings are also editable via the ⚙ gear icon in the popup.

## Usage

```
waynext toggle    Show/hide popup (default)
waynext show      Show popup
waynext hide      Hide popup
waynext status    Print waybar JSON to stdout
waynext reload    Re-read config and re-fetch calendar
waynext restart   Kill daemon and restart
```

## Security

- `credentials.json` and `token.json` are in `.gitignore` and never committed
- OAuth scope is read-only: `calendar.readonly`
- No credentials or domain-specific configuration in source code
