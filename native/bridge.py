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
import ctypes
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
BRIDGE_VERSION = "2.0.4"
API_VERSION = "1"
HTTP_PORT = 8765
WS_PORT = 8766
SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "claude-zen-screenshots")
WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workflows")

# ── State ──
extension_ws = None  # WebSocket connection to extension
pending_commands = {}  # id -> asyncio.Future
command_counter = 0

# ── Cache ──
import re as _re
_cache = {}  # key -> (timestamp, data)
CACHE_TTL = 5  # seconds

# ── Keep-alive (tab unloader defense) ──
# Zen aggressively unloads inactive tabs. For reliable parallel multi-tab work
# we run a background pinger per tab the agent has asked us to keep warm.
_keep_alive_tasks = {}  # tab_id -> asyncio.Task

# ── 1.4.0 orchestration / state ──
_tags = {}  # tag name -> tab_id
_tab_pool = {  # tab id pool for fast-acquire workflows
    "size": 0,
    "available": [],   # list of tab ids
    "in_use": set(),   # tab ids currently checked out
    "url": "about:blank",
}
_audit_log = []  # list of {ts, action, params, ok, error, ms}
_AUDIT_MAX = 2000
_log_buffer = []  # mirrors bridge stdout lines
_LOG_MAX = 5000
_policy = {"allow": [], "deny": [], "readonly": False}
_sessions_dir = os.path.join(os.path.expanduser("~"), "claude-zen-sessions")

def _cache_key(action, params):
    return action + ":" + json.dumps(params, sort_keys=True)

def _cache_get(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1]
    if entry:
        del _cache[key]
    return None

def _cache_set(key, data):
    _cache[key] = (time.time(), data)
    if len(_cache) > 100:
        cutoff = time.time() - CACHE_TTL
        for k in [k for k, (t, _) in _cache.items() if t < cutoff]:
            del _cache[k]


def _cache_clear(reason=None):
    """Clear short-lived read cache after browser/page mutations."""
    if _cache:
        _cache.clear()


def _focus_window_by_title(title):
    """Best-effort foreground focus for callers that explicitly ask for it."""
    if not sys.platform.startswith("win") or not title:
        return False

    user32 = ctypes.windll.user32
    matches = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        text = buf.value
        if title in text or text in title:
            matches.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    if not matches:
        return False

    hwnd = matches[0]
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


def _native_click_screen(x, y):
    if not sys.platform.startswith("win"):
        return {"error": "trustedClick is only implemented on Windows"}

    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(round(x)), int(round(y)))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.03)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    return {"ok": True}


async def trusted_click(params):
    """Use a real OS-level mouse click at a browser viewport coordinate."""
    selector = params.get("selector") or ""
    coords = params.get("coords") or {}
    coordinate_space = params.get("coordinateSpace") or params.get("space") or "viewport"
    focus = params.get("focus", False)
    x = params.get("x", coords.get("x"))
    y = params.get("y", coords.get("y"))

    if not selector and (x is None or y is None):
        return {"error": "trustedClick requires selector or x/y coordinates"}

    if coordinate_space == "screen":
        focused = _focus_window_by_title(params.get("title") or "Zen Browser") if focus else False
        clicked = _native_click_screen(x, y)
        if "error" in clicked:
            return clicked
        return {
            "ok": True,
            "trusted": True,
            "coordinateSpace": "screen",
            "screenX": x,
            "screenY": y,
            "focused": focused,
            "focusRequested": bool(focus),
        }

    selector_json = json.dumps(selector)
    x_json = json.dumps(x)
    y_json = json.dumps(y)
    code = f"""
(() => {{
  const selector = {selector_json};
  let x = {x_json};
  let y = {y_json};
  const el = selector ? document.querySelector(selector) : null;
  if (el) {{
    el.scrollIntoView({{ behavior: 'instant', block: 'center', inline: 'center' }});
    const rect = el.getBoundingClientRect();
    x = rect.left + rect.width / 2;
    y = rect.top + rect.height / 2;
  }}
  if (x == null || y == null) return JSON.stringify({{ error: 'No target point' }});
  const viewportLeft = Number.isFinite(window.mozInnerScreenX)
    ? window.mozInnerScreenX
    : window.screenX + Math.max(0, (window.outerWidth - window.innerWidth) / 2);
  const viewportTop = Number.isFinite(window.mozInnerScreenY)
    ? window.mozInnerScreenY
    : window.screenY + Math.max(0, window.outerHeight - window.innerHeight);
  return JSON.stringify({{
    title: document.title,
    viewportX: x,
    viewportY: y,
    screenX: viewportLeft + x,
    screenY: viewportTop + y,
    devicePixelRatio: window.devicePixelRatio || 1
  }});
}})()
"""
    result = await send_to_extension("executeJS", {"code": code})
    if "error" in result:
        return result

    try:
        point = json.loads(result.get("result") or "{}")
    except json.JSONDecodeError:
        return {"error": "trustedClick could not parse browser point", "raw": result}

    if "error" in point:
        return point

    focused = _focus_window_by_title(point.get("title", "")) if focus else False
    clicked = _native_click_screen(point["screenX"], point["screenY"])
    if "error" in clicked:
        return clicked

    return {
        "ok": True,
        "trusted": True,
        "screenX": point["screenX"],
        "screenY": point["screenY"],
        "viewportX": point["viewportX"],
        "viewportY": point["viewportY"],
        "title": point.get("title", ""),
        "focused": focused,
        "focusRequested": bool(focus),
    }


def _log(msg):
    """Tee print() to in-memory ring buffer so /api/logs can read it back."""
    line = f"[{now()}] {msg}"
    print(line)
    _log_buffer.append(line)
    if len(_log_buffer) > _LOG_MAX:
        del _log_buffer[: len(_log_buffer) - _LOG_MAX]


def _audit(action, params, ok, error, ms):
    _audit_log.append({
        "ts": int(time.time() * 1000),
        "action": action,
        "params": {k: v for k, v in (params or {}).items() if k != "code"},  # don't log JS bodies
        "ok": bool(ok),
        "error": error,
        "ms": int(ms),
    })
    if len(_audit_log) > _AUDIT_MAX:
        del _audit_log[: len(_audit_log) - _AUDIT_MAX]


