// Claude Bridge for Zen - Background Script
// Connects to local bridge server via WebSocket, relays commands to content scripts

'use strict';

const EXPECTED_CONTENT_VERSION = 14;
const WS_URL = 'ws://127.0.0.1:8766';
let ws = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let reconnectDelay = 1000; // Start at 1s, exponential backoff
const MAX_RECONNECT_DELAY = 15000;

// == WebSocket Connection ==

function connect() {
  // Kill any existing connection first
  cleanup();
  
  try {
    ws = new WebSocket(WS_URL);
    
    ws.onopen = () => {
      console.log('[Claude Bridge] Connected to bridge server');
      reconnectDelay = 1000; // Reset backoff
      stopReconnect();
      startHeartbeat();
      injectAllTabs(); // Inject content scripts into all existing tabs
    };
    
    ws.onmessage = async (event) => {
      let cmdId = null;
      try {
        const command = JSON.parse(event.data);
        cmdId = command.id;
        console.log('[Claude Bridge] <<', command.action, cmdId);
        
        const result = await handleCommand(command);
        
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ id: cmdId, result }));
        }
      } catch (e) {
        console.error('[Claude Bridge] Command error:', e);
        if (cmdId && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ id: cmdId, result: { error: e.message } }));
        }
      }
    };
    
    ws.onclose = () => {
      console.log('[Claude Bridge] Disconnected');
      cleanup();
      scheduleReconnect();
    };
    
    ws.onerror = () => {
      console.error('[Claude Bridge] WebSocket error');
      // onclose will fire after this, which handles reconnect
    };
  } catch (e) {
    console.error('[Claude Bridge] Connection failed:', e);
    cleanup();
    scheduleReconnect();
  }
}

function cleanup() {
  stopHeartbeat();
  if (ws) {
    try { ws.close(); } catch {}
    ws = null;
  }
}

function scheduleReconnect() {
  stopReconnect();
  console.log(`[Claude Bridge] Reconnecting in ${reconnectDelay}ms...`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelay);
  // Exponential backoff capped at MAX
  reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_DELAY);
}

function stopReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

// Heartbeat: detect dead connections the browser hasn't noticed
function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.log('[Claude Bridge] Heartbeat: connection dead');
      cleanup();
      scheduleReconnect();
      return;
    }
    try {
      ws.send(JSON.stringify({ type: 'ping' }));
    } catch {
      cleanup();
      scheduleReconnect();
    }
  }, 10000); // Every 10s
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

// == Inject Content Scripts Into All Tabs ==

async function injectAllTabs() {
  try {
    const tabs = await browser.tabs.query({});
    let injected = 0, skipped = 0;
    for (const tab of tabs) {
      if (isRestrictedUrl(tab.url)) {
        skipped++;
        continue;
      }
      try {
        await browser.tabs.executeScript(tab.id, { file: '/content/content.js' });
        injected++;
      } catch {
        skipped++; // CSP or other restriction
      }
    }
    console.log(`[Claude Bridge] Injected content script: ${injected} tabs, ${skipped} skipped`);
  } catch (e) {
    console.error('[Claude Bridge] Tab injection error:', e);
  }
}

function isRestrictedUrl(url) {
  if (!url) return true;
  if (url === 'about:blank') return false;  // blank tabs may have loaded content - let sendMessage decide
  return url.startsWith('about:') ||
         url.startsWith('moz-extension:') ||
         url.startsWith('chrome:') ||
         url.startsWith('resource:') ||
         url.startsWith('view-source:') ||
         url.startsWith('data:');
}

// == Command Handler ==

