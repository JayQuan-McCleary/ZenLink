# Best Buy — Scraping Playbook

## Problem
Best Buy's search/category pages lazy-load product cards via intersection observers.
The `.sku-item` selector (visible in DevTools on a fully loaded page) returns empty
when queried immediately after navigation or when the tab is not the active/focused tab.
Even after `waitForElement` reports a match, the list may be skeleton placeholders
rather than real product data.

## Root Cause
Best Buy uses React with virtual/windowed rendering. Product cards only fully hydrate
when they enter the viewport. Running the tab in the background (e.g. in a `parallel`
batch sequence) means the intersection observers never fire — nothing scrolls into view
because the tab has no visible viewport.

Additionally, `.sku-item` is **not the real product container** in 2026. The live
selector is `.product-list-item` (confirmed March 2026).

## What Does NOT Work
```json
{ "action": "waitForElement", "selector": ".sku-item", "timeout": 12000 }
```
Times out even when the page has loaded — wrong selector, and lazy-loading hasn't
triggered because the tab isn't focused.

```json
{ "action": "scroll", "direction": "down", "amount": 5 }
```
ZenLink's `scroll amount` maps to a very small pixel offset (scrollY went from 0 → 10px
with `amount: 5`). Not enough to trigger viewport-based lazy loading.

## What WORKS — JS `window.scrollTo` for Real Pixel Scrolling

### Step 1 — Navigate to a search URL with filters baked in
```
https://www.bestbuy.com/site/searchpage.jsp?st=gaming+desktop+pc&intl=nosplash&price=PCE_0~PCE_200000&sort=BESTSELLING
```

### Step 2 — Wait for initial page paint then use JS to scroll real pixels
```json
{ "action": "sleep", "ms": 3000 },
{ "action": "js", "code": "window.scrollTo(0, 800)" },
{ "action": "sleep", "ms": 1500 },
{ "action": "js", "code": "window.scrollTo(0, 1600)" },
{ "action": "sleep", "ms": 1500 },
{ "action": "js", "code": "window.scrollTo(0, 2400)" },
{ "action": "sleep", "ms": 2000 }
```
Each `scrollTo` call moves to an absolute Y position in pixels. The sleeps give
the intersection observer time to fire and render the newly visible cards.

### Step 3 — Extract using correct 2026 selectors
```json
{
  "action": "js",
  "code": "JSON.stringify([...document.querySelectorAll('.product-list-item')].slice(0,10).map(el => ({ name: el.querySelector('.product-title')?.innerText?.trim(), price: el.querySelector('[class*=price]')?.innerText?.trim()?.match(/\\$[\\d,\\.]+/)?.[0], rating: el.querySelector('[class*=rating]')?.getAttribute('aria-label') })).filter(x => x.name))"
}
```

## Key Selectors (verified 2026-03-12)
| Data | Selector |
|------|----------|
| Product card container | `.product-list-item` |
| Product name | `.product-title` |
| Price | `[class*=price]` → regex `\$[\d,\.]+` |
| Rating | `[class*=rating]` → `aria-label` attribute |

**⚠️ Do NOT use `.sku-item`** — this was the old selector and returns 0 results as of 2026.

## Notes
- Must be the **active/focused tab** for intersection observers to fire, OR use
  `window.scrollTo` via JS (which works even in background tabs since it directly
  manipulates the scroll position).
- Parallel batch sequences will fail on Best Buy because background tabs don't
  trigger lazy loading — run sequentially or use the `window.scrollTo` JS workaround.
- `scrollTo(0, 800)` → `(0, 1600)` → `(0, 2400)` with 1500ms sleeps between is
  the reliable pattern. Each step loads the next viewport's worth of cards.
- 500+ `[class*=product]` elements exist on the page — don't use that as a readiness
  check. Use `.product-list-item` count > 0 instead.
