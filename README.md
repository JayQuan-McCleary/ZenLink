# ZenLink

Browser automation bridge for Zen Browser & Firefox — control your browser through a simple HTTP API.

Built as a fast, working alternative to Chrome MCP. Works with any AI assistant, automation tool, or script that can make HTTP requests.

## Why ZenLink?

AI desktop assistants (Claude, ChatGPT, Copilot, etc.) often need to interact with your browser — filling forms, reading pages, taking screenshots, navigating sites. Most solutions are either Chrome-only, buggy, or require complex setup.

ZenLink is a lightweight HTTP bridge that gives **anything that can call `curl` or `fetch`** full control over Zen Browser or Firefox. No special SDK, no proprietary protocol — just REST endpoints on localhost.

## How It Works

```
Any AI Assistant / Script / Tool
    |
    |  HTTP request (e.g. POST http://localhost:8765/api/click)
    v
ZenLink Bridge Server (Python, localhost:8765)
    |
    |  WebSocket relay (ws://localhost:8766)
    v
ZenLink Browser Extension
    |
    |  browser.tabs API + content scripts
    v
Web Page DOM — click, type, read, screenshot, etc.
```

**The key idea:** Your AI assistant has shell access (terminal, PowerShell, bash). It sends HTTP requests to ZenLink's local server. The server relays commands to the browser extension over WebSocket. The extension executes them in the actual browser tab and returns results.

### Example: How an AI assistant fills a login form

1. **You say:** "Log into my account on example.com"
2. **AI runs:** `curl -X POST localhost:8765/api/navigate -d '{"url":"https://example.com/login"}'`
3. **AI runs:** `curl -X POST localhost:8765/api/fill -d '{"selector":"#email","value":"me@example.com"}'`
4. **AI runs:** `curl -X POST localhost:8765/api/fill -d '{"selector":"#password","value":"secret"}'`
5. **AI runs:** `curl -X POST localhost:8765/api/click -d '{"selector":"#submit"}'`
6. **Browser:** Navigates, fills fields, clicks submit — you're logged in.

Or with the **batch endpoint** (one request, multiple commands):

```json
POST http://localhost:8765/api/batch
{
  "commands": [
    {"action": "navigate", "url": "https://example.com/login"},
    {"action": "sleep", "ms": 2000},
    {"action": "fill", "selector": "#email", "value": "me@example.com"},
    {"action": "fill", "selector": "#password", "value": "secret"},
    {"action": "click", "selector": "#submit"}
  ]
}
```

## Who Can Use This?

| Tool | How it calls ZenLink |
|------|---------------------|
| **Claude Desktop** (via MCP Shell) | `Invoke-RestMethod http://localhost:8765/api/...` |
| **ChatGPT** (via Code Interpreter / Actions) | HTTP requests to localhost |
| **Any AI with terminal access** | `curl`, `wget`, `requests`, `fetch` |
| **Python scripts** | `requests.post("http://localhost:8765/api/click", json={...})` |
| **Node.js** | `fetch("http://localhost:8765/api/...")` |
| **Bash scripts** | `curl -s localhost:8765/api/page-text` |
| **PowerShell** | `Invoke-RestMethod localhost:8765/api/status` |

If it can make HTTP requests, it can control your browser.

## Quick Start

### 1. Install Python dependency
```bash
pip install websockets
```

### 2. Load extension in Zen Browser (or Firefox)
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
# {"status": "running", "extension_connected": true}
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
POST http://localhost:8765/api/batch
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

Available batch actions: `navigate`, `newTab`, `closeTab`, `switchTab`, `click`, `type`, `fill`, `scroll`, `hover`, `find`, `js`, `pageInfo`, `pageText`, `screenshot`, `tabs`, `forms`, `dom`, `sleep`

### Element Targeting

Multiple ways to target elements:
- **CSS selector**: `#id`, `.class`, `input[name=email]`
- **Ref ID**: `r0`, `r5` — returned by `/api/find` and `/api/dom`
- **Coordinates**: `{"coords": {"x": 100, "y": 200}}`

## Usage Examples

### Python
```python
import requests

# Navigate and fill a form
requests.post("http://localhost:8765/api/navigate", json={"url": "https://example.com"})
requests.post("http://localhost:8765/api/fill", json={"selector": "#search", "value": "hello"})
requests.post("http://localhost:8765/api/click", json={"selector": "#submit"})

# Read page content
text = requests.get("http://localhost:8765/api/page-text").json()
```

### PowerShell (Claude Desktop / MCP)
```powershell
Invoke-RestMethod http://localhost:8765/api/navigate -Method Post -Body '{"url":"https://example.com"}' -ContentType "application/json"
Invoke-RestMethod http://localhost:8765/api/page-text
```

### curl
```bash
curl -X POST http://localhost:8765/api/navigate -H "Content-Type: application/json" -d '{"url":"https://example.com"}'
curl http://localhost:8765/api/page-text
```

### JavaScript / Node.js
```javascript
await fetch("http://localhost:8765/api/navigate", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({url: "https://example.com"})
});
const {title} = await (await fetch("http://localhost:8765/api/page-info")).json();
```

## Features

- **Full page control** — navigate, click, type, scroll, hover, fill forms
- **Smart element finding** — natural language queries, CSS selectors, coordinates, or ref IDs
- **Tab management** — open, close, switch, list tabs
- **Screenshots** — capture viewport as PNG
- **JavaScript execution** — run arbitrary JS in page context
- **Batch commands** — multiple commands in one request for speed
- **Shadow DOM support** — auto-pierces open shadow roots for modern web components
- **Auto-reconnect** — extension reconnects with exponential backoff after bridge restart
- **Content script versioning** — updated scripts auto-inject without page refresh

## Known Limitations

- Extension loads as temporary add-on (needs reload after browser restart)
- `about:` and browser internal pages can't be controlled
- `/api/type` doesn't support contentEditable elements (use `/api/js` instead)
- Closed shadow DOM forms need `/api/js` for value setting
- No authentication on localhost endpoints (intended for local use only)

## Security Note

ZenLink exposes full browser control over localhost with no auth. This is designed for personal, local use. If you need to expose it over a network, add token-based authentication first.

## Origin

Built out of frustration with Chrome MCP being broken. Originally created to give Claude Desktop browser automation capabilities through Zen Browser, but works with any tool that can make HTTP requests.

## License

MIT — do whatever you want with it.