async function handleCommand(command) {
  const { action, id, ...params } = command;
  
  switch (action) {
    // Browser-level commands (handled here)
    case 'screenshot':   return await captureScreenshot();
    case 'getTabs':      return await getOpenTabs();
    case 'navigate':     return await navigateTab(params.url, params.tabId, params.expectTitle);
    case 'switchTab':    return await switchTab(params.tabId);
    case 'newTab':       return await createTab(params.url);
    case 'closeTab':     return await closeTab(params.tabId);
    case 'wakeTab':      return await wakeTab(params.tabId);
    case 'reloadExtension': return reloadExtensionAction();
    case 'getPageTextFromTab': return await forwardToContent('getPageText', params);

    // 1.4.0 browser-level additions
    case 'pinTab':           return await updateTab(params.tabId, { pinned: !!params.pinned });
    case 'muteTab':          return await updateTab(params.tabId, { muted: !!params.muted });
    case 'duplicateTab':     return await duplicateTab(params.tabId);
    case 'reloadTabBrowser': return await reloadTabBrowser(params.tabId, params.bypassCache);
    case 'goBack':           return await goBack(params.tabId);
    case 'goForward':        return await goForward(params.tabId);
    case 'getZoom':          return await getZoom(params.tabId);
    case 'setZoom':          return await setZoom(params.tabId, params.factor);
    case 'getWindows':       return await getWindowsList();
    case 'createWindow':     return await createWindow(params.url, params.incognito);
    case 'closeWindow':      return await closeWindow(params.windowId);
    case 'focusWindow':      return await focusWindow(params.windowId);
    case 'moveTab':          return await moveTabAction(params.tabId, params.windowId, params.index);
    case 'detachTab':        return await detachTab(params.tabId);
    case 'elementScreenshot':return await elementScreenshot(params.tabId, params.selector);
    case 'fullPageScreenshot': return await fullPageScreenshot(params.tabId);
    case 'cookies':          return await cookiesOp(params.op, params.url, params.name, params.value, params.domain, params.path, params.secure);
    case 'clipboard':        return await clipboardOp(params.op, params.text);
    case 'downloads':        return await downloadsOp(params.op, params.url, params.filename, params.query);
    case 'clearBrowsingData':return await clearBrowsingData(params.types, params.since);
    case 'intercept':        return await interceptOp(params.op, params.patterns, params.effect);
    case 'clickAndWaitNavigation': return await clickAndWaitNavigation(params.tabId, params.selector, params.timeout);

    // Content script commands (forwarded to active tab)
    case 'getPageInfo':
    case 'getPageText':
    case 'getAccessibilityTree':
    case 'getFormFields':
    case 'click':
    case 'type':
    case 'setEditableContent':
    case 'scroll':
    case 'hover':
    case 'fill':
    case 'find':
    case 'executeJS':
    case 'highlight':
    case 'clearHighlight':
    case 'waitForElement':
    case 'waitForResult':
    // 1.4.0 forwarded actions
    case 'query':
    case 'getHTML':
    case 'getLinks':
    case 'getImages':
    case 'getMeta':
    case 'getStructuredData':
    case 'getBounds':
    case 'getComputedStyle':
    case 'getReadability':
    case 'getMarkdown':
    case 'selectOption':
    case 'checkBox':
    case 'focusElement':
    case 'blurElement':
    case 'keypress':
    case 'doubleClick':
    case 'submitForm':
    case 'formFill':
    case 'drag':
    case 'waitForUrl':
    case 'waitForTitle':
    case 'waitForNetworkIdle':
    case 'captureNetwork':
    case 'watchConsole':
    case 'consoleLogs':
    case 'storageOp':
    case 'getIframes':
    case 'explainSelector':
    case 'fullPageMetrics':
      return await forwardToContent(action, params);

    default:
      return { error: `Unknown action: ${action}` };
  }
}

// == Browser Commands ==

async function captureScreenshot() {
  try {
    const dataUrl = await browser.tabs.captureVisibleTab(null, { format: 'png', quality: 90 });
    const tabs = await browser.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    return {
      dataUrl,
      url: tab?.url || '',
      title: tab?.title || '',
      width: tab?.width || 0,
      height: tab?.height || 0
    };
  } catch (e) {
    return { error: `Screenshot failed: ${e.message}` };
  }
}

async function getOpenTabs() {
  try {
    const tabs = await browser.tabs.query({});
    return {
      tabs: tabs.map(t => ({
        id: t.id, url: t.url, title: t.title,
        active: t.active, windowId: t.windowId, index: t.index
      }))
    };
  } catch (e) {
    return { error: e.message };
  }
}

