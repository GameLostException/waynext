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

## Authentication

waynext uses Google OAuth2 with an "installed app" client secret. The flow is:

1. You place a `client_secret.json` in `~/.config/waynext/`
2. Click **Sign in with Google** in the settings panel (⚙ icon)
3. A browser window opens for Google's consent screen — approve it
4. waynext saves a refresh token to `~/.config/waynext/token.json`
5. Done — token is reused automatically and silently refreshed forever

**`client_secret.json` is never committed to git** — only `client_secret.template.json`
is tracked, as a structural reference.

### Option A — Reuse an existing Google OAuth client (recommended)

If you have another app on this machine with a registered OAuth client
(e.g. Syncopath), copy its `client_secret.json`:

```bash
cp ~/.config/syncopath/client_secret.json ~/.config/waynext/client_secret.json
```

Then click **Sign in with Google**. You may be asked to grant Calendar access
in addition to any existing permissions.

### Option B — Create your own Google Cloud client

1. Go to https://console.cloud.google.com/
2. Create a project → **APIs & Services → Enable APIs** → enable **Google Calendar API**
3. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "waynext")
4. Download the JSON → save as `~/.config/waynext/client_secret.json`
5. Click **Sign in with Google** in the settings panel

### Security notes

- `client_secret.json` and `token.json` are in `.gitignore` — they are never committed
- OAuth scope is **read-only** (`calendar.readonly`) — waynext cannot modify your calendar
- The refresh token grants read access to your calendar and can be revoked at any time:
  https://myaccount.google.com/permissions
- The client_secret identifies the *application*, not your account — see SPEC.md § Security



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

### 2. Install

```bash
git clone https://github.com/GameLostException/waynext.git ~/Lab/waynext
cd ~/Lab/waynext
make install
```

### 3. Set up authentication

See **§ Authentication** above — either copy an existing `client_secret.json` or
create one from Google Cloud Console. Then click **Sign in with Google** in the
settings panel (⚙ icon in the popup).


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

- `client_secret.json` and `token.json` are in `.gitignore` and never committed
- Only `client_secret.template.json` (empty placeholder) is tracked in git
- OAuth scope is read-only: `calendar.readonly`
- No credentials or domain-specific configuration in source code
- Revoke access at any time: https://myaccount.google.com/permissions