WRITE_ACTIONS = {
    "click", "type", "setEditableContent", "scroll", "hover", "fill",
    "navigate", "newTab", "closeTab", "switchTab", "executeJS", "trustedClick",
    "selectOption", "checkBox", "keypress", "doubleClick", "submitForm",
    "formFill", "drag", "cookies", "clipboard", "downloads",
    "clearBrowsingData", "intercept", "clickAndWaitNavigation",
    "pinTab", "muteTab", "duplicateTab", "reloadTabBrowser", "goBack", "goForward",
    "setZoom", "createWindow", "closeWindow", "moveTab", "detachTab",
    "storageOp", "reloadExtension", "wakeTab", "highlight", "clearHighlight",
}


def _check_policy(action, params):
    """Return error dict if action/url violates current policy, else None."""
    if _policy["readonly"] and action in WRITE_ACTIONS:
        # Read-only ops on cookies/storage/etc. are still allowed; tighten if needed.
        sub_op = (params or {}).get("op") if isinstance(params, dict) else None
        if action in ("cookies", "clipboard", "storageOp", "downloads") and sub_op in ("get", "read", "list", "snapshot"):
            return None
        return {"error": f"Read-only mode is active; refusing action: {action}"}
    url = (params or {}).get("url") if isinstance(params, dict) else None
    if url:
        for pat in _policy.get("deny", []):
            try:
                if _re.search(pat, url):
                    return {"error": f"URL denied by policy ({pat}): {url}"}
            except _re.error:
                pass
        allow = _policy.get("allow", [])
        if allow:
            ok = False
            for pat in allow:
                try:
                    if _re.search(pat, url):
                        ok = True
                        break
                except _re.error:
                    pass
            if not ok:
                return {"error": f"URL not in allowlist: {url}"}
    return None


def _invalidate_cache_for_action(action):
    if action in WRITE_ACTIONS or action in ("elementScreenshot", "fullPageScreenshot"):
        _cache_clear(action)


# ── Orchestration: broadcast and syncBarrier ──

async def _broadcast_async(tab_ids, command, timeout=30):
    """Send the same command (minus tabId) to each tab in parallel."""
    if not tab_ids:
        return {"error": "broadcast requires at least one tabId"}
    action = command.get("action")
    if not action:
        return {"error": "broadcast command needs an action"}
    base_params = {k: v for k, v in command.items() if k != "action"}

    async def one(tid):
        # Route through the same logic batch uses so behavior is identical.
        cmd = {**base_params, "action": action, "tabId": tid}
        try:
            res = await asyncio.wait_for(run_command(cmd), timeout=timeout)
            return tid, res
        except asyncio.TimeoutError:
            return tid, {"error": f"timed out after {timeout}s"}

    results = await asyncio.gather(*[one(t) for t in tab_ids])
    return {"results": {str(t): r for t, r in results}}


async def _sync_barrier_async(tab_ids, predicate, timeout=30000, poll_interval=300):
    """Wait until a JS predicate returns truthy on every listed tab.
    Returns per-tab status and overall ok flag."""
    if not tab_ids:
        return {"error": "syncBarrier requires at least one tabId"}
    deadline = time.monotonic() + (timeout / 1000)
    last = {}
    while time.monotonic() < deadline:
        all_pass = True
        for tid in tab_ids:
            r = await send_to_extension("executeJS", {"tabId": tid, "code": predicate}, timeout=10)
            last[tid] = r
            val = (r or {}).get("result")
            if val in (None, "", "0", "false", "null", "undefined"):
                all_pass = False
        if all_pass:
            return {"ok": True, "results": last}
        await asyncio.sleep(poll_interval / 1000)
    return {"ok": False, "results": last, "error": "syncBarrier timed out"}


# ── Tab pool ──

async def _ensure_tab_pool(target_size, url):
    """Grow or shrink the pool to target_size warm tabs."""
    while len(_tab_pool["available"]) + len(_tab_pool["in_use"]) < target_size:
        r = await send_to_extension("newTab", {"url": url})
        tid = r.get("tabId") if isinstance(r, dict) else None
        if tid:
            _tab_pool["available"].append(tid)
        else:
            return {"error": "failed to create pool tab: " + str(r)}
    # Shrink: close extra available tabs
    while len(_tab_pool["available"]) + len(_tab_pool["in_use"]) > target_size and _tab_pool["available"]:
        tid = _tab_pool["available"].pop()
        try:
            await send_to_extension("closeTab", {"tabId": tid})
        except Exception:
            pass
    _tab_pool["size"] = target_size
    _tab_pool["url"] = url
    return _pool_status()


def _pool_status():
    return {
        "size": _tab_pool["size"],
        "available": list(_tab_pool["available"]),
        "in_use": list(_tab_pool["in_use"]),
        "url": _tab_pool["url"],
    }


# ── Session save/load (cookies + localStorage) ──

def _session_path(name):
    safe = _re.sub(r"[^\w\-]", "_", name or "")
    return os.path.join(_sessions_dir, safe + ".json")


