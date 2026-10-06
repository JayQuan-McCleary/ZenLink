# Changelog

## 2.1.1

Extension-only release (bridge unchanged at 2.1.0). Verified on a temporary Zen profile: 75/75 e2e.

### Screenshots
- `elementScreenshot` takes an optional `scale` (1-4, default 1): `POST /api/element-screenshot
  {"selector": "...", "scale": 2}`. The viewport is rendered at that scale (`captureVisibleTab` `scale`), so
  `scale: 2` returns a genuinely sharper PNG at twice the CSS size - text in article screenshots stays crisp
  when it's enlarged - instead of an upscale. Tall elements are still scrolled and stitched at the new scale.
- The capture now reads the real pixels-per-CSS-pixel from the captured image instead of assuming
  `devicePixelRatio`.

## 2.1.0

Extension + bridge release. Everything below was verified against a temporary Zen profile (75/75 e2e) and in
real use filling TikTok / YouTube / Instagram / Threads / X / Bluesky upload pages.

### Uploads
- Native chunked uploads (`uploadChunk` / `uploadCommit` content actions, `/api/upload-file`, MCP
  `zen_upload_file`): files stream into the page in 384 KB chunks, so local files of any size (up to 1 GB) can
  be attached without the OS file picker. Older extensions fall back to an executeJS transport.
- Uploaded Files and the DataTransfer are now built with the **page's own constructors**
  (`window.wrappedJSObject` + `cloneInto`) in both transports, for `input` and `drop` modes. Content-script
  Files were silently ignored by some uploaders (YouTube thumbnail, TikTok/Instagram cover inputs).
  Responses include `pageFiles: true`.

### Screenshots
- **Fixed: `fullPageScreenshot` disconnected the extension.** The bridge's WebSocket used the websockets
  library's 1 MiB default message cap; a full-page PNG is several MB. The cap is now 256 MB.
- `elementScreenshot` now scrolls and stitches elements taller than the viewport (they came back blank below
  the fold). Response adds `segments` and `complete`.
- `fullPageScreenshot` scales device-pixel-ratio captures correctly (was cropped on HiDPI), aligns the last
  segment when the page clamps the final scroll, and hides fixed/sticky headers after the first segment so they
  don't repeat. Canvas is capped at 32000 px (`truncated: true` when the page is taller).

### Bridge API
- `/api/js` result cap raised from 50 KB to 8 MB (content.js `JS_RESULT_LIMIT`). `{"full": true}` reads
  results of any size back in slices (works with older extensions too).
- `/api/close-tab` `{"force": true}` disarms beforeunload ("Leave page?") prompts before closing - plain closes
  timed out on upload pages.
- Bare image URLs passed to `new-tab` / `navigate` open inside a local wrapper page (`/view?src=`, image is
  `#zlimg`) instead of an ImageDocument, which knocked the extension offline. `{"raw": true}` opts out.
- Concurrency hardening (previously unreleased 2.0.5 - 2.0.8 bridge builds):

  - Bumped the bridge runtime version to `2.0.8`.
  - Changed the scheduler from independent tab/global locks to a shared/exclusive
    gate: tab-scoped commands still overlap across tabs, while global actions
    wait for active tab commands and block new tab work until complete.
  - Added scheduler gate state to `/api/scheduler`, `/api/status`, and
    `/api/health`, and clean up idle tab lock state after successful tab closes.
  - Bumped the bridge runtime version to `2.0.7`.
  - Added scheduler queue visibility: `/api/scheduler`, `/api/status`, and
    `/api/health` now report queued command counts per tab/global scope, not just
    active commands already sent to the extension.
  - Added `test_scheduler.ps1`, a focused live regression test for camelCase route
    aliases, cross-tab overlap, and same-tab queue reporting.
  - Updated the broad `test_e2e.ps1` health assertion so current 2.x bridge
    builds do not fail a stale `1.4.0` version check.
  - Updated `test_e2e.ps1` cleanup so its temporary `example` tab tag is removed
    after the orchestration checks.
  - Bumped the bridge runtime version to `2.0.6`.
  - Added camelCase HTTP route aliases for common agent guesses such as
    `/api/pageInfo`, `/api/pageText`, `/api/executeJS`, `/api/waitForElement`,
    and the other multi-word API routes.
  - Bumped the bridge runtime version to `2.0.5` for this concurrency hardening
    build. The browser extension manifest remains `2.0.4` until a packaged
    extension release is cut.
  - Hardened concurrent multi-agent use:
    - Replaced the single-threaded HTTP server with `ThreadingHTTPServer` so
      multiple MCP/client requests can wait independently.
    - Added bridge-side scheduling with per-tab command locks and a global lock
      for active-tab/capture/browser-global actions.
    - Serialized WebSocket sends to the browser extension while still allowing
      independent tab-scoped commands to overlap.
    - Fixed a reconnect race where a stale extension WebSocket could clear a
      newer live connection and make the bridge report "extension not connected".
    - Added scheduler state to `/api/status`, `/api/health`, and `/api/scheduler`.
  - Fixed `trustedClick` selector lookup to honor `tabId` before calculating
    viewport/screen coordinates.

### Tests
- `test_e2e.ps1`: example.com no longer has an `<h1>`, so the 8 element tests target its `<p>` instead.

## 2.0.4

- Metadata-only listed AMO submission build.
- No code changes from 2.0.3; AMO requires a unique version number because
  2.0.3 already exists as a self-distributed version.

## 2.0.3

- Fixed read-only policy enforcement for core write actions and batch commands.
- Cleared short-lived read caches after browser/page mutations so immediate
  reads after navigation do not return stale page data.
- Fixed tab-targeted element/full-page screenshots by activating the requested
  tab for capture and restoring the previous active tab afterward.
- Restored `scroll` amount semantics to pixels to match the MCP/API contract.
- Added device pixel ratio metadata to element bounds for screenshot cropping.

## 2.0.2

- Added `extension/icons/*.png` to git tracking. The synced extension tree was
  missing the icon PNGs because they'd never been `git add`-ed, so CI runs of
  `web-ext sign` failed validation with "An icon defined in the manifest could
  not be found in the package."

## 2.0.1

- Bumped `manifest.json` `strict_min_version` from 140.0 → 142.0 so AMO's
  automated linter accepts the `browser_specific_settings.gecko.data_collection_permissions`
  field (added in Firefox 142). Lets the CI auto-sign workflow succeed.
- No code changes from 2.0.0.

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
