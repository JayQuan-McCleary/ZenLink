# ZenLink Scraping Playbooks

> **🤖 AI INSTRUCTION — READ THIS FIRST**
>
> Before writing any scraping batch sequence for a known site, check if a
> playbook exists in this directory. Playbooks contain verified selectors,
> working URL patterns, and documented workarounds from real scraping sessions.
> Using a playbook avoids re-discovering the same failures from scratch.
>
> **Available playbooks:**
> - [`amazon.md`](./amazon.md) — Bot detection, scroll-then-extract pattern
> - [`bestbuy.md`](./bestbuy.md) — Lazy loading, `window.scrollTo` fix, correct 2026 selectors
> - [`newegg.md`](./newegg.md) — Silent category redirects, use `/p/pl?d=` search URL
> - [`bhphotovideo.md`](./bhphotovideo.md) — CSS Modules hashed classes, wildcard selector pattern
>
> If a playbook doesn't exist for the target site, create one after you solve it.

---

## Index

### 🛒 E-commerce Sites

| Site | Main Challenge | Key Fix | Playbook |
|------|---------------|---------|---------|
| **Amazon** | Bot detection on JS injection | Navigate normally, scroll, read rendered DOM only | [amazon.md](./amazon.md) |
| **Best Buy** | Intersection-observer lazy loading | `window.scrollTo(0, Npx)` via JS action + correct selector `.product-list-item` | [bestbuy.md](./bestbuy.md) |
| **Newegg** | SubCategory URLs silently redirect | Use `/p/pl?d=keywords` search URL instead | [newegg.md](./newegg.md) |
| **B&H Photo** | CSS Modules hashed class names | `[class*="product_"]` wildcard + innerText parsing | [bhphotovideo.md](./bhphotovideo.md) |

---

## Common Patterns (Quick Reference)

### Lazy-loading sites (Best Buy, B&H)
Use JS pixel scrolling — ZenLink's native `scroll amount` maps to tiny pixel offsets,
not enough to trigger intersection observers:
```json
{ "action": "js", "code": "window.scrollTo(0, 800)" },
{ "action": "sleep", "ms": 1500 },
{ "action": "js", "code": "window.scrollTo(0, 1600)" },
{ "action": "sleep", "ms": 1500 },
{ "action": "js", "code": "window.scrollTo(0, 2400)" },
{ "action": "sleep", "ms": 2000 }
```

### CSS Modules sites (B&H and any site with `word_hash` class names)
Sniff the live class prefix, then use wildcard attribute selector:
```json
{ "action": "js", "code": "JSON.stringify([...new Set([...document.querySelectorAll('[class]')].flatMap(e => [...e.classList]).filter(c => /^(item|product|card|result)_[a-zA-Z0-9]+$/.test(c)))].slice(0,10))" }
```
Then query: `document.querySelectorAll('[class*="product_"]')`

### Bot-detection sites (Amazon)
- Use real browser navigation via `navigate` action only
- Never use `fetch()` or XHR via the `js` action
- Always scroll before extracting — let the page hydrate
- Use `waitForElement` as your gate before any extraction JS

### Silent redirect detection (Newegg)
Always sanity-check `document.title` after navigation:
```json
{ "action": "js", "code": "document.title.substring(0, 80)" }
```
If the title doesn't match your expected category → you've been redirected.

---

## Adding a New Playbook

When you solve a new site, create `docs/scraping-playbooks/SITENAME.md` with:
1. **Problem** — what failed and why
2. **Root Cause** — the technical reason
3. **What Does NOT Work** — with code examples (saves future re-discovery)
4. **What WORKS** — full working sequence with copy-pasteable JSON
5. **Key Selectors** — table of verified selectors with a date stamp
6. **Notes** — edge cases, expiry warnings, gotchas

Then add a row to the table in this index file.

---

## Contributing Playbooks

Playbooks only help future agents and users if they're checked into the repo.
After creating or updating a playbook:

1. **Stage the files:** `git add docs/scraping-playbooks/`
2. **Commit:** `git commit -m "playbook: add <sitename> scraping workaround"`
3. **Push:** `git push`

If you're an AI agent, ask the user to commit and push on your behalf.
If you're a human contributor, please push your playbooks — they save everyone
(including the AI) from re-discovering the same site-specific failures.
