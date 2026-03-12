"""
Claude Bridge Server for Zen Browser
Runs on localhost:8765 — Claude Desktop sends commands via HTTP, 
extension connects via WebSocket to receive and execute them.

Usage: python bridge.py

------------------------------------------------------------------------
AI AGENT NOTICE -- READ BEFORE WRITING SCRAPING BATCH SEQUENCES
------------------------------------------------------------------------
Before writing any batch commands that scrape a known website, check:
    docs/scraping-playbooks/README.md   (relative to repo root)
Playbooks exist for sites that required non-obvious workarounds:
  * amazon.md       -- bot detection / scroll-before-extract pattern
  * bestbuy.md      -- lazy loading / window.scrollTo fix / 2026 selectors
  * newegg.md       -- silent category redirects / use /p/pl?d= search URL
  * bhphotovideo.md -- CSS Modules hashed classes / wildcard selector pattern
Using a playbook avoids re-discovering the same failures from scratch.
If you solve a new site, add a playbook so future agents benefit.
------------------------------------------------------------------------
"""

import asyncio
import json
import base64
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from datetime import datetime

# Try to import websockets, install if missing
try:
    import websockets
    from websockets.server import serve as ws_serve
except ImportError:
    print("Installing websockets...")
    os.system(f"{sys.executable} -m pip install websockets --quiet")
    import websockets
    from websockets.server import serve as ws_serve

# ── Config ──
HTTP_PORT = 8765
WS_PORT = 8766
SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "claude-zen-screenshots")

# ── State ──
extension_ws = None  # WebSocket connection to extension
pending_commands = {}  # id -> asyncio.Future
command_counter = 0


# ══════════════════════════════════════════════
#  WebSocket Server (Extension connects here)
# ══════════════════════════════════════════════

async def ws_handler(websocket):
    global extension_ws
    extension_ws = websocket
    print(f"[{now()}] ✅ Zen Browser extension connected")
    
    try:
        async for message in websocket:
            data = json.loads(message)
            cmd_id = data.get("id")
            
            # Heartbeat ping - just ignore
            if data.get("type") == "ping":
                continue
            
            if cmd_id and cmd_id in pending_commands:
                pending_commands[cmd_id].set_result(data.get("result", {}))
            else:
                print(f"[{now()}] ← Extension: {json.dumps(data)[:200]}")
    except websockets.exceptions.ConnectionClosed:
        print(f"[{now()}] ⚠️ Extension disconnected")
    finally:
        extension_ws = None


async def send_to_extension(action, params=None, timeout=30):
    """Send command to extension and wait for response."""
    global command_counter
    
    if not extension_ws:
        return {"error": "Zen Browser extension not connected. Open Zen Browser and ensure the extension is loaded."}
    
    command_counter += 1
    cmd_id = f"cmd_{command_counter}_{int(time.time())}"
    
    command = {
        "id": cmd_id,
        "action": action,
        **(params or {})
    }
    
    future = asyncio.get_event_loop().create_future()
    pending_commands[cmd_id] = future
    
    try:
        await extension_ws.send(json.dumps(command))
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        pending_commands.pop(cmd_id, None)


# ══════════════════════════════════════════════
#  HTTP API Server (Claude Desktop calls this)
# ══════════════════════════════════════════════


# Action name -> extension action name (for simple forwarding)
BATCH_ACTION_MAP = {
    'navigate':       'navigate',
    'newTab':         'newTab',
    'closeTab':       'closeTab',
    'switchTab':      'switchTab',
    'click':          'click',
    'type':           'type',
    'fill':           'fill',
    'scroll':         'scroll',
    'hover':          'hover',
    'find':           'find',
    'js':             'executeJS',
    'pageInfo':       'getPageInfo',
    'pageText':       'getPageText',
    'pageTextByTabId':'getPageTextFromTab',
    'tabs':           'getTabs',
    'forms':          'getFormFields',
    'dom':            'getAccessibilityTree',
    'highlight':      'highlight',
}


