# ZenLink

Browser automation bridge for Zen Browser (and Firefox). Lets AI assistants and scripts control your browser through a simple HTTP API.

Built as an alternative to Chrome MCP — works with Claude Desktop, any MCP-compatible tool, or plain curl/PowerShell.

## Features

- **Full page control** — navigate, click, type, scroll, hover, fill forms
- **Smart element finding** — natural language queries, CSS selectors, coordinates, or ref IDs
- **Tab management** — open, close, switch, list tabs
- **Screenshots** — capture viewport as PNG
- **JavaScript execution** — run arbitrary JS in page context
- **Batch commands** — send multiple commands in one request for speed
- **Shadow DOM support** — auto-pierces shadow roots for modern web components
- **Auto-reconnect** — extension reconnects to bridge automatically with exponential backoff
- **Content script versioning** — updated scripts auto-inject without page refresh

## Architecture

```
Your Tool / Claude Desktop / curl
    ¦
    ¦  HTTP (localhost:8765)
    ?
Bridge Server (Python)
    ¦
    ¦  WebSocket (localhost:8766)
    ?
Zen Browser Extension
    ¦
    ¦  browser.tabs API + content scripts
    ?
Web Page DOM
```

## Quick Start

### 1. Install Python dependency
```bash
pip install websockets
```

### 2. Load extension in Zen Browser
1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on...**
3. Select `manifest.json` from this folder

### 3. Start the bridge
```bash
python native/bridge.py
```
Or double-click `start-bridge.bat`

### 4. Verify
```bash
curl http://localhost:8765/api/status
# {"status": "running", "extension_connected": true, ...}
```

## API Reference

### Read Operations (GET)

| Endpoint | Description |
|----------|-------------|
| `/api/status` | Bridge + extension connection status |
| `/api/tabs` | List all open tabs |
| `/api/page-info` | URL, title, dimensions, scroll position |
| `/api/page-text` | Extract readable text from page |
| `/api/dom` | Accessibility tree |
| `/api/forms` | All form fields with labels and values |
| `/api/screenshot` | Capture viewport (saves PNG, returns path) |

### Action Operations (POST)

| Endpoint | Body | Description |
|----------|------|-------------|
| `/api/navigate` | `{"url": "..."}` | Load URL in active tab |
| `/api/new-tab` | `{"url": "..."}` | Open URL in new tab |
| `/api/close-tab` | `{"tabId": 123}` | Close tab by ID |
| `/api/switch-tab` | `{"tabId": 123}` | Focus a tab |
| `/api/click` | `{"selector": "..."}` or `{"coords": {"x":0,"y":0}}` | Click element |
| `/api/type` | `{"selector": "...", "text": "...", "clear": true}` | Type into input |
| `/api/fill` | `{"selector": "...", "value": "..."}` | Set form field value |
| `/api/scroll` | `{"direction": "down", "amount": 500}` | Scroll page |
| `/api/hover` | `{"selector": "..."}` | Hover over element |
| `/api/find` | `{"query": "login button"}` | Find elements by description |
| `/api/js` | `{"code": "document.title"}` | Execute JavaScript |
| `/api/highlight` | `{"selector": "..."}` | Visual overlay on element |
| `/api/batch` | `{"commands": [...]}` | Run multiple commands at once |

### Batch Commands

Send multiple commands in a single request — significantly faster than individual calls:

```json
{
  "commands": [
    {"action": "navigate", "url": "https://example.com"},
    {"action": "sleep", "ms": 2000},
    {"action": "fill", "selector": "#email", "value": "test@example.com"},
    {"action": "fill", "selector": "#password", "value": "secret"},
    {"action": "click", "selector": "#submit"},
    {"action": "pageInfo"}
  ]
}
```

Batch actions: `navigate`, `newTab`, `closeTab`, `switchTab`, `click`, `type`, `fill`, `scroll`, `hover`, `find`, `js`, `pageInfo`, `pageText`, `screenshot`, `tabs`, `forms`, `dom`, `sleep`

### Element Targeting

Multiple ways to target elements:
- **CSS selector**: `#id`, `.class`, `input[name=email]`
- **Ref ID**: `r0`, `r5` — returned by `/api/find` and `/api/dom`
- **Coordinates**: `{"coords": {"x": 100, "y": 200}}`

## Usage with Claude Desktop

Via Windows MCP Shell:
```powershell
Invoke-RestMethod http://localhost:8765/api/status
Invoke-RestMethod http://localhost:8765/api/navigate -Method Post -Body '{"url":"https://example.com"}' -ContentType "application/json"
```

Via PowerShell helper:
```powershell
. .\native\zen-bridge.ps1
Zen-Navigate "https://example.com"
Zen-Click "#login-btn"
Zen-Find "search bar"
```

Also works with curl, Python requests, or any HTTP client.

## Known Limitations

- Extension is loaded as temporary add-on (reloads needed after Zen restarts)
- `about:` and browser internal pages can't be controlled
- `/api/type` doesn't support contentEditable elements (use `/api/js` instead)
- Shadow DOM forms need `/api/js` fallback for value setting
- No authentication on localhost endpoints (intended for local use only)

## Security Note

The bridge exposes full browser control over localhost with no auth. This is fine for personal/local use. If you need to expose it on a network, add a shared secret or token first.

## License

MIT — do whatever you want with it.