async function navigateTab(url, tabId, expectTitle) {
  try {
    const targetTabId = tabId || (await getActiveTabId());
    if (!targetTabId) return { error: 'No active tab' };
    await browser.tabs.update(targetTabId, { url });

    // Wait for page to finish loading before returning
    await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        browser.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 15000);

      function listener(updatedTabId, changeInfo, tab) {
        if (updatedTabId === targetTabId && changeInfo.status === 'complete' && tab.url && !tab.url.startsWith('about:')) {
          clearTimeout(timeout);
          browser.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      }
      browser.tabs.onUpdated.addListener(listener);
    });

    // Check if page title matches expectation (catches silent redirects)
    const result = { ok: true, url };
    if (expectTitle) {
      const tab = await browser.tabs.get(targetTabId);
      const title = tab.title || '';
      const match = title.toLowerCase().includes(expectTitle.toLowerCase());
      result.title = title;
      if (!match) {
        result.warning = `Title "${title}" does not contain expected "${expectTitle}" — possible redirect`;
        result.redirected = true;
      }
    }
    return result;
  } catch (e) {
    return { error: e.message };
  }
}

async function switchTab(tabId) {
  try {
    await browser.tabs.update(tabId, { active: true });
    return { ok: true };
  } catch (e) {
    return { error: e.message };
  }
}

async function createTab(url) {
  try {
    const tab = await browser.tabs.create({ url: url || 'about:blank' });
    return { ok: true, tabId: tab.id };
  } catch (e) {
    return { error: e.message };
  }
}

async function closeTab(tabId) {
  try {
    await browser.tabs.remove(tabId);
    return { ok: true };
  } catch (e) {
    return { error: e.message };
  }
}

function reloadExtensionAction() {
  // Defer the reload so we can ack first — the WS dies the moment we reload,
  // and the bridge's pending future would otherwise hang. The new background
  // script comes up cold and reconnects via the existing onclose backoff.
  setTimeout(() => {
    try { browser.runtime.reload(); } catch (e) { console.error('[Claude Bridge] Reload failed:', e); }
  }, 100);
  return { ok: true, reloading: true };
}

async function wakeTab(tabId) {
  // Reload a discarded tab and wait for it to be ready. Idempotent — returns
  // immediately if the tab is already alive. Use before sending commands to a
  // tab that may have been idle long enough for Zen's Tab Unloader to discard.
  if (!tabId) return { error: 'wakeTab requires tabId' };
  try {
    const tab = await browser.tabs.get(tabId);
    if (!tab.discarded) {
      return { ok: true, woken: false, discarded: false };
    }
    await browser.tabs.reload(tabId);
    const ready = await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        browser.tabs.onUpdated.removeListener(listener);
        resolve(false);
      }, 15000);
      function listener(updatedTabId, changeInfo) {
        if (updatedTabId === tabId && changeInfo.status === 'complete') {
          clearTimeout(timeout);
          browser.tabs.onUpdated.removeListener(listener);
          resolve(true);
        }
      }
      browser.tabs.onUpdated.addListener(listener);
    });
    return { ok: true, woken: true, ready };
  } catch (e) {
    return { error: e.message };
  }
}

// == Forward to Content Script ==

async function forwardToContent(action, params) {
  const tabId = params.tabId || (await getActiveTabId());
  if (!tabId) return { error: 'No active tab found' };
  
  // Check for restricted pages
  try {
    const tab = await browser.tabs.get(tabId);
    if (isRestrictedUrl(tab.url)) {
      return { error: `Cannot access browser internal page: ${tab.url.split('?')[0]}` };
    }
  } catch {}
  
  // Check if content script is loaded and up-to-date
  let needsInject = false;
  try {
    const ping = await browser.tabs.sendMessage(tabId, { action: 'ping' });
    if (!ping || ping.version < EXPECTED_CONTENT_VERSION) needsInject = true;
  } catch {
    needsInject = true;
  }

  if (needsInject) {
    try {
      await browser.tabs.executeScript(tabId, { file: '/content/content.js' });
      await new Promise(r => setTimeout(r, 150));
    } catch (e) {
      return { error: `Cannot inject content script: ${e.message}` };
    }
  }

  try {
    const response = await browser.tabs.sendMessage(tabId, { action, ...params });
    if (response?.error?.includes('HTMLInputElement') || response?.error?.includes('HTMLTextAreaElement')) {
      if (action === 'fill' && params.selector && params.value != null) {
        return await fillViaExecScript(tabId, params.selector, String(params.value));
      }
    }
    return response || { error: 'No response from content script' };
  } catch (e) {
    return { error: `Content script error: ${e.message}` };
  }
}