async def _save_session_async(name, urls):
    os.makedirs(_sessions_dir, exist_ok=True)
    bundle = {"name": name, "saved": int(time.time()), "cookies": [], "storage": {}}
    # cookies for each url
    for u in (urls or []):
        r = await send_to_extension("cookies", {"op": "get", "url": u}, timeout=10)
        if isinstance(r, dict) and "cookies" in r:
            bundle["cookies"].extend(r["cookies"])
    # localStorage snapshot per active tab if no urls
    if urls:
        for u in urls:
            # Open a temp tab for each origin, snapshot, close
            t = await send_to_extension("newTab", {"url": u})
            tid = t.get("tabId") if isinstance(t, dict) else None
            if tid:
                # Wait briefly for page
                await asyncio.sleep(1)
                snap = await send_to_extension("storageOp", {"tabId": tid, "kind": "local", "op": "snapshot"}, timeout=10)
                if isinstance(snap, dict) and snap.get("ok"):
                    bundle["storage"][u] = snap.get("items", {})
                await send_to_extension("closeTab", {"tabId": tid})
    with open(_session_path(name), "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    return {"ok": True, "name": name, "path": _session_path(name), "cookies": len(bundle["cookies"]), "origins": len(bundle["storage"])}


async def _load_session_async(name):
    path = _session_path(name)
    if not os.path.isfile(path):
        return {"error": f"Session not found: {name}"}
    with open(path, "r", encoding="utf-8") as f:
        bundle = json.load(f)
    set_count = 0
    for c in bundle.get("cookies", []):
        try:
            url = ("https://" if c.get("secure") else "http://") + str(c.get("domain", "")).lstrip(".") + str(c.get("path", "/"))
            await send_to_extension("cookies", {
                "op": "set", "url": url, "name": c.get("name"), "value": c.get("value"),
                "domain": c.get("domain"), "path": c.get("path"), "secure": c.get("secure"),
            }, timeout=10)
            set_count += 1
        except Exception:
            pass
    storage_restored = 0
    for u, items in (bundle.get("storage") or {}).items():
        t = await send_to_extension("newTab", {"url": u})
        tid = t.get("tabId") if isinstance(t, dict) else None
        if tid:
            await asyncio.sleep(0.8)
            await send_to_extension("storageOp", {"tabId": tid, "kind": "local", "op": "restore", "value": items}, timeout=10)
            await send_to_extension("closeTab", {"tabId": tid})
            storage_restored += 1
    return {"ok": True, "name": name, "cookies_set": set_count, "origins_restored": storage_restored}


def _list_sessions():
    if not os.path.isdir(_sessions_dir):
        return []
    out = []
    for fn in os.listdir(_sessions_dir):
        if fn.endswith(".json"):
            p = os.path.join(_sessions_dir, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    b = json.load(f)
                out.append({
                    "name": b.get("name", fn[:-5]),
                    "path": p,
                    "saved": b.get("saved"),
                    "cookies": len(b.get("cookies", [])),
                    "origins": len(b.get("storage", {})),
                })
            except Exception:
                pass
    return out


# ── Variable substitution in batch ──

def _substitute_vars(cmd, prior_results):
    """Replace ${$N.path.to.value} tokens anywhere in cmd with values from prior_results.

    Example: ${$0.tabId} pulls .tabId from the first prior result.
    """
    if isinstance(cmd, dict):
        return {k: _substitute_vars(v, prior_results) for k, v in cmd.items()}
    if isinstance(cmd, list):
        return [_substitute_vars(x, prior_results) for x in cmd]
    if isinstance(cmd, str):
        def repl(m):
            path = m.group(1)
            try:
                if not path.startswith("$"):
                    return m.group(0)
                # $N.foo.bar
                head, *rest = path[1:].split(".")
                idx = int(head)
                val = prior_results[idx]
                for k in rest:
                    if isinstance(val, dict):
                        val = val.get(k)
                    elif isinstance(val, list) and k.isdigit():
                        val = val[int(k)]
                    else:
                        return m.group(0)
                return val if isinstance(val, str) else json.dumps(val)
            except Exception:
                return m.group(0)
        return _re.sub(r"\$\{(\$[^}]+)\}", repl, cmd)
    return cmd


async def _keep_alive_loop(tab_id, interval):
    """Fire a cheap script eval at a tab on every interval so Zen's unloader
    keeps resetting its idle timer. Any failure is swallowed — the loop keeps
    going until cancelled, since the tab may briefly be navigating or closing
    without us needing to bail."""
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await send_to_extension("executeJS", {"tabId": tab_id, "code": "1"}, timeout=10)
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


async def _start_keep_alive_async(tab_ids, interval):
    started = []
    for tid in tab_ids:
        existing = _keep_alive_tasks.get(tid)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(_keep_alive_loop(tid, interval))
        _keep_alive_tasks[tid] = task
        started.append(tid)
    return started


async def _stop_keep_alive_async(tab_ids):
    stopped = []
    if tab_ids is None:
        tab_ids = list(_keep_alive_tasks.keys())
    for tid in tab_ids:
        task = _keep_alive_tasks.pop(tid, None)
        if task and not task.done():
            task.cancel()
            stopped.append(tid)
    return stopped


def _parse_workflow(filepath, variables=None):
    """Parse a workflow .md file into a list of batch commands."""
    variables = variables or {}
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = _re.findall(r'```json\s*\n(.*?)\n```', content, _re.DOTALL)

    commands = []
    for block in blocks:
        text = block
        for key, val in variables.items():
            # For placeholders embedded inside a JSON string that itself contains JS code.
            text = text.replace('{{jslit:' + key + '}}', json.dumps(json.dumps(val))[1:-1])
            text = text.replace('{{json:' + key + '}}', json.dumps(val))
            text = text.replace('{{' + key + '}}', str(val))
        try:
            cmd = json.loads(text)
            commands.append(cmd)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in workflow block: {e}"

    if not commands:
        return None, "No JSON command blocks found in workflow"
    return commands, None


# ══════════════════════════════════════════════
#  WebSocket Server (Extension connects here)
# ══════════════════════════════════════════════

async def ws_handler(websocket):
    global extension_ws
    if extension_ws is not None:
        print(f"[{now()}] ↻ New extension connection replacing existing one")
    extension_ws = websocket
    print(f"[{now()}] ✅ Zen Browser extension connected")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"[{now()}] ⚠️ Bad message from extension: {e}")
                continue
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
        # Fail all pending commands immediately instead of letting them timeout
        for cmd_id, future in list(pending_commands.items()):
            if not future.done():
                future.set_result({"error": "Extension disconnected"})
        pending_commands.clear()


async def send_to_extension(action, params=None, timeout=30):
    """Send command to extension and wait for response."""
    global command_counter
    
    if not extension_ws:
        return {"error": "Zen Browser extension not connected. Open Zen Browser and ensure the extension is loaded."}
    
    command_counter += 1
    cmd_id = f"cmd_{command_counter}_{int(time.time())}"
    
    # Spread params FIRST so that the command name is never clobbered by a
    # `params["action"]` field. (Some endpoints — captureNetwork, intercept —
    # legitimately use "action" as a sub-op name; without this ordering, the
    # extension receives the sub-op as the top-level action and reports
    # "Unknown action: start"/"block".)
    command = {
        "id": cmd_id,
        **(params or {}),
        "action": action,
    }
    
    future = asyncio.get_running_loop().create_future()
    pending_commands[cmd_id] = future
    
    try:
        await extension_ws.send(json.dumps(command))
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {timeout}s"}
    except websockets.exceptions.ConnectionClosed:
        return {"error": "Extension disconnected while waiting for response"}
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
    'setEditableContent': 'setEditableContent',
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
    'wakeTab':        'wakeTab',
    'reloadExtension':'reloadExtension',
    # 1.4.0 — browser-level
    'pinTab':              'pinTab',
    'muteTab':             'muteTab',
    'duplicateTab':        'duplicateTab',
    'reloadTabBrowser':    'reloadTabBrowser',
    'goBack':              'goBack',
    'goForward':           'goForward',
    'getZoom':             'getZoom',
    'setZoom':             'setZoom',
    'getWindows':          'getWindows',
    'createWindow':        'createWindow',
    'closeWindow':         'closeWindow',
    'focusWindow':         'focusWindow',
    'moveTab':             'moveTab',
    'detachTab':           'detachTab',
    'elementScreenshot':   'elementScreenshot',
    'fullPageScreenshot':  'fullPageScreenshot',
    'cookies':             'cookies',
    'clipboard':           'clipboard',
    'downloads':           'downloads',
    'clearBrowsingData':   'clearBrowsingData',
    'intercept':           'intercept',
    'clickAndWaitNavigation': 'clickAndWaitNavigation',
    # 1.4.0 — content-script-forwarded
    'query':               'query',
    'getHTML':             'getHTML',
    'getLinks':            'getLinks',
    'getImages':           'getImages',
    'getMeta':             'getMeta',
    'getStructuredData':   'getStructuredData',
    'getBounds':           'getBounds',
    'getComputedStyle':    'getComputedStyle',
    'getReadability':      'getReadability',
    'getMarkdown':         'getMarkdown',
    'selectOption':        'selectOption',
    'checkBox':            'checkBox',
    'focusElement':        'focusElement',
    'blurElement':         'blurElement',
    'keypress':            'keypress',
    'doubleClick':         'doubleClick',
    'submitForm':          'submitForm',
    'formFill':            'formFill',
    'drag':                'drag',
    'waitForUrl':          'waitForUrl',
    'waitForTitle':        'waitForTitle',
    'waitForNetworkIdle':  'waitForNetworkIdle',
    'captureNetwork':      'captureNetwork',
    'watchConsole':        'watchConsole',
    'consoleLogs':         'consoleLogs',
    'storageOp':           'storageOp',
    'getIframes':          'getIframes',
    'explainSelector':     'explainSelector',
    'fullPageMetrics':     'fullPageMetrics',
}


