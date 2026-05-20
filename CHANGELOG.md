# Changelog

## 2.0.0 — Parallel agentic work

Major release. Adds ~50 new bridge actions and orchestration primitives aimed
at AI agents that need to drive multiple browser tabs in parallel.

### Highlights

- **Parallel multi-tab work.** Every tab-targeted action now accepts a
  `tabId` so commands can run against a specific tab without stealing focus.
  New `parallel` batch action fans out across tabs concurrently.
- **Tab Unloader defense.** Zen aggressively discards idle tabs;
  `keepAlive` runs a background pinger that pins listed tabs against the
  unloader, and `wakeTab` revives any tab that did get discarded.
- **Hot reload.** `reloadExtension` lets a future MCP call rebuild & reload
  the extension without manually toggling it in `about:addons`.
- **Content extraction.** `getReadability` (article extractor), `getMarkdown`
  (DOM → MD), `query` (multi-field extract), plus `getLinks`, `getImages`,
  `getMeta`, `getStructuredData`, `getBounds`, `getComputedStyle`, `getHTML`,
  `getIframes`, `explainSelector`, `fullPageMetrics`.
- **Forms & interaction.** `formFill` (fuzzy field matching by selector/
  name/label/placeholder), `selectOption`, `checkBox`, `focus`/`blur`,
  `keypress` (with modifiers), `doubleClick`, `submitForm`, `drag`,
  `clickAndWaitNavigation`.
- **Visual.** `elementScreenshot` (scroll-into-view + crop via
  OffscreenCanvas), `fullPageScreenshot` (scroll-and-stitch).
- **Tab/window management.** `pinTab`, `muteTab`, `duplicateTab`,
  `reloadTabBrowser` (with `bypassCache`), `goBack`, `goForward`,
  `getZoom`/`setZoom`, `getWindows`, `createWindow`, `closeWindow`,
  `focusWindow`, `moveTab`, `detachTab`.
- **Network / state / auth.** `cookies` (get/set/remove/clear),
  `localStorage`/`sessionStorage` ops, `clipboard` read/write,
  `downloads` (download/list/cancel), `clearBrowsingData`, request
  `intercept` (block/redirect by URL regex), `captureNetwork`
  (PerformanceObserver-based), `waitForNetworkIdle`, `waitForUrl`,
  `waitForTitle`, `watchConsole`/`consoleLogs`.
- **Sessions.** `saveSession`/`loadSession` snapshot cookies + per-origin
  localStorage to disk under `~/claude-zen-sessions/` — log in once
  interactively, parallel agents reuse the session.
- **Orchestration.** `broadcast` (same command to many tabs concurrently),
  `syncBarrier` (wait for a JS predicate across N tabs), tag/resolve
  tab names, tab `pool` (warm pool of N tabs with acquire/release).
- **Batch scripting.** New `if`, `while`, `try`, `sequence`, `retry`
  actions inside `batch`. `${$N.field}` variable substitution between
  batch steps so the output of step N can flow into step M.
- **Observability & policy.** `health`, `logs` (bridge log ring buffer),
  `audit` (command history with timings), `setPolicy` (URL
  allowlist/denylist + read-only mode that blocks write actions).

### Breaking

- **New extension permissions** required: `cookies`, `webRequest`,
  `webRequestBlocking`, `downloads`, `clipboardRead`, `clipboardWrite`,
  `browsingData`. Browser will re-prompt on update.
- `intercept` and `captureNetwork` use `effect`/`op` parameters instead
  of `action` to avoid collision with the wire-level `action` key.

### Fixed

- MCP server bridge URL hard-coded to `127.0.0.1` instead of `localhost`
  — prevents stray IPv6 listeners on `:::8765` from hijacking calls.
- `send_to_extension` no longer lets `params["action"]` clobber the
  command action name.

### Internal

- Bridge HTTP server adds POST handlers to formerly GET-only endpoints
  (`/api/page-info`, `/api/page-text`, `/api/tabs`, `/api/forms`,
  `/api/dom`) so callers can pass `tabId` in the body.
- Cache keys for cacheable handlers now include `tabId` to avoid
  cross-tab cache pollution.
- Bridge ring-buffers stdout into an in-memory log; `_audit` records
  every forwarded action with timings and outcome.

### Test coverage

- 75/75 e2e tests passing (`test_e2e.ps1`) — bridge orchestration,
  1.x extension regression, every new extension feature exercised
  against live `example.com`/`iana.org` tabs.

## 1.3.0

- IPv6 fix for ZenLink-MCP (the bridge listens only on `127.0.0.1`).
- `wakeTab` for explicit revival of unloader-discarded tabs.
- `keepAlive`/`keepAliveStop` to pin tabs against Zen's Tab Unloader.
- `reloadExtension` for hot-reloading the extension via API.
- `bridge_version` and `keep_alive_tabs` added to `/api/status`.

## 1.2.2

- 1KB-bounded `[object Object]` truncation in `executeJS` results.
- Various stability fixes.

## 1.1.2 — and earlier

See git history.

## 2.0.1

- Bumped `manifest.json` `strict_min_version` from 140.0 → 142.0 so AMO's
  automated linter accepts the `browser_specific_settings.gecko.data_collection_permissions`
  field (added in Firefox 142). Lets the CI auto-sign workflow succeed.
- No code changes from 2.0.0.