async function getActiveTabId() {
  const tabs = await browser.tabs.query({ active: true, currentWindow: true });
  return tabs[0]?.id || null;
}

async function fillViaExecScript(tabId, selector, value) {
  try {
    const escaped = (s) => s.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const sel = escaped(selector);
    const val = escaped(value);
    const code = "(function(){const el=document.querySelector('" + sel + "');if(!el)return 'not found';const target=el.shadowRoot?(el.shadowRoot.querySelector('input,textarea,select')||el):(el.querySelector('input,textarea,select')||el);target.focus();const proto=target.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,'value');if(setter&&setter.set)setter.set.call(target,'" + val + "');else target.value='" + val + "';target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));return 'ok:'+target.tagName.toLowerCase();})();";
    const results = await browser.tabs.executeScript(tabId, { code });
    const r = results?.[0];
    if (r && r.startsWith('ok:')) {
      return { ok: true, pierced: true, method: 'execScript', tag: r.split(':')[1] };
    }
    return { error: r || 'fillViaExecScript failed' };
  } catch (e) {
    return { error: 'execScript fill error: ' + e.message };
  }
}

// ══════════════════════════════════════════════════════════════
// 1.4.0 — browser-level handler implementations
// ══════════════════════════════════════════════════════════════

async function updateTab(tabId, props) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    const tab = await browser.tabs.update(id, props);
    return { ok: true, tabId: id, pinned: tab.pinned, muted: tab.mutedInfo?.muted };
  } catch (e) { return { error: e.message }; }
}

async function duplicateTab(tabId) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    const dup = await browser.tabs.duplicate(id);
    return { ok: true, tabId: dup.id, sourceTabId: id };
  } catch (e) { return { error: e.message }; }
}

async function reloadTabBrowser(tabId, bypassCache) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    await browser.tabs.reload(id, { bypassCache: !!bypassCache });
    return { ok: true, tabId: id, bypassCache: !!bypassCache };
  } catch (e) { return { error: e.message }; }
}

async function goBack(tabId) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    await browser.tabs.goBack(id);
    return { ok: true, tabId: id };
  } catch (e) { return { error: e.message }; }
}

async function goForward(tabId) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    await browser.tabs.goForward(id);
    return { ok: true, tabId: id };
  } catch (e) { return { error: e.message }; }
}

async function getZoom(tabId) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    const factor = await browser.tabs.getZoom(id);
    return { ok: true, tabId: id, factor };
  } catch (e) { return { error: e.message }; }
}

async function setZoom(tabId, factor) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    await browser.tabs.setZoom(id, Number(factor) || 1);
    return { ok: true, tabId: id, factor: Number(factor) || 1 };
  } catch (e) { return { error: e.message }; }
}

async function getWindowsList() {
  try {
    const wins = await browser.windows.getAll({ populate: true });
    return {
      count: wins.length,
      windows: wins.map(w => ({
        id: w.id, focused: w.focused, state: w.state, type: w.type,
        width: w.width, height: w.height, left: w.left, top: w.top,
        incognito: w.incognito,
        tabs: (w.tabs || []).map(t => ({ id: t.id, url: t.url, title: t.title, active: t.active, pinned: t.pinned })),
      })),
    };
  } catch (e) { return { error: e.message }; }
}

