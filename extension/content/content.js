// Claude Bridge for Zen - Content Script
// Handles all DOM interaction on web pages

(function() {
  'use strict';
  const CONTENT_VERSION = 11;
  if (window.__claudeBridgeVersion >= CONTENT_VERSION) return;
  window.__claudeBridgeVersion = CONTENT_VERSION;

  // Element reference map (capped to prevent memory leaks in long sessions)
  const REF_MAP_LIMIT = 10000;
  const refMap = {};
  let refCounter = 0;

  function pruneRefMap() {
    const keys = Object.keys(refMap);
    if (keys.length > REF_MAP_LIMIT) {
      // Drop oldest half
      keys.slice(0, Math.floor(keys.length / 2)).forEach(k => delete refMap[k]);
    }
  }

  // ── Message Handler ──
  browser.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    const handlers = {
      'ping':                  () => ({ ok: true, version: CONTENT_VERSION }),
      'getPageInfo':           () => getPageInfo(),
      'getPageText':           () => getPageText(msg.selector),
      'getAccessibilityTree':  () => getAccessibilityTree(msg.depth || 6),
      'getFormFields':         () => getFormFields(),
      'click':                 () => clickElement(msg.selector, msg.coords),
      'type':                  () => typeText(msg.selector, msg.text, msg.clear),
      'scroll':                () => scrollPage(msg.direction, msg.amount, msg.selector),
      'hover':                 () => hoverElement(msg.selector, msg.coords),
      'fill':                  () => fillFieldSafe(msg.selector, msg.value),
      'find':                  () => findElements(msg.query),
      'executeJS':             () => executeJS(msg.code),
      'highlight':             () => highlightElement(msg.selector),
      'clearHighlight':        () => clearHighlight(),
      'waitForElement':        () => waitForElement(msg.selector, msg.timeout, msg.pollInterval),
      'waitForResult':         () => waitForResult(msg.code, msg.timeout, msg.pollInterval),
    };

    // Only respond if we're the latest version
    if (window.__claudeBridgeVersion !== CONTENT_VERSION) return false;
    const handler = handlers[msg.action];
    if (!handler) return false;

    try {
      const result = handler();
      if (result && typeof result.then === 'function') {
        // Async handler — keep message channel open
        result.then(r => sendResponse(r)).catch(e => sendResponse({ error: e.message }));
        return true;
      }
      sendResponse(result);
    } catch (e) {
      sendResponse({ error: e.message });
    }
    return false;
  });

  // ── Page Info ──
  function getPageInfo() {
    return {
      url: location.href,
      title: document.title,
      lang: document.documentElement.lang || 'unknown',
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
      viewportW: window.innerWidth,
      viewportH: window.innerHeight,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      forms: document.forms.length,
      links: document.links.length,
      images: document.images.length,
    };
  }

  // ── Page Text ──
  const PAGE_TEXT_LIMIT = 20000;

  function truncateText(raw) {
    const truncated = raw.length > PAGE_TEXT_LIMIT;
    return {
      text: truncated ? raw.slice(0, PAGE_TEXT_LIMIT) : raw,
      ...(truncated && { truncated: true, fullLength: raw.length }),
    };
  }

  function getPageText(selector) {
    if (selector) {
      const el = document.querySelector(selector);
      if (!el) return { error: 'Selector not found: ' + selector };
      return truncateText(el.innerText);
    }
    const selectors = ['article', '[role="main"]', 'main', '.content', '.post-content', '.article-body', '.entry-content'];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText.length > 100) {
        return truncateText(el.innerText);
      }
    }
    return truncateText(document.body.innerText);
  }

  // ── Accessibility Tree ──
  function getAccessibilityTree(maxDepth) {
    pruneRefMap();
    refCounter = 0;
    
    function buildNode(el, depth) {
      if (depth > maxDepth || !el || el.nodeType !== 1) return null;
      if (isHidden(el)) return null;

      const tag = el.tagName.toLowerCase();
      // Skip noise
      if (['script', 'style', 'noscript', 'svg', 'path', 'br', 'hr'].includes(tag)) return null;

      const rect = el.getBoundingClientRect();
      const isInteractive = isInteractiveElement(el);
      const text = directText(el);

      // Skip non-interactive elements with no text and small size
      if (!isInteractive && !text && rect.width < 5 && rect.height < 5) return null;

      const ref = `r${refCounter++}`;
      refMap[ref] = el;

      const node = { ref, tag };
      
      // Only include useful properties
      if (el.id) node.id = el.id;
      if (isInteractive) {
        node.interactive = true;
        const role = el.getAttribute('role') || inferRole(el);
        if (role) node.role = role;
      }
      if (text) node.text = text;
      if (el.name) node.name = el.name;
      if (el.type && el.tagName === 'INPUT') node.type = el.type;
      if (el.value && isInteractive) node.value = String(el.value).slice(0, 80);
      if (el.placeholder) node.placeholder = el.placeholder;
      if (el.href) node.href = el.href.slice(0, 200);
      if (el.ariaLabel) node.label = el.ariaLabel;
      if (el.disabled) node.disabled = true;
      if (el.checked !== undefined) node.checked = el.checked;

      // Bounds only for interactive or visible elements
      if (isInteractive || text) {
        node.bounds = [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)];
      }

      // Recurse children
      const children = [];
      for (const child of el.children) {
        const childNode = buildNode(child, depth + 1);
        if (childNode) children.push(childNode);
      }
      if (children.length) node.children = children;

      return node;
    }

    const tree = buildNode(document.body, 0);
    return { tree, refs: refCounter };
  }

  // ── Find Elements ──
  function findElements(query) {
    pruneRefMap();
    const q = query.toLowerCase();
    const results = [];

    // Try as CSS selector
    try {
      const matches = document.querySelectorAll(query);
      if (matches.length > 0 && matches.length < 50) {
        matches.forEach(el => {
          if (!isHidden(el)) results.push(makeResult(el));
        });
        if (results.length) return { results: results.slice(0, 20) };
      }
    } catch {}

    // Text/attribute search
    const candidates = document.querySelectorAll('a, button, input, select, textarea, [role], [onclick], label, h1, h2, h3, h4, h5, h6, p, span, div, li, td, th, img');
    for (const el of candidates) {
      if (isHidden(el)) continue;
      const score = scoreMatch(el, q);
      if (score > 0) results.push({ ...makeResult(el), score });
    }

    results.sort((a, b) => b.score - a.score);
    return { results: results.slice(0, 20) };
  }

  function makeResult(el) {
    const ref = `r${refCounter++}`;
    refMap[ref] = el;
    const rect = el.getBoundingClientRect();
    return {
      ref,
      tag: el.tagName.toLowerCase(),
      text: (el.textContent || '').trim().slice(0, 120),
      selector: buildSelector(el),
      bounds: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
      interactive: isInteractiveElement(el)
    };
  }

  function scoreMatch(el, query) {
    let score = 0;
    const fields = [
      [el.textContent, 2],
      [el.ariaLabel, 5],
      [el.placeholder, 4],
      [el.title, 3],
      [el.alt, 3],
      [el.id, 3],
      [el.name, 2],
      [el.value, 2],
    ];
    for (const [val, weight] of fields) {
      if (val && String(val).toLowerCase().includes(query)) score += weight;
    }
    if (isInteractiveElement(el)) score += 1;
    return score;
  }

  // ── Click ──
  function clickElement(selector, coords) {
    const el = resolveElement(selector, coords);
    if (!el) return { error: 'Element not found' };

    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    
    // Small delay for scroll, then click
    const rect = el.getBoundingClientRect();
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;

    // Full mouse event sequence
    for (const type of ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, {
        bubbles: true, cancelable: true, view: window,
        clientX: x, clientY: y
      }));
    }

    // Also try .click() for stubborn elements
    if (typeof el.click === 'function') el.click();

    return { ok: true, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().slice(0, 60) };
  }

  // ── Type ──
  function typeText(selector, text, clear) {
    const el = resolveElement(selector);
    if (!el) return { error: 'Element not found' };

    el.focus();
    
    if (clear) {
      // Use native value setter to properly trigger React/Vue state
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) {
        setter.call(el, text);
      } else {
        el.value = text;
      }
    } else {
      if (el.contentEditable === 'true') {
        document.execCommand('insertText', false, text);
        return { ok: true };
      }
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) {
        setter.call(el, el.value + text);
      } else {
        el.value += text;
      }
    }

    // Fire all the events React/Vue/Angular listen for
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));

    return { ok: true };
  }

  // ── Scroll ──
  function scrollPage(direction, amount, selector) {
    const target = selector ? resolveElement(selector) : null;
    // amount now means viewport-heights (1 = one full viewport, default = 1)
    // This ensures lazy-loading intersection observers actually trigger
    const viewportH = window.innerHeight;
    const dist = Math.round((amount || 1) * viewportH);
    const opts = { behavior: 'smooth' };

    if (direction === 'top') {
      (target || window).scrollTo({ top: 0, ...opts });
    } else if (direction === 'bottom') {
      (target || window).scrollTo({ top: document.body.scrollHeight, ...opts });
    } else {
      const deltas = { up: { top: -dist }, down: { top: dist }, left: { left: -dist }, right: { left: dist } };
      (target || window).scrollBy({ ...deltas[direction || 'down'], ...opts });
    }

    return { ok: true, scrollY: window.scrollY, scrollX: window.scrollX, scrolledPx: dist };
  }

  // ── Hover ──
  function hoverElement(selector, coords) {
    const el = resolveElement(selector, coords);
    if (!el) return { error: 'Element not found' };

    const rect = el.getBoundingClientRect();
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;

    for (const type of ['mouseenter', 'mouseover', 'mousemove']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
    }

    return { ok: true, tag: el.tagName.toLowerCase() };
  }

  // ── Fill Form ──
  async function fillFieldSafe(selector, value) {
    let result;
    try { result = fillField(selector, value); } catch(e) { result = { error: e.message }; }
    if (result.error && (result.error.includes('HTMLInputElement') || result.error.includes('HTMLTextAreaElement'))) {
      // Shadow DOM not accessible from content script — use page context
      const pageResult = await fillViaPageContext(selector, value);
      if (pageResult.startsWith('ok:')) {
        return { ok: true, pierced: true, method: 'pageContext', tag: pageResult.split(':')[1].toLowerCase() };
      }
      return { error: pageResult };
    }
    return result;
  }

  function fillField(selector, value) {
    const el = resolveElement(selector);
    if (!el) return { error: 'Element not found' };

    // Auto-pierce shadow DOM: if element isn't a standard input, look inside shadow root
    const target = getFillTarget(el);

    if (target.type === 'checkbox' || target.type === 'radio') {
      target.checked = !!value;
      target.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (target.tagName === 'SELECT') {
      target.value = value;
      target.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      // Use native setter to trigger React/Vue/custom element state
      target.focus();
      const proto = target.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) {
        setter.call(target, String(value));
      } else {
        target.value = String(value);
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return { ok: true, pierced: target !== el, tag: target.tagName.toLowerCase() };
  }

  function fillViaPageContext(selector, value) {
    // Last resort: inject script into page context to pierce closed shadow DOM
    // Uses JSON.stringify to safely embed user input and prevent template injection
    return new Promise((resolve) => {
      const script = document.createElement('script');
      const callbackName = '__claudeFill_' + Date.now();
      const safeSelector = JSON.stringify(selector);
      const safeValue = JSON.stringify(String(value));
      const safeCb = JSON.stringify(callbackName);
      script.textContent = '(function(){' +
        'var el=document.querySelector(' + safeSelector + ');' +
        'if(!el){window[' + safeCb + ']="not found";return;}' +
        'var target=el.shadowRoot?(el.shadowRoot.querySelector("input,textarea,select")||el):(el.querySelector("input,textarea,select")||el);' +
        'target.focus();' +
        'var proto=target.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;' +
        'var setter=Object.getOwnPropertyDescriptor(proto,"value");' +
        'if(setter&&setter.set)setter.set.call(target,' + safeValue + ');' +
        'else target.value=' + safeValue + ';' +
        'target.dispatchEvent(new Event("input",{bubbles:true}));' +
        'target.dispatchEvent(new Event("change",{bubbles:true}));' +
        'window[' + safeCb + ']="ok:"+target.tagName;' +
      '})();';
      document.documentElement.appendChild(script);
      script.remove();
      const result = window.wrappedJSObject?.[callbackName] || window[callbackName] || 'unknown';
      resolve(result);
    });
  }

  function getFillTarget(el) {
    const tag = el.tagName.toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag)) return el;

    // Open shadow root
    if (el.shadowRoot) {
      const inner = el.shadowRoot.querySelector('input, textarea, select');
      if (inner) return inner;
    }

    // Regular children
    const child = el.querySelector('input, textarea, select');
    if (child) return child;

    // Closed shadow root — try page context access via wrappedJSObject (Firefox)
    try {
      const unwrapped = el.wrappedJSObject || el;
      if (unwrapped.shadowRoot) {
        const inner = unwrapped.shadowRoot.querySelector('input, textarea, select');
        if (inner) return XPCNativeWrapper(inner);
      }
    } catch {}

    return el;
  }

  // ── Get Form Fields ──
  function getFormFields() {
    const fields = [];
    document.querySelectorAll('input, select, textarea').forEach(el => {
      if (isHidden(el)) return;
      const rect = el.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) return;

      const ref = `r${refCounter++}`;
      refMap[ref] = el;

      fields.push({
        ref,
        tag: el.tagName.toLowerCase(),
        type: el.type || 'text',
        name: el.name || '',
        id: el.id || '',
        placeholder: el.placeholder || '',
        value: (el.value || '').slice(0, 100),
        label: findLabel(el),
        required: el.required,
        selector: buildSelector(el),
        bounds: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]
      });
    });
    return { fields };
  }

  // ── Execute JS ──
  const JS_RESULT_LIMIT = 50000; // 50KB cap to avoid blowing up WebSocket

  function executeJS(code) {
    try {
      const result = eval(code);
      const str = String(result ?? '');
      const truncated = str.length > JS_RESULT_LIMIT;
      return {
        result: truncated ? str.slice(0, JS_RESULT_LIMIT) : str,
        ...(truncated && { truncated: true, fullLength: str.length }),
      };
    } catch (e) {
      return { error: e.message };
    }
  }

  // ── Wait For Element ──
  function waitForElement(selector, timeout, pollInterval) {
    const maxWait = timeout || 10000;
    const interval = pollInterval || 200;
    const start = Date.now();

    return new Promise((resolve) => {
      function poll() {
        const el = document.querySelector(selector);
        if (el && !isHidden(el)) {
          const elapsed = Date.now() - start;
          const ref = `r${refCounter++}`;
          refMap[ref] = el;
          resolve({
            ok: true,
            found: true,
            selector,
            elapsed,
            ref,
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').trim().slice(0, 120),
          });
        } else if (Date.now() - start >= maxWait) {
          resolve({
            ok: false,
            found: false,
            selector,
            elapsed: Date.now() - start,
            error: `Timed out after ${maxWait}ms waiting for "${selector}"`,
          });
        } else {
          setTimeout(poll, interval);
        }
      }
      poll();
    });
  }

  // ── Wait For Result (poll JS expression until non-empty) ──
  function waitForResult(code, timeout, pollInterval) {
    const maxWait = timeout || 15000;
    const interval = pollInterval || 500;
    const start = Date.now();

    return new Promise((resolve) => {
      function poll() {
        try {
          const raw = eval(code);
          const result = String(raw ?? '');
          // Treat non-empty, non-null, non-undefined, non-"[]", non-"null" as success
          if (result && result !== 'undefined' && result !== 'null' && result !== '[]' && result !== '""') {
            resolve({
              ok: true,
              result: result.slice(0, JS_RESULT_LIMIT),
              elapsed: Date.now() - start,
              polls: Math.floor((Date.now() - start) / interval) + 1,
            });
            return;
          }
        } catch (e) {
          // Expression threw — keep polling (page may still be hydrating)
        }

        if (Date.now() - start >= maxWait) {
          resolve({
            ok: false,
            result: null,
            elapsed: Date.now() - start,
            error: `Timed out after ${maxWait}ms — expression never returned non-empty result`,
          });
        } else {
          setTimeout(poll, interval);
        }
      }
      poll();
    });
  }

  // ── Highlight ──
  let highlightOverlay = null;

  function highlightElement(selector) {
    clearHighlight();
    const el = resolveElement(selector);
    if (!el) return { error: 'Element not found' };

    const rect = el.getBoundingClientRect();
    highlightOverlay = document.createElement('div');
    Object.assign(highlightOverlay.style, {
      position: 'fixed',
      left: rect.x + 'px', top: rect.y + 'px',
      width: rect.width + 'px', height: rect.height + 'px',
      border: '2px solid #e94560',
      background: 'rgba(233, 69, 96, 0.15)',
      pointerEvents: 'none',
      zIndex: '2147483647',
      borderRadius: '3px'
    });
    document.body.appendChild(highlightOverlay);
    return { ok: true };
  }

  function clearHighlight() {
    if (highlightOverlay) { highlightOverlay.remove(); highlightOverlay = null; }
    return { ok: true };
  }

  // ── Helpers ──

  function resolveElement(selector, coords) {
    if (!selector && coords) return document.elementFromPoint(coords.x, coords.y);
    if (!selector) return null;
    
    // Try ref map first
    if (selector.startsWith('r') && refMap[selector]) return refMap[selector];
    
    // Try CSS selector
    try { return document.querySelector(selector); } catch {}
    return null;
  }

  function isHidden(el) {
    if (!el || el.hidden) return true;
    const s = window.getComputedStyle(el);
    return s.display === 'none' || s.visibility === 'hidden';
  }

  function isInteractiveElement(el) {
    const interactiveTags = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'DETAILS', 'SUMMARY']);
    if (interactiveTags.has(el.tagName)) return true;
    if (el.onclick || el.hasAttribute('tabindex') || el.contentEditable === 'true') return true;
    const role = el.getAttribute('role');
    if (role && ['button', 'link', 'textbox', 'checkbox', 'radio', 'combobox', 'tab', 'menuitem', 'switch', 'slider'].includes(role)) return true;
    return false;
  }

  function inferRole(el) {
    const map = { 'A': 'link', 'BUTTON': 'button', 'INPUT': el.type || 'textbox', 'SELECT': 'combobox', 'TEXTAREA': 'textbox' };
    return map[el.tagName] || '';
  }

  function directText(el) {
    let t = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) t += n.textContent;
    }
    return t.trim().slice(0, 120);
  }

  function buildSelector(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let cur = el;
    for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
      let s = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift(`#${CSS.escape(cur.id)}`); break; }
      if (cur.className && typeof cur.className === 'string') {
        const cls = cur.className.trim().split(/\s+/).slice(0, 2).map(c => CSS.escape(c)).join('.');
        if (cls) s += '.' + cls;
      }
      parts.unshift(s);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }

  function findLabel(el) {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return lbl.textContent.trim().slice(0, 80);
    }
    const parent = el.closest('label');
    return parent ? parent.textContent.trim().slice(0, 80) : (el.ariaLabel || '');
  }

})();