async def run_command(cmd):
    """Execute a single batch command asynchronously."""
    action = cmd.get('action', '')
    params = {k: v for k, v in cmd.items() if k != 'action'}
    try:
        # Simple forwarding actions (the majority)
        if action in BATCH_ACTION_MAP:
            return await send_to_extension(BATCH_ACTION_MAP[action], params)

        # Actions with custom logic
        if action == 'screenshot':
            r = await send_to_extension('screenshot', params)
            if 'dataUrl' in r:
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                img = base64.b64decode(r['dataUrl'].split(',')[1])
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                fp = os.path.join(SCREENSHOT_DIR, f'zen_{ts}.png')
                with open(fp, 'wb') as f2: f2.write(img)
                return {'saved_to': fp}
            return r
        elif action == 'waitForElement':
            timeout_ms = params.get('timeout', 10000)
            timeout_s = (timeout_ms / 1000) + 5
            return await send_to_extension('waitForElement', params, timeout=timeout_s)
        elif action == 'waitForResult':
            timeout_ms = params.get('timeout', 15000)
            timeout_s = (timeout_ms / 1000) + 5
            return await send_to_extension('waitForResult', params, timeout=timeout_s)
        elif action == 'sleep':
            await asyncio.sleep(params.get('ms', 1000) / 1000)
            return {'ok': True}
        else:
            return {'error': f'Unknown batch action: {action}'}
    except Exception as e:
        return {'error': str(e)}


class BridgeHandler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        routes = {
            "/": self.handle_status,
            "/api/status": self.handle_status,
            "/api/screenshot": self.handle_screenshot,
            "/api/page-info": self.handle_page_info,
            "/api/page-text": self.handle_page_text,
            "/api/tabs": self.handle_tabs,
            "/api/dom": self.handle_dom,
            "/api/forms": self.handle_forms,
        }
        
        handler = routes.get(self.path.split("?")[0])
        if handler:
            handler()
        else:
            self.send_json(404, {"error": "Not found"})
    
    def do_POST(self):
        routes = {
            "/api/click": self.handle_click,
            "/api/type": self.handle_type,
            "/api/scroll": self.handle_scroll,
            "/api/hover": self.handle_hover,
            "/api/fill": self.handle_fill,
            "/api/navigate": self.handle_navigate,
            "/api/find": self.handle_find,
            "/api/js": self.handle_js,
            "/api/highlight": self.handle_highlight,
            "/api/close-tab": self.handle_close_tab,
            "/api/switch-tab": self.handle_switch_tab,
            "/api/new-tab": self.handle_new_tab,
            "/api/wait-for-element": self.handle_wait_for_element,
            "/api/wait-for-result": self.handle_wait_for_result,
            "/api/page-text-by-tab-id": self.handle_page_text_by_tab_id,
            "/api/batch": self.handle_batch,
        }
        
        handler = routes.get(self.path)
        if handler:
            handler()
        else:
            self.send_json(404, {"error": "Not found"})
    
    # ── GET Handlers ──
    
    def handle_status(self):
        self.send_json(200, {
            "status": "running",
            "extension_connected": extension_ws is not None,
            "port": HTTP_PORT,
            "ws_port": WS_PORT,
            "screenshot_dir": SCREENSHOT_DIR
        })
    
    def handle_screenshot(self):
        result = self.run_async(send_to_extension("screenshot"))
        if "error" in result:
            self.send_json(500, result)
            return
        
        # Save screenshot to file
        if result.get("dataUrl"):
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            filename = f"zen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = os.path.join(SCREENSHOT_DIR, filename)
            
            # Strip data URL prefix
            b64 = result["dataUrl"].split(",", 1)[1] if "," in result["dataUrl"] else result["dataUrl"]
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(b64))
            
            result["saved_to"] = filepath
            result["filename"] = filename
            # Don't send the full base64 back over HTTP (too large)
            del result["dataUrl"]
        
        self.send_json(200, result)
    
    def handle_page_info(self):
        result = self.run_async(send_to_extension("getPageInfo"))
        self.send_json(200, result)
    
    def handle_page_text(self):
        result = self.run_async(send_to_extension("getPageText"))
        self.send_json(200, result)
    
    def handle_tabs(self):
        result = self.run_async(send_to_extension("getTabs"))
        self.send_json(200, result)
    
    def handle_dom(self):
        result = self.run_async(send_to_extension("getAccessibilityTree", {"depth": 6}))
        self.send_json(200, result)
    
    def handle_forms(self):
        result = self.run_async(send_to_extension("getFormFields"))
        self.send_json(200, result)
    
    # ── POST Handlers ──
    
    def handle_click(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("click", body))
        self.send_json(200, result)
    
    def handle_type(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("type", body))
        self.send_json(200, result)
    
    def handle_scroll(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("scroll", body))
        self.send_json(200, result)
    
    def handle_hover(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("hover", body))
        self.send_json(200, result)
    
    def handle_fill(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("fill", body))
        self.send_json(200, result)
    
    def handle_navigate(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("navigate", body))
        self.send_json(200, result)
    
    def handle_find(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("find", body))
        self.send_json(200, result)
    
    def handle_js(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("executeJS", body))
        self.send_json(200, result)
    
    def handle_highlight(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("highlight", body))
        self.send_json(200, result)

    def handle_close_tab(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("closeTab", body))
        self.send_json(200, result)

    def handle_switch_tab(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("switchTab", body))
        self.send_json(200, result)

    def handle_new_tab(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("newTab", body))
        self.send_json(200, result)

    def handle_wait_for_element(self):
        body = self.read_body()
        # waitForElement can take up to timeout + buffer; extend the WS timeout accordingly
        timeout_ms = body.get("timeout", 10000)
        timeout_s = (timeout_ms / 1000) + 5
        result = self.run_async(send_to_extension("waitForElement", body, timeout=timeout_s))
        self.send_json(200, result)

    def handle_wait_for_result(self):
        body = self.read_body()
        timeout_ms = body.get("timeout", 15000)
        timeout_s = (timeout_ms / 1000) + 5
        result = self.run_async(send_to_extension("waitForResult", body, timeout=timeout_s))
        self.send_json(200, result)

    def handle_page_text_by_tab_id(self):
        body = self.read_body()
        result = self.run_async(send_to_extension("getPageTextFromTab", body))
        self.send_json(200, result)

    def handle_batch(self):
        body = self.read_body()
        commands = body.get('commands', [])
        stop_on_warning = body.get('stopOnWarning', False)

        async def run_all():
            results = []
            for cmd in commands:
                r = await run_command(cmd)
                results.append(r)
                # Stop early if a command returned a warning/redirect and stopOnWarning is set
                if stop_on_warning and isinstance(r, dict) and (r.get('warning') or r.get('error')):
                    r['_stopped'] = True
                    break
            return results

        results = self.run_async(run_all(), timeout=300)
        self.send_json(200, {'results': results})

    # ── Helpers ──
    
    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except:
            return {}
    
    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def run_async(self, coro, timeout=35):
        """Run async coroutine from sync HTTP handler."""
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
    
    def log_message(self, format, *args):
        print(f"[{now()}] HTTP {args[0]}" if args else "")