async function createWindow(url, incognito) {
  try {
    const w = await browser.windows.create({
      url: url ? (Array.isArray(url) ? url : [url]) : undefined,
      incognito: !!incognito,
    });
    return { ok: true, windowId: w.id, tabIds: (w.tabs || []).map(t => t.id) };
  } catch (e) { return { error: e.message }; }
}

async function closeWindow(windowId) {
  try {
    await browser.windows.remove(windowId);
    return { ok: true, windowId };
  } catch (e) { return { error: e.message }; }
}

async function focusWindow(windowId) {
  try {
    await browser.windows.update(windowId, { focused: true });
    return { ok: true, windowId };
  } catch (e) { return { error: e.message }; }
}

async function moveTabAction(tabId, windowId, index) {
  try {
    const moved = await browser.tabs.move(tabId, { windowId, index: index ?? -1 });
    return { ok: true, tabId, windowId, index: Array.isArray(moved) ? moved[0]?.index : moved.index };
  } catch (e) { return { error: e.message }; }
}

async function detachTab(tabId) {
  try {
    const w = await browser.windows.create({ tabId });
    return { ok: true, windowId: w.id, tabId };
  } catch (e) { return { error: e.message }; }
}

async function activateTabForCapture(tabId) {
  const tab = await browser.tabs.get(tabId);
  const activeTabs = await browser.tabs.query({ windowId: tab.windowId, active: true });
  const previousTabId = activeTabs[0]?.id !== tabId ? activeTabs[0]?.id : null;
  if (previousTabId) {
    await browser.tabs.update(tabId, { active: true });
    await new Promise(r => setTimeout(r, 100));
  }
  return { tab, previousTabId };
}

async function restoreTabAfterCapture(previousTabId) {
  if (!previousTabId) return;
  try { await browser.tabs.update(previousTabId, { active: true }); } catch {}
}

// ── Element / full-page screenshot ──
async function elementScreenshot(tabId, selector) {
  let previousTabId = null;
  try {
    const id = tabId || (await getActiveTabId());
    if (!id || !selector) return { error: 'tabId and selector required' };
    const tab = await browser.tabs.get(id);
    if (isRestrictedUrl(tab.url)) return { error: 'Cannot access browser internal page' };
    ({ previousTabId } = await activateTabForCapture(id));
    const bounds = await forwardToContent('getBounds', { tabId: id, selector });
    if (bounds.error) return bounds;
    // Scroll element into view first
    await forwardToContent('scroll', { tabId: id, direction: 'top' });
    await forwardToContent('executeJS', {
      tabId: id,
      code: `(()=>{const e=document.querySelector(${JSON.stringify(selector)}); if(e)e.scrollIntoView({block:'center',inline:'center',behavior:'instant'}); return e?.getBoundingClientRect();})()`,
    });
    // Take a viewport screenshot
    const dataUrl = await browser.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
    // Get fresh bounds after scroll
    const b2 = await forwardToContent('getBounds', { tabId: id, selector });
    const dpr = b2.viewport?.dpr || 1;
    // Crop using OffscreenCanvas (extension context)
    const img = await loadDataUrl(dataUrl);
    const canvas = new OffscreenCanvas(Math.round(b2.width), Math.round(b2.height));
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, Math.round(b2.x * dpr), Math.round(b2.y * dpr), Math.round(b2.width * dpr), Math.round(b2.height * dpr), 0, 0, Math.round(b2.width), Math.round(b2.height));
    const blob = await canvas.convertToBlob({ type: 'image/png' });
    const buf = await blob.arrayBuffer();
    const cropped = arrayBufferToDataUrl(buf, 'image/png');
    return { ok: true, selector, bounds: { x: b2.x, y: b2.y, width: b2.width, height: b2.height }, dataUrl: cropped };
  } catch (e) { return { error: 'elementScreenshot failed: ' + e.message }; }
  finally { await restoreTabAfterCapture(previousTabId); }
}

