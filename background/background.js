// Claude Bridge for Zen - Background Script
// Connects to local bridge server via WebSocket, relays commands to content scripts

'use strict';

const EXPECTED_CONTENT_VERSION = 5;
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
    case 'navigate':     return await navigateTab(params.url, params.tabId);
    case 'switchTab':    return await switchTab(params.tabId);
    case 'newTab':       return await createTab(params.url);
    case 'closeTab':     return await closeTab(params.tabId);
    case 'getPageTextFromTab': return await forwardToContent('getPageText', params);
    
    // Content script commands (forwarded to active tab)
    case 'getPageInfo':
    case 'getPageText':
    case 'getAccessibilityTree':
    case 'getFormFields':
    case 'click':
    case 'type':
    case 'scroll':
    case 'hover':
    case 'fill':
    case 'find':
    case 'executeJS':
    case 'highlight':
    case 'clearHighlight':
    case 'waitForElement':
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

async function navigateTab(url, tabId) {
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

      function listener(updatedTabId, changeInfo) {
        if (updatedTabId === targetTabId && changeInfo.status === 'complete') {
          clearTimeout(timeout);
          browser.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      }
      browser.tabs.onUpdated.addListener(listener);
    });

    return { ok: true, url };
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

// == Init ==
connect();
browser.runtime.onStartup.addListener(connect);
browser.runtime.onInstalled.addListener(() => {
  console.log('[Claude Bridge] Extension installed');
  connect();
});