def now():
    return datetime.now().strftime("%H:%M:%S")


# ══════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════

loop = None

async def main():
    global loop
    loop = asyncio.get_event_loop()
    
    # Start WebSocket server
    ws_server = await ws_serve(ws_handler, "127.0.0.1", WS_PORT)
    print(f"[{now()}] 🔌 WebSocket server on ws://localhost:{WS_PORT}")
    
    # Start HTTP server in thread
    http_server = HTTPServer(("127.0.0.1", HTTP_PORT), BridgeHandler)
    http_thread = Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    print(f"[{now()}] 🌐 HTTP API server on http://localhost:{HTTP_PORT}")
    
    print(f"[{now()}] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"[{now()}] Claude Bridge for Zen Browser is ready!")
    print(f"[{now()}] Waiting for extension to connect...")
    print(f"[{now()}] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("API Endpoints:")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/status             - Connection status")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/screenshot          - Capture page")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/page-info           - Page metadata")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/page-text           - Page text content")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/tabs                - List open tabs")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/dom                 - Accessibility tree")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/forms               - Form fields")
    print(f"  POST http://localhost:{HTTP_PORT}/api/click               - Click element")
    print(f"  POST http://localhost:{HTTP_PORT}/api/type                - Type text")
    print(f"  POST http://localhost:{HTTP_PORT}/api/scroll              - Scroll page")
    print(f"  POST http://localhost:{HTTP_PORT}/api/hover               - Hover element")
    print(f"  POST http://localhost:{HTTP_PORT}/api/fill                - Fill form field")
    print(f"  POST http://localhost:{HTTP_PORT}/api/navigate            - Go to URL")
    print(f"  POST http://localhost:{HTTP_PORT}/api/find                - Find elements")
    print(f"  POST http://localhost:{HTTP_PORT}/api/js                  - Execute JavaScript")
    print(f"  POST http://localhost:{HTTP_PORT}/api/highlight           - Highlight element")
    print(f"  POST http://localhost:{HTTP_PORT}/api/wait-for-element    - Wait for element to appear")
    print(f"  POST http://localhost:{HTTP_PORT}/api/wait-for-result     - Poll JS expression until non-empty")
    print()
    
    # Keep running
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{now()}] Shutting down...")