async function fullPageScreenshot(tabId) {
  let previousTabId = null;
  try {
    const id = tabId || (await getActiveTabId());
    if (!id) return { error: 'No tab' };
    const tab = await browser.tabs.get(id);
    if (isRestrictedUrl(tab.url)) return { error: 'Cannot access browser internal page' };
    ({ previousTabId } = await activateTabForCapture(id));
    const m = await forwardToContent('fullPageMetrics', { tabId: id });
    if (m.error) return m;
    const segments = [];
    let y = 0;
    const guard = 30; // Max 30 viewports tall
    let i = 0;
    while (y < m.docHeight && i < guard) {
      await forwardToContent('executeJS', { tabId: id, code: `window.scrollTo(0, ${y}); 1` });
      await new Promise(r => setTimeout(r, 120)); // Let lazy content settle
      const data = await browser.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
      segments.push({ y, data });
      y += m.viewportHeight;
      i++;
    }
    // Stitch with OffscreenCanvas
    const canvas = new OffscreenCanvas(Math.round(m.docWidth), Math.round(m.docHeight));
    const ctx = canvas.getContext('2d');
    for (const seg of segments) {
      const img = await loadDataUrl(seg.data);
      const drawH = Math.min(m.viewportHeight, m.docHeight - seg.y);
      ctx.drawImage(img, 0, 0, img.width, drawH * (img.height / m.viewportHeight), 0, seg.y, img.width, drawH);
    }
    const blob = await canvas.convertToBlob({ type: 'image/png' });
    const buf = await blob.arrayBuffer();
    return { ok: true, width: m.docWidth, height: m.docHeight, segments: segments.length, dataUrl: arrayBufferToDataUrl(buf, 'image/png') };
  } catch (e) { return { error: 'fullPageScreenshot failed: ' + e.message }; }
  finally { await restoreTabAfterCapture(previousTabId); }
}

async function loadDataUrl(dataUrl) {
  const resp = await fetch(dataUrl);
  const blob = await resp.blob();
  return await createImageBitmap(blob);
}

function arrayBufferToDataUrl(buf, mime) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
  return 'data:' + mime + ';base64,' + btoa(bin);
}

// ── Cookies ──
async function cookiesOp(op, url, name, value, domain, path, secure) {
  try {
    if (op === 'get') {
      const all = await browser.cookies.getAll({ url, domain, name });
      return { ok: true, count: all.length, cookies: all };
    }
    if (op === 'set') {
      const c = await browser.cookies.set({ url, name, value: String(value), domain, path, secure: !!secure });
      return { ok: true, cookie: c };
    }
    if (op === 'remove') {
      const r = await browser.cookies.remove({ url, name });
      return { ok: true, removed: !!r };
    }
    if (op === 'clear') {
      const all = await browser.cookies.getAll({ url, domain });
      for (const c of all) {
        const u = (c.secure ? 'https://' : 'http://') + c.domain.replace(/^\./, '') + c.path;
        try { await browser.cookies.remove({ url: u, name: c.name }); } catch {}
      }
      return { ok: true, removed: all.length };
    }
    return { error: 'Unknown cookies op: ' + op };
  } catch (e) { return { error: e.message }; }
}

// ── Clipboard ──
async function clipboardOp(op, text) {
  try {
    if (op === 'read') {
      const t = await navigator.clipboard.readText();
      return { ok: true, text: t };
    }
    if (op === 'write') {
      await navigator.clipboard.writeText(String(text ?? ''));
      return { ok: true, length: String(text ?? '').length };
    }
    return { error: 'Unknown clipboard op: ' + op };
  } catch (e) { return { error: 'clipboard ' + op + ' failed: ' + e.message }; }
}

// ── Downloads ──
async function downloadsOp(op, url, filename, query) {
  try {
    if (op === 'download') {
      const id = await browser.downloads.download({ url, filename, conflictAction: 'uniquify' });
      return { ok: true, downloadId: id };
    }
    if (op === 'list') {
      const items = await browser.downloads.search(query || { limit: 20, orderBy: ['-startTime'] });
      return { ok: true, count: items.length, items };
    }
    if (op === 'cancel') {
      await browser.downloads.cancel(query?.id);
      return { ok: true };
    }
    return { error: 'Unknown downloads op: ' + op };
  } catch (e) { return { error: e.message }; }
}

