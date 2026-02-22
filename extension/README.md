# Claude Bridge for Zen Browser

Lets Claude Desktop (claude.ai) control Zen Browser through a local HTTP bridge. Like Chrome MCP, but for Zen.

## Architecture

```
Claude Desktop (claude.ai)
    │
    │ Windows MCP Shell → PowerShell/curl
    │
    ▼
Bridge Server (Python, localhost:8765)
    │
    │ WebSocket (ws://localhost:8766)
    │
    ▼
Zen Browser Extension
    │
    │ browser.tabs.sendMessage()
    │
    ▼
Content Script (DOM interaction)
```

Claude sends HTTP requests → Bridge relays via WebSocket → Extension executes in browser → Results flow back.

## Setup

### 1. Install Python dependency
```bash
pip install websockets
```

### 2. Load extension in Zen Browser
1. Open `about:debugging#/runtime/this-firefox`
2. Click **"Load Temporary Add-on..."**
3. Select `manifest.json` from this folder

### 3. Start the bridge server
```bash
python native/bridge.py
```
Or double-click `start-bridge.bat`

### 4. Verify connection
The bridge terminal should show:
```
✅ Zen Browser extension connected
```

## API Endpoints

Claude Desktop calls these via `Invoke-RestMethod` or `curl`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Connection status |
| GET | `/api/screenshot` | Capture visible page (saves PNG) |
| GET | `/api/page-info` | URL, title, dimensions |
| GET | `/api/page-text` | Extract page text content |
| GET | `/api/tabs` | List all open tabs |
| GET | `/api/dom` | Accessibility tree (interactive elements) |
| GET | `/api/forms` | All form fields with labels |
| POST | `/api/navigate` | `{"url": "..."}` |
| POST | `/api/click` | `{"selector": "..."}` or `{"coords": {"x":0,"y":0}}` |
| POST | `/api/type` | `{"selector": "...", "text": "...", "clear": true}` |
| POST | `/api/scroll` | `{"direction": "down", "amount": 500}` |
| POST | `/api/hover` | `{"selector": "..."}` |
| POST | `/api/fill` | `{"selector": "...", "value": "..."}` |
| POST | `/api/find` | `{"query": "search button"}` |
| POST | `/api/js` | `{"code": "document.title"}` |
| POST | `/api/highlight` | `{"selector": "..."}` |

## How Claude Desktop Uses It

Claude uses Windows MCP Shell to send commands:

```powershell
# Check status
Invoke-RestMethod http://localhost:8765/api/status

# Take screenshot
Invoke-RestMethod http://localhost:8765/api/screenshot

# Navigate
Invoke-RestMethod http://localhost:8765/api/navigate -Method Post -Body '{"url":"https://example.com"}' -ContentType "application/json"

# Click a button
Invoke-RestMethod http://localhost:8765/api/click -Method Post -Body '{"selector":"#submit-btn"}' -ContentType "application/json"

# Type into a field
Invoke-RestMethod http://localhost:8765/api/type -Method Post -Body '{"selector":"input[name=email]","text":"test@example.com","clear":true}' -ContentType "application/json"

# Get page text for summarization
Invoke-RestMethod http://localhost:8765/api/page-text

# Find elements by description
Invoke-RestMethod http://localhost:8765/api/find -Method Post -Body '{"query":"login button"}' -ContentType "application/json"
```

### PowerShell Helper (optional)
Load the helper for shorter commands:
```powershell
. .\native\zen-bridge.ps1

Zen-Status
Zen-Navigate "https://example.com"
Zen-Click "#login-btn"
Zen-Type "input[name=email]" "hello@example.com" -clear
Zen-Find "search bar"
Zen-Screenshot
```

## Element Selectors

You can target elements by:
- **CSS selector**: `#id`, `.class`, `input[name=email]`, `button.submit`
- **Ref ID**: `r0`, `r5`, `r23` — returned by `/api/dom` and `/api/find`
- **Coordinates**: `{"coords": {"x": 100, "y": 200}}` — for click/hover

## Screenshots

Screenshots are saved to `~/claude-zen-screenshots/` as PNG files. The API returns the filepath so Claude can reference or view them.

## File Structure
```
claude-bridge-for-zen/
├── manifest.json           # Zen/Firefox extension manifest
├── background/
│   └── background.js       # WebSocket client + tab/screenshot management
├── content/
│   └── content.js          # DOM interaction (click, type, find, etc.)
├── native/
│   ├── bridge.py           # HTTP + WebSocket bridge server
│   └── zen-bridge.ps1      # PowerShell helper functions
├── icons/
│   ├── icon-48.png
│   └── icon-96.png
├── setup.bat               # One-time setup
├── start-bridge.bat        # Launch bridge server
└── README.md
```

## Limitations

- Extension must be reloaded after Zen restarts (temporary add-on)
- Screenshots capture visible viewport only
- Some pages block content script injection (browser internal pages, etc.)
- Bridge server must be running for communication to work