async def run_command(cmd):
    """Execute a single batch command asynchronously."""
    action = cmd.get('action', '')
    params = {k: v for k, v in cmd.items() if k != 'action'}
    try:
        # Normalize find params: extension expects "query", users may send "selector"
        if action == 'find' and 'query' not in params and 'selector' in params:
            params['query'] = params.pop('selector')

        effective_action = BATCH_ACTION_MAP.get(action, action)
        policy_err = _check_policy(effective_action, params)
        if policy_err:
            return policy_err
        _invalidate_cache_for_action(effective_action)

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
        elif action == 'trustedClick':
            return await trusted_click(params)
        elif action == 'sleep':
            await asyncio.sleep(params.get('ms', 1000) / 1000)
            return {'ok': True}
        elif action == 'parallel':
            sequences = params.get('sequences', [])
            # Validate: navigate/switchTab in parallel sequences must specify tabId
            for i, seq in enumerate(sequences):
                for cmd in seq:
                    a = cmd.get('action', '')
                    if a in ('navigate', 'switchTab') and 'tabId' not in cmd:
                        return {'error': f'Sequence {i}: "{a}" inside parallel requires explicit tabId to avoid race conditions'}
            async def run_sequence(seq):
                results = []
                for c in seq:
                    results.append(await run_command(c))
                return results
            return list(await asyncio.gather(*[run_sequence(s) for s in sequences]))
        elif action == 'broadcast':
            return await _broadcast_async(params.get('tabIds', []), params.get('command', {}))
        elif action == 'syncBarrier':
            return await _sync_barrier_async(
                params.get('tabIds', []),
                params.get('predicate', '1'),
                timeout=params.get('timeout', 30000),
                poll_interval=params.get('pollInterval', 300),
            )
        elif action == 'retry':
            inner = params.get('command') or {}
            attempts = max(1, int(params.get('maxAttempts', 3)))
            backoff_ms = int(params.get('backoffMs', 500))
            on_errors = params.get('onErrors')
            last = None
            for i in range(attempts):
                last = await run_command(inner)
                if not (isinstance(last, dict) and last.get('error')):
                    return {'ok': True, 'attempts': i + 1, 'result': last}
                if on_errors and not any(substr in last.get('error', '') for substr in on_errors):
                    return {'ok': False, 'attempts': i + 1, 'result': last, 'reason': 'error not in onErrors'}
                await asyncio.sleep((backoff_ms / 1000) * (2 ** i))
            return {'ok': False, 'attempts': attempts, 'result': last}
        elif action == 'if':
            cond = params.get('condition')  # JS expression to evaluate on tabId
            tab_id = params.get('tabId')
            then_cmds = params.get('then', [])
            else_cmds = params.get('else', [])
            check = await send_to_extension('executeJS', {'tabId': tab_id, 'code': cond or '1'}, timeout=10)
            truthy = bool(check.get('result')) and check.get('result') not in ('false', '0', 'null', 'undefined', '')
            branch = then_cmds if truthy else else_cmds
            out = []
            for c in branch:
                out.append(await run_command(c))
            return {'branch': 'then' if truthy else 'else', 'condition': check.get('result'), 'results': out}
        elif action == 'while':
            cond = params.get('condition')
            tab_id = params.get('tabId')
            body = params.get('do', [])
            max_iters = int(params.get('maxIterations', 20))
            results = []
            for i in range(max_iters):
                check = await send_to_extension('executeJS', {'tabId': tab_id, 'code': cond or '0'}, timeout=10)
                truthy = bool(check.get('result')) and check.get('result') not in ('false', '0', 'null', 'undefined', '')
                if not truthy:
                    return {'iterations': i, 'results': results, 'stoppedBy': 'condition'}
                step = []
                for c in body:
                    step.append(await run_command(c))
                results.append(step)
            return {'iterations': max_iters, 'results': results, 'stoppedBy': 'maxIterations'}
        elif action == 'try':
            body = params.get('do', [])
            catch = params.get('catch', [])
            try:
                results = []
                for c in body:
                    r = await run_command(c)
                    results.append(r)
                    if isinstance(r, dict) and r.get('error'):
                        raise RuntimeError(r['error'])
                return {'ok': True, 'results': results}
            except Exception as e:
                caught = []
                for c in catch:
                    caught.append(await run_command(c))
                return {'ok': False, 'error': str(e), 'catch_results': caught}
        elif action == 'sequence':
            # Run a list of commands with variable substitution between them
            inner = params.get('do', [])
            prior = []
            out = []
            for c in inner:
                resolved = _substitute_vars(c, prior)
                r = await run_command(resolved)
                prior.append(r)
                out.append(r)
            return {'results': out}
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
            "/api/version": self.handle_version,
            "/api/workflows": self.handle_list_workflows,
            # 1.4.0 — read-only routes also reachable via GET
            "/api/health": self.handle_health,
            "/api/logs": self.handle_logs,
            "/api/audit": self.handle_audit,
            "/api/get-policy": self.handle_get_policy,
            "/api/list-sessions": self.handle_list_sessions,
            "/api/list-tags": self.handle_list_tags,
        }
        
        handler = routes.get(self.path.split("?")[0])
        if handler:
            handler()
        else:
            self.send_json(404, {"error": "Not found"})
    
    def do_POST(self):
        routes = {
            "/api/click": self.handle_click,
            "/api/trusted-click": self.handle_trusted_click,
            "/api/type": self.handle_type,
            "/api/set-editable-content": self.handle_set_editable_content,
            "/api/scroll": self.handle_scroll,
            "/api/hover": self.handle_hover,
            "/api/fill": self.handle_fill,
            "/api/navigate": self.handle_navigate,
            "/api/find": self.handle_find,
            "/api/js": self.handle_js,
            "/api/highlight": self.handle_highlight,
            "/api/clear-highlight": self.handle_clear_highlight,
            "/api/close-tab": self.handle_close_tab,
            "/api/switch-tab": self.handle_switch_tab,
            "/api/new-tab": self.handle_new_tab,
            "/api/wait-for-element": self.handle_wait_for_element,
            "/api/wait-for-result": self.handle_wait_for_result,
            "/api/page-text-by-tab-id": self.handle_page_text_by_tab_id,
            "/api/batch": self.handle_batch,
            "/api/cache": self.handle_cache,
            "/api/workflow": self.handle_workflow,
            # GET endpoints also accepted as POST so callers can supply
            # a tabId in the body to target a non-active tab.
            "/api/page-info": self.handle_page_info,
            "/api/page-text": self.handle_page_text,
            "/api/tabs": self.handle_tabs,
            "/api/forms": self.handle_forms,
            "/api/dom": self.handle_dom,
            # Parallel multi-tab work
            "/api/wake-tab": self.handle_wake_tab,
            "/api/keep-alive": self.handle_keep_alive,
            "/api/keep-alive-stop": self.handle_keep_alive_stop,
            "/api/reload-extension": self.handle_reload_extension,
            # 1.4.0 — generic passthrough handlers (forward body to extension action)
            "/api/query": lambda: self._forward("query"),
            "/api/html": lambda: self._forward("getHTML"),
            "/api/links": lambda: self._forward("getLinks"),
            "/api/images": lambda: self._forward("getImages"),
            "/api/meta": lambda: self._forward("getMeta"),
            "/api/structured-data": lambda: self._forward("getStructuredData"),
            "/api/bounds": lambda: self._forward("getBounds"),
            "/api/computed-style": lambda: self._forward("getComputedStyle"),
            "/api/readability": lambda: self._forward("getReadability"),
            "/api/markdown": lambda: self._forward("getMarkdown"),
            "/api/select-option": lambda: self._forward("selectOption"),
            "/api/check": lambda: self._forward("checkBox"),
            "/api/focus": lambda: self._forward("focusElement"),
            "/api/blur": lambda: self._forward("blurElement"),
            "/api/keypress": lambda: self._forward("keypress"),
            "/api/double-click": lambda: self._forward("doubleClick"),
            "/api/submit-form": lambda: self._forward("submitForm"),
            "/api/form-fill": lambda: self._forward("formFill"),
            "/api/drag": lambda: self._forward("drag"),
            "/api/wait-for-url": lambda: self._forward("waitForUrl", default_timeout=20),
            "/api/wait-for-title": lambda: self._forward("waitForTitle", default_timeout=20),
            "/api/wait-for-network-idle": lambda: self._forward("waitForNetworkIdle", default_timeout=30),
            "/api/capture-network": lambda: self._forward("captureNetwork"),
            "/api/watch-console": lambda: self._forward("watchConsole"),
            "/api/console-logs": lambda: self._forward("consoleLogs"),
            "/api/storage": lambda: self._forward("storageOp"),
            "/api/iframes": lambda: self._forward("getIframes"),
            "/api/explain-selector": lambda: self._forward("explainSelector"),
            "/api/full-page-metrics": lambda: self._forward("fullPageMetrics"),
            "/api/pin-tab": lambda: self._forward("pinTab"),
            "/api/mute-tab": lambda: self._forward("muteTab"),
            "/api/duplicate-tab": lambda: self._forward("duplicateTab"),
            "/api/reload-tab": lambda: self._forward("reloadTabBrowser"),
            "/api/back": lambda: self._forward("goBack"),
            "/api/forward": lambda: self._forward("goForward"),
            "/api/get-zoom": lambda: self._forward("getZoom"),
            "/api/set-zoom": lambda: self._forward("setZoom"),
            "/api/windows": lambda: self._forward("getWindows"),
            "/api/create-window": lambda: self._forward("createWindow"),
            "/api/close-window": lambda: self._forward("closeWindow"),
            "/api/focus-window": lambda: self._forward("focusWindow"),
            "/api/move-tab": lambda: self._forward("moveTab"),
            "/api/detach-tab": lambda: self._forward("detachTab"),
            "/api/element-screenshot": lambda: self._forward("elementScreenshot", default_timeout=20),
            "/api/full-page-screenshot": lambda: self._forward("fullPageScreenshot", default_timeout=60),
            "/api/cookies": lambda: self._forward("cookies"),
            "/api/clipboard": lambda: self._forward("clipboard"),
            "/api/downloads": lambda: self._forward("downloads"),
            "/api/clear-browsing-data": lambda: self._forward("clearBrowsingData", default_timeout=60),
            "/api/intercept": lambda: self._forward("intercept"),
            "/api/click-and-wait-navigation": lambda: self._forward("clickAndWaitNavigation", default_timeout=20),
            # 1.4.0 — bridge-only orchestration
            "/api/broadcast": self.handle_broadcast,
            "/api/sync-barrier": self.handle_sync_barrier,
            "/api/retry": self.handle_retry,
            "/api/tag-tab": self.handle_tag_tab,
            "/api/resolve-tag": self.handle_resolve_tag,
            "/api/list-tags": self.handle_list_tags,
            "/api/untag-tab": self.handle_untag_tab,
            "/api/tab-pool": self.handle_tab_pool,
            "/api/pool-acquire": self.handle_pool_acquire,
            "/api/pool-release": self.handle_pool_release,
            "/api/save-session": self.handle_save_session,
            "/api/load-session": self.handle_load_session,
            "/api/list-sessions": self.handle_list_sessions,
            "/api/delete-session": self.handle_delete_session,
            "/api/health": self.handle_health,
            "/api/logs": self.handle_logs,
            "/api/audit": self.handle_audit,
            "/api/set-policy": self.handle_set_policy,
            "/api/get-policy": self.handle_get_policy,
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
            "screenshot_dir": SCREENSHOT_DIR,
            "bridge_version": BRIDGE_VERSION,
            "keep_alive_tabs": [tid for tid, t in _keep_alive_tasks.items() if not t.done()],
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
        params = self._tab_params()
        key = _cache_key("getPageInfo", params)
        cached = _cache_get(key)
        if cached:
            self.send_json(200, {**cached, "_cached": True})
            return
        result = self.run_async(send_to_extension("getPageInfo", params))
        if "error" not in result:
            _cache_set(key, result)
        self.send_json(200, result)

    def handle_page_text(self):
        params = self._tab_params()
        key = _cache_key("getPageText", params)
        cached = _cache_get(key)
        if cached:
            self.send_json(200, {**cached, "_cached": True})
            return
        result = self.run_async(send_to_extension("getPageText", params))
        if "error" not in result:
            _cache_set(key, result)
        self.send_json(200, result)

    def handle_tabs(self):
        key = _cache_key("getTabs", {})
        cached = _cache_get(key)
        if cached:
            self.send_json(200, {**cached, "_cached": True})
            return
        result = self.run_async(send_to_extension("getTabs"))
        if "error" not in result:
            _cache_set(key, result)
        self.send_json(200, result)

    def handle_dom(self):
        params = {"depth": 6, **self._tab_params()}
        result = self.run_async(send_to_extension("getAccessibilityTree", params))
        self.send_json(200, result)

    def handle_forms(self):
        result = self.run_async(send_to_extension("getFormFields", self._tab_params()))
        self.send_json(200, result)

    def handle_version(self):
        info = {
            "bridge": BRIDGE_VERSION,
            "api": API_VERSION,
            "extension_connected": extension_ws is not None,
        }
        if extension_ws:
            try:
                result = self.run_async(send_to_extension("ping"), timeout=5)
                info["extension"] = result.get("version", "unknown")
            except Exception:
                info["extension"] = "unreachable"
        else:
            info["extension"] = "not connected"
        self.send_json(200, info)

    def handle_list_workflows(self):
        available = []
        if os.path.isdir(WORKFLOW_DIR):
            available = [f[:-3] for f in os.listdir(WORKFLOW_DIR) if f.endswith('.md')]
        self.send_json(200, {"workflows": available, "directory": WORKFLOW_DIR})

    # ── POST Handlers ──

    def _send_extension_action(self, action, body, timeout=35):
        policy_err = _check_policy(action, body)
        if policy_err:
            self.send_json(403, policy_err)
            return
        _invalidate_cache_for_action(action)
        t0 = time.time()
        try:
            result = self.run_async(send_to_extension(action, body, timeout=timeout), timeout=timeout + 5)
        except Exception as e:
            result = {"error": str(e)}
        _audit(
            action,
            body,
            isinstance(result, dict) and not result.get("error"),
            (result or {}).get("error") if isinstance(result, dict) else None,
            (time.time() - t0) * 1000,
        )
        self.send_json(200, result)
    
    def handle_click(self):
        self._send_extension_action("click", self.read_body())

    def handle_trusted_click(self):
        body = self.read_body()
        policy_err = _check_policy("trustedClick", body)
        if policy_err:
            self.send_json(403, policy_err)
            return
        _invalidate_cache_for_action("trustedClick")
        t0 = time.time()
        result = self.run_async(trusted_click(body))
        _audit("trustedClick", body, isinstance(result, dict) and not result.get("error"), (result or {}).get("error") if isinstance(result, dict) else None, (time.time() - t0) * 1000)
        self.send_json(200, result)
    
    def handle_type(self):
        self._send_extension_action("type", self.read_body())

    def handle_set_editable_content(self):
        self._send_extension_action("setEditableContent", self.read_body())
    
    def handle_scroll(self):
        self._send_extension_action("scroll", self.read_body())
    
    def handle_hover(self):
        self._send_extension_action("hover", self.read_body())
    
    def handle_fill(self):
        self._send_extension_action("fill", self.read_body())
    
    def handle_navigate(self):
        self._send_extension_action("navigate", self.read_body())
    
    def handle_find(self):
        body = self.read_body()
        # Extension expects "query", users may send "selector"
        if 'query' not in body and 'selector' in body:
            body['query'] = body.pop('selector')
        result = self.run_async(send_to_extension("find", body))
        self.send_json(200, result)
    
    def handle_js(self):
        self._send_extension_action("executeJS", self.read_body())
    
    def handle_highlight(self):
        self._send_extension_action("highlight", self.read_body())

    def handle_clear_highlight(self):
        self._send_extension_action("clearHighlight", {})

    def handle_close_tab(self):
        self._send_extension_action("closeTab", self.read_body())

    def handle_switch_tab(self):
        self._send_extension_action("switchTab", self.read_body())

    def handle_new_tab(self):
        self._send_extension_action("newTab", self.read_body())

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

    def handle_wake_tab(self):
        self._send_extension_action("wakeTab", self.read_body(), timeout=20)

    def handle_keep_alive(self):
        body = self.read_body()
        tab_ids = body.get("tabIds", []) or []
        try:
            interval = max(10, int(body.get("intervalSeconds", 60)))
        except (TypeError, ValueError):
            interval = 60
        started = self.run_async(_start_keep_alive_async(tab_ids, interval))
        self.send_json(200, {
            "ok": True,
            "keeping_alive": started,
            "interval_seconds": interval,
            "active_total": len([t for t in _keep_alive_tasks.values() if not t.done()]),
        })

    def handle_keep_alive_stop(self):
        body = self.read_body()
        tab_ids = body.get("tabIds")  # None means stop all
        stopped = self.run_async(_stop_keep_alive_async(tab_ids))
        self.send_json(200, {
            "ok": True,
            "stopped": stopped,
            "active_total": len([t for t in _keep_alive_tasks.values() if not t.done()]),
        })

    def handle_reload_extension(self):
        # Extension acks then reloads itself ~100ms later. Use a short timeout
        # — if no ack arrives, the extension is unreachable and reloading is
        # moot anyway.
        self._send_extension_action("reloadExtension", {}, timeout=5)

    # ── 1.4.0 generic forwarder ──

    def _forward(self, action, default_timeout=15):
        """Forward body + policy check + audit to a named extension action."""
        body = self.read_body()
        policy_err = _check_policy(action, body)
        if policy_err:
            self.send_json(403, policy_err)
            return
        _invalidate_cache_for_action(action)
        t0 = time.time()
        try:
            result = self.run_async(send_to_extension(action, body, timeout=default_timeout), timeout=default_timeout + 5)
        except Exception as e:
            result = {"error": str(e)}
        _audit(action, body, isinstance(result, dict) and not result.get("error"), (result or {}).get("error") if isinstance(result, dict) else None, (time.time() - t0) * 1000)
        self.send_json(200, result)

    # ── 1.4.0 orchestration ──

    def handle_broadcast(self):
        body = self.read_body()
        result = self.run_async(_broadcast_async(body.get("tabIds", []), body.get("command", {}), timeout=body.get("timeout", 30)))
        self.send_json(200, result)

    def handle_sync_barrier(self):
        body = self.read_body()
        result = self.run_async(_sync_barrier_async(
            body.get("tabIds", []),
            body.get("predicate", "1"),
            timeout=body.get("timeout", 30000),
            poll_interval=body.get("pollInterval", 300),
        ))
        self.send_json(200, result)

    def handle_retry(self):
        body = self.read_body()
        cmd = {
            "action": "retry",
            **body,
        }
        result = self.run_async(run_command(cmd))
        self.send_json(200, result)

    # ── Tag tabs ──

    def handle_tag_tab(self):
        body = self.read_body()
        name = body.get("name")
        tab_id = body.get("tabId")
        if not name or not tab_id:
            self.send_json(400, {"error": "name and tabId required"})
            return
        _tags[name] = tab_id
        self.send_json(200, {"ok": True, "name": name, "tabId": tab_id, "totalTags": len(_tags)})

    def handle_resolve_tag(self):
        body = self.read_body()
        name = body.get("name")
        tab_id = _tags.get(name)
        if tab_id is None:
            self.send_json(404, {"error": f"Tag not found: {name}"})
            return
        self.send_json(200, {"name": name, "tabId": tab_id})

    def handle_list_tags(self):
        self.send_json(200, {"tags": dict(_tags), "count": len(_tags)})

    def handle_untag_tab(self):
        body = self.read_body()
        name = body.get("name")
        existed = name in _tags
        _tags.pop(name, None)
        self.send_json(200, {"ok": True, "removed": existed})

    # ── Tab pool ──

    def handle_tab_pool(self):
        body = self.read_body()
        size = int(body.get("size", 0))
        url = body.get("url", "about:blank")
        result = self.run_async(_ensure_tab_pool(size, url))
        self.send_json(200, result)

    def handle_pool_acquire(self):
        if not _tab_pool["available"]:
            # Auto-grow if empty
            if _tab_pool["size"] == 0:
                self.send_json(400, {"error": "Tab pool not initialized; call /api/tab-pool first"})
                return
            grow = self.run_async(_ensure_tab_pool(_tab_pool["size"] + 1, _tab_pool["url"]))
            if "error" in grow:
                self.send_json(500, grow)
                return
        tab_id = _tab_pool["available"].pop(0)
        _tab_pool["in_use"].add(tab_id)
        self.send_json(200, {"ok": True, "tabId": tab_id, **_pool_status()})

    def handle_pool_release(self):
        body = self.read_body()
        tab_id = body.get("tabId")
        if tab_id is None:
            self.send_json(400, {"error": "tabId required"})
            return
        _tab_pool["in_use"].discard(tab_id)
        if tab_id not in _tab_pool["available"]:
            _tab_pool["available"].append(tab_id)
        self.send_json(200, {"ok": True, **_pool_status()})

    # ── Sessions ──

    def handle_save_session(self):
        body = self.read_body()
        name = body.get("name") or "default"
        urls = body.get("urls") or []
        result = self.run_async(_save_session_async(name, urls))
        self.send_json(200, result)

    def handle_load_session(self):
        body = self.read_body()
        name = body.get("name") or "default"
        result = self.run_async(_load_session_async(name))
        self.send_json(200, result)

    def handle_list_sessions(self):
        self.send_json(200, {"sessions": _list_sessions(), "dir": _sessions_dir})

    def handle_delete_session(self):
        body = self.read_body()
        name = body.get("name")
        path = _session_path(name)
        if os.path.isfile(path):
            os.remove(path)
            self.send_json(200, {"ok": True, "removed": name})
        else:
            self.send_json(404, {"error": f"Session not found: {name}"})

    # ── Observability ──

    def handle_health(self):
        active_keep_alive = [tid for tid, t in _keep_alive_tasks.items() if not t.done()]
        self.send_json(200, {
            "bridge_version": BRIDGE_VERSION,
            "api_version": API_VERSION,
            "extension_connected": extension_ws is not None,
            "ports": {"http": HTTP_PORT, "ws": WS_PORT},
            "keep_alive_tabs": active_keep_alive,
            "tab_pool": _pool_status(),
            "tags": dict(_tags),
            "policy": dict(_policy),
            "sessions_dir": _sessions_dir,
            "screenshot_dir": SCREENSHOT_DIR,
            "audit_entries": len(_audit_log),
            "log_entries": len(_log_buffer),
        })

    def handle_logs(self):
        body = self.read_body() if self.command == "POST" else {}
        limit = int(body.get("limit", 200))
        since_ts = body.get("since")
        lines = _log_buffer[-limit:]
        if since_ts:
            lines = [l for l in lines if l >= since_ts]  # works on timestamp prefix
        self.send_json(200, {"count": len(lines), "lines": lines})

    def handle_audit(self):
        body = self.read_body() if self.command == "POST" else {}
        limit = int(body.get("limit", 100))
        since = int(body.get("since", 0))
        items = [a for a in _audit_log if a["ts"] >= since][-limit:]
        self.send_json(200, {"count": len(items), "entries": items, "total": len(_audit_log)})

    def handle_set_policy(self):
        body = self.read_body()
        if "allow" in body: _policy["allow"] = list(body["allow"])
        if "deny" in body: _policy["deny"] = list(body["deny"])
        if "readonly" in body: _policy["readonly"] = bool(body["readonly"])
        self.send_json(200, {"ok": True, "policy": dict(_policy)})

    def handle_get_policy(self):
        self.send_json(200, dict(_policy))

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

    def handle_cache(self):
        global CACHE_TTL
        body = self.read_body()
        action = body.get("action", "clear")
        if action == "clear":
            _cache.clear()
            self.send_json(200, {"ok": True, "cleared": True})
        elif action == "status":
            self.send_json(200, {"entries": len(_cache), "ttl": CACHE_TTL})
        elif action == "ttl":
            CACHE_TTL = body.get("seconds", 5)
            self.send_json(200, {"ok": True, "ttl": CACHE_TTL})
        else:
            self.send_json(400, {"error": f"Unknown cache action: {action}"})

    def handle_workflow(self):
        body = self.read_body()
        name = body.get("name", "")
        variables = body.get("variables", {})

        if not name:
            self.send_json(400, {"error": "Missing 'name' parameter"})
            return

        # Sanitize filename
        safe_name = _re.sub(r'[^\w\-]', '', name)
        filepath = os.path.join(WORKFLOW_DIR, f"{safe_name}.md")

        if not os.path.isfile(filepath):
            available = []
            if os.path.isdir(WORKFLOW_DIR):
                available = [f[:-3] for f in os.listdir(WORKFLOW_DIR) if f.endswith('.md')]
            self.send_json(404, {"error": f"Workflow '{name}' not found", "available": available})
            return

        commands, err = _parse_workflow(filepath, variables)
        if err:
            self.send_json(400, {"error": err})
            return

        async def run_workflow():
            results = []
            for cmd in commands:
                r = await run_command(cmd)
                results.append(r)
                if isinstance(r, dict) and r.get('error'):
                    break
            return results

        results = self.run_async(run_workflow(), timeout=300)
        self.send_json(200, {"workflow": name, "steps": len(commands), "results": results})

    # ── Helpers ──
    
    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[{now()}] ⚠️ Bad request body: {e}")
            return {}

    def _tab_params(self):
        """Read tabId from POST body if present. Returns {} for GET or absent tabId.

        Lets the same handler serve both GET (active tab) and POST (with
        {"tabId": N} to target a specific tab) without duplicating logic.
        """
        if self.command != 'POST':
            return {}
        body = self.read_body()
        tab_id = body.get('tabId')
        return {'tabId': tab_id} if tab_id is not None else {}
    
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
    loop = asyncio.get_running_loop()
    
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
    print(f"  GET  http://localhost:{HTTP_PORT}/api/version             - Bridge/extension version info")
    print(f"  GET  http://localhost:{HTTP_PORT}/api/workflows           - List available workflows")
    print(f"  POST http://localhost:{HTTP_PORT}/api/workflow            - Execute a named workflow")
    print(f"  POST http://localhost:{HTTP_PORT}/api/cache               - Cache control (clear/status/ttl)")
    print(f"  POST http://localhost:{HTTP_PORT}/api/wake-tab            - Reload a discarded tab and wait for ready")
    print(f"  POST http://localhost:{HTTP_PORT}/api/keep-alive          - Pin a list of tabs against the Tab Unloader")
    print(f"  POST http://localhost:{HTTP_PORT}/api/keep-alive-stop     - Stop keep-alive (omit tabIds to stop all)")
    print()
    
    # Keep running
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{now()}] Shutting down...")