// ── Browsing data ──
async function clearBrowsingData(types, since) {
  try {
    const opts = since ? { since } : {};
    const dataTypes = {};
    for (const t of (types || ['cache','cookies','history','localStorage','passwords','downloads'])) dataTypes[t] = true;
    await browser.browsingData.remove(opts, dataTypes);
    return { ok: true, cleared: Object.keys(dataTypes), since };
  } catch (e) { return { error: e.message }; }
}

// ── Request interception ──
const __intercept = { rules: [], listening: false, requestLog: [], maxLog: 500 };

function interceptListener(details) {
  __intercept.requestLog.push({
    time: Date.now(),
    url: details.url,
    method: details.method,
    tabId: details.tabId,
    type: details.type,
  });
  if (__intercept.requestLog.length > __intercept.maxLog) __intercept.requestLog.shift();
  for (const rule of __intercept.rules) {
    try {
      if (new RegExp(rule.pattern).test(details.url)) {
        if (rule.action === 'block') return { cancel: true };
        if (rule.action === 'redirect' && rule.target) return { redirectUrl: rule.target };
      }
    } catch {}
  }
  return {};
}

async function interceptOp(op, patterns, effect) {
  try {
    if (op === 'add') {
      for (const p of (patterns || [])) __intercept.rules.push({ pattern: p, action: effect || 'block' });
      ensureInterceptListener();
      return { ok: true, rules: __intercept.rules.length };
    }
    if (op === 'clear') {
      __intercept.rules.length = 0;
      removeInterceptListener();
      return { ok: true, rules: 0 };
    }
    if (op === 'list') {
      return { ok: true, rules: __intercept.rules.slice(), logged: __intercept.requestLog.length };
    }
    if (op === 'log') {
      return { ok: true, count: __intercept.requestLog.length, requests: __intercept.requestLog.slice(-200) };
    }
    if (op === 'clearLog') {
      __intercept.requestLog.length = 0;
      return { ok: true };
    }
    return { error: 'Unknown intercept op: ' + op };
  } catch (e) { return { error: e.message }; }
}

function ensureInterceptListener() {
  if (__intercept.listening) return;
  try {
    browser.webRequest.onBeforeRequest.addListener(interceptListener, { urls: ['<all_urls>'] }, ['blocking']);
    __intercept.listening = true;
  } catch (e) {
    console.error('[Claude Bridge] webRequest unavailable:', e);
  }
}

function removeInterceptListener() {
  if (!__intercept.listening) return;
  try {
    browser.webRequest.onBeforeRequest.removeListener(interceptListener);
    __intercept.listening = false;
  } catch {}
}

// ── Click and wait for navigation ──
async function clickAndWaitNavigation(tabId, selector, timeout) {
  try {
    const id = tabId || (await getActiveTabId());
    if (!id || !selector) return { error: 'tabId and selector required' };
    const t0 = Date.now();
    const navPromise = new Promise((resolve) => {
      const max = timeout || 15000;
      const to = setTimeout(() => { browser.tabs.onUpdated.removeListener(listener); resolve({ ok: false, timedOut: true }); }, max);
      function listener(updatedTabId, changeInfo, tab) {
        if (updatedTabId === id && changeInfo.status === 'complete') {
          clearTimeout(to);
          browser.tabs.onUpdated.removeListener(listener);
          resolve({ ok: true, url: tab.url, title: tab.title, elapsed: Date.now() - t0 });
        }
      }
      browser.tabs.onUpdated.addListener(listener);
    });
    const click = await forwardToContent('click', { tabId: id, selector });
    if (click.error) return { ...click, clicked: false };
    const nav = await navPromise;
    return { ok: true, clicked: true, navigation: nav };
  } catch (e) { return { error: e.message }; }
}

// == Init ==
connect();
browser.runtime.onStartup.addListener(connect);
browser.runtime.onInstalled.addListener(() => {
  console.log('[Claude Bridge] Extension installed');
  connect();
});
