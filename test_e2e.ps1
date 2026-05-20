$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:8765'
$pass = 0; $fail = 0; $skip = 0
$results = @()

function Hit($path, $body, $method='POST', $timeout=30) {
  $opts = @{ Uri = "$base$path"; Method = $method; ContentType = 'application/json'; TimeoutSec = $timeout }
  if ($body -ne $null) { $opts.Body = ($body | ConvertTo-Json -Depth 10 -Compress) }
  try { return Invoke-RestMethod @opts }
  catch { return @{ error = "HTTP: $($_.Exception.Message)" } }
}

function Test($name, $r, $expect) {
  $ok = $true; $why = ''
  if ($r -eq $null) { $ok = $false; $why = 'null response' }
  elseif ($r.error -and $expect -ne 'expectError') { $ok = $false; $why = $r.error }
  elseif ($expect -eq 'expectError' -and -not $r.error) { $ok = $false; $why = 'expected error, got success' }
  elseif ($expect -is [scriptblock]) {
    $check = & $expect $r
    if (-not $check) { $ok = $false; $why = 'check failed' }
  }
  if ($ok) { $script:pass++; Write-Host "[PASS] $name" -ForegroundColor Green }
  else     { $script:fail++; Write-Host "[FAIL] $name :: $why" -ForegroundColor Red }
  $script:results += @{ name = $name; ok = $ok; why = $why; resp = $r }
}

function Skip($name, $why) {
  $script:skip++
  Write-Host "[SKIP] $name :: $why" -ForegroundColor Yellow
}

# === Bridge-only orchestration ===
Write-Host "`n== Bridge-only orchestration ==" -ForegroundColor Cyan
Test 'health'           (Hit '/api/health' $null)            { param($r) $r.bridge_version -eq '1.4.0' -and $r.extension_connected }
Test 'get-policy'       (Hit '/api/get-policy' $null)        { param($r) $r.PSObject.Properties.Name -contains 'allow' }
Test 'set-policy'       (Hit '/api/set-policy' @{ deny=@('evil') })   { param($r) $r.policy.deny -contains 'evil' }
Test 'get-policy after' (Hit '/api/get-policy' $null)        { param($r) $r.deny -contains 'evil' }
Test 'reset-policy'     (Hit '/api/set-policy' @{ deny=@() })   { param($r) $r.policy.deny.Count -eq 0 }
Test 'tag-tab'          (Hit '/api/tag-tab' @{ name='probe'; tabId=999 })  { param($r) $r.ok }
Test 'resolve-tag'      (Hit '/api/resolve-tag' @{ name='probe' })  { param($r) $r.tabId -eq 999 }
Test 'list-tags'        (Hit '/api/list-tags' $null)         { param($r) $r.tags.probe -eq 999 }
Test 'untag-tab'        (Hit '/api/untag-tab' @{ name='probe' })    { param($r) $r.ok }
Test 'list-sessions'    (Hit '/api/list-sessions' $null)     { param($r) $r.PSObject.Properties.Name -contains 'sessions' }
Test 'logs'             (Hit '/api/logs' @{ limit=5 })       { param($r) $r.PSObject.Properties.Name -contains 'lines' }
Test 'audit'            (Hit '/api/audit' @{ limit=5 })      { param($r) $r.PSObject.Properties.Name -contains 'entries' }

# === Legacy regression (1.3.0 extension still works) ===
Write-Host "`n== Legacy regression (1.3.0 extension) ==" -ForegroundColor Cyan
$tabs = Hit '/api/tabs' $null 'GET'
Test 'tabs (GET)' $tabs { param($r) $r.tabs -ne $null }
$open = Hit '/api/new-tab' @{ url='https://example.com' }
Test 'new-tab' $open { param($r) $r.tabId -gt 0 }
$tabId = $open.tabId
Start-Sleep -Seconds 2
Test 'page-info GET (active tab)' (Hit '/api/page-info' $null 'GET') { param($r) $r.url -ne $null }
Test 'page-info POST (specific tab)' (Hit '/api/page-info' @{ tabId=$tabId }) { param($r) $r.title -match 'Example' }
Test 'wait-for-element h1' (Hit '/api/wait-for-element' @{ selector='h1'; timeout=8000; tabId=$tabId } 'POST' 20) { param($r) $r.found }

# Close test tab
$null = Hit '/api/close-tab' @{ tabId=$tabId }

# === Batch enhancements (1.4.0 bridge feature, ext-agnostic) ===
Write-Host "`n== Batch enhancements ==" -ForegroundColor Cyan
$retryR = Hit '/api/batch' @{ commands=@(@{ action='retry'; command=@{ action='tabs' }; maxAttempts=2 }) }
Test 'batch retry wrapper' $retryR { param($r) $r.results[0].ok }

$broadR = Hit '/api/broadcast' @{ tabIds=@(); command=@{ action='pageInfo' } }
Test 'broadcast empty (expect error)' $broadR 'expectError'

# === Probe whether 1.4.0 extension is live ===
Write-Host "`n== 1.4.0 extension feature probes ==" -ForegroundColor Cyan
# Open a test tab BEFORE probing extension features (about:addons blocks injection)
$t = Hit '/api/new-tab' @{ url='https://example.com' }
$tid = $t.tabId
Start-Sleep -Seconds 3
$null = Hit '/api/wait-for-element' @{ selector='h1'; tabId=$tid; timeout=10000 }

$probe = Hit '/api/iframes' @{ tabId=$tid }
$ext14 = -not ($probe.error -match 'Unknown action')
if (-not $ext14) {
  Skip 'extension v1.4.0 features (NOT installed yet)' 'awaiting "Add" click in Zen'
  $null = Hit '/api/close-tab' @{ tabId=$tid }
} else {
  Test 'iframes' $probe { param($r) $r.PSObject.Properties.Name -contains 'iframes' }

  Test 'query basic'      (Hit '/api/query' @{ selector='p'; tabId=$tid })  { param($r) $r.items.Count -ge 1 }
  Test 'query fields'     (Hit '/api/query' @{ selector='a'; tabId=$tid; fields=@('text','href','bounds') })  { param($r) $r.items[0].href -ne $null }
  Test 'html'             (Hit '/api/html' @{ selector='h1'; tabId=$tid })  { param($r) $r.html -match 'h1' }
  Test 'links'            (Hit '/api/links' @{ tabId=$tid })                { param($r) $r.count -ge 1 }
  Test 'images'           (Hit '/api/images' @{ tabId=$tid })               { param($r) $r.PSObject.Properties.Name -contains 'images' }
  Test 'meta'             (Hit '/api/meta' @{ tabId=$tid })                 { param($r) $r.title -eq 'Example Domain' }
  Test 'structured-data'  (Hit '/api/structured-data' @{ tabId=$tid })      { param($r) $r.PSObject.Properties.Name -contains 'jsonld' }
  Test 'bounds'           (Hit '/api/bounds' @{ selector='h1'; tabId=$tid }) { param($r) $r.width -gt 0 }
  Test 'computed-style'   (Hit '/api/computed-style' @{ selector='h1'; tabId=$tid })  { param($r) $r.computed.color -ne $null }
  Test 'readability'      (Hit '/api/readability' @{ tabId=$tid })          { param($r) $r.title -match 'Example' -and $r.textContent.Length -gt 50 }
  Test 'markdown'         (Hit '/api/markdown' @{ tabId=$tid })             { param($r) $r.markdown -match '#' }
  Test 'full-page-metrics' (Hit '/api/full-page-metrics' @{ tabId=$tid })  { param($r) $r.docWidth -gt 0 }
  Test 'explain-selector' (Hit '/api/explain-selector' @{ selector='p'; tabId=$tid }) { param($r) $r.matches -ge 0 }
  Test 'watch-console on' (Hit '/api/watch-console' @{ enabled=$true; tabId=$tid })   { param($r) $r.enabled }
  Test 'console-logs'     (Hit '/api/console-logs' @{ tabId=$tid })         { param($r) $r.PSObject.Properties.Name -contains 'logs' }
  Test 'capture-network start' (Hit '/api/capture-network' @{ op='start'; tabId=$tid })  { param($r) $r.capturing }
  Test 'capture-network read'  (Hit '/api/capture-network' @{ op='read'; tabId=$tid })   { param($r) $r.PSObject.Properties.Name -contains 'entries' }
  Test 'wait-for-url'     (Hit '/api/wait-for-url' @{ pattern='example'; timeout=5000; tabId=$tid })  { param($r) $r.ok }
  Test 'wait-for-title'   (Hit '/api/wait-for-title' @{ pattern='Example'; timeout=5000; tabId=$tid }) { param($r) $r.ok }
  Test 'wait-for-network-idle' (Hit '/api/wait-for-network-idle' @{ idleMs=300; timeout=5000; tabId=$tid })  { param($r) $r.ok }

  Test 'storage local set' (Hit '/api/storage' @{ kind='local'; op='set'; key='zk_test'; value='hello'; tabId=$tid })  { param($r) $r.ok }
  Test 'storage local get' (Hit '/api/storage' @{ kind='local'; op='get'; key='zk_test'; tabId=$tid })  { param($r) $r.value -eq 'hello' }
  Test 'storage local snapshot' (Hit '/api/storage' @{ kind='local'; op='snapshot'; tabId=$tid })  { param($r) $r.items.zk_test -eq 'hello' }
  Test 'storage local clear' (Hit '/api/storage' @{ kind='local'; op='clear'; tabId=$tid })  { param($r) $r.ok }

  Test 'focus h1'  (Hit '/api/focus' @{ selector='h1'; tabId=$tid })  { param($r) $r.ok }
  Test 'blur'      (Hit '/api/blur' @{ tabId=$tid })                  { param($r) $r.ok }
  Test 'keypress Tab' (Hit '/api/keypress' @{ key='Tab'; tabId=$tid })  { param($r) $r.ok }
  Test 'double-click h1' (Hit '/api/double-click' @{ selector='h1'; tabId=$tid })  { param($r) $r.ok }
  Test 'drag (no targets, expect error)' (Hit '/api/drag' @{ from='#nope-a'; to='#nope-b'; tabId=$tid }) 'expectError'

  # Tab management
  Test 'pin-tab'   (Hit '/api/pin-tab' @{ pinned=$true; tabId=$tid })   { param($r) $r.ok }
  Test 'unpin'     (Hit '/api/pin-tab' @{ pinned=$false; tabId=$tid })  { param($r) $r.ok }
  Test 'mute'      (Hit '/api/mute-tab' @{ muted=$true; tabId=$tid })   { param($r) $r.ok }
  Test 'unmute'    (Hit '/api/mute-tab' @{ muted=$false; tabId=$tid })  { param($r) $r.ok }
  Test 'reload'    (Hit '/api/reload-tab' @{ tabId=$tid })              { param($r) $r.ok }
  Start-Sleep -Seconds 2
  Test 'zoom set'  (Hit '/api/set-zoom' @{ factor=1.25; tabId=$tid })   { param($r) $r.ok }
  Test 'zoom get'  (Hit '/api/get-zoom' @{ tabId=$tid })                { param($r) $r.factor -gt 1 }
  Test 'zoom reset' (Hit '/api/set-zoom' @{ factor=1.0; tabId=$tid })   { param($r) $r.ok }
  Test 'windows'   (Hit '/api/windows' @{})                              { param($r) $r.count -ge 1 }
  $dupR = Hit '/api/duplicate-tab' @{ tabId=$tid }
  Test 'duplicate-tab' $dupR { param($r) $r.tabId -gt 0 }
  if ($dupR.tabId) { $null = Hit '/api/close-tab' @{ tabId=$dupR.tabId } }

  # Cookies / clipboard
  Test 'cookies get'  (Hit '/api/cookies' @{ op='get'; url='https://example.com' })  { param($r) $r.PSObject.Properties.Name -contains 'cookies' }
  Test 'clipboard write' (Hit '/api/clipboard' @{ op='write'; text='zenlink-test-string' })  { param($r) $r.ok }
  Test 'clipboard read'  (Hit '/api/clipboard' @{ op='read' })            { param($r) $r.text -match 'zenlink-test' }

  # Intercept
  Test 'intercept add'   (Hit '/api/intercept' @{ op='add'; patterns=@('doubleclick\.net'); effect='block' })  { param($r) $r.ok -eq $true -and $r.rules -ge 1 }
  Test 'intercept list'  (Hit '/api/intercept' @{ op='list' })            { param($r) $r.PSObject.Properties.Name -contains 'rules' }
  Test 'intercept clear' (Hit '/api/intercept' @{ op='clear' })           { param($r) $r.rules -eq 0 }

  # Element + full-page screenshot (heavier — but we said thorough)
  Test 'element-screenshot' (Hit '/api/element-screenshot' @{ selector='h1'; tabId=$tid } 'POST' 30)  { param($r) $r.dataUrl -ne $null }
  Test 'full-page-screenshot' (Hit '/api/full-page-screenshot' @{ tabId=$tid } 'POST' 90)  { param($r) $r.dataUrl -ne $null }

  # Orchestration with real tabs
  $t2 = Hit '/api/new-tab' @{ url='https://www.iana.org/' }
  $tid2 = $t2.tabId
  Start-Sleep -Seconds 3
  $null = Hit '/api/wait-for-element' @{ selector='body'; tabId=$tid2; timeout=10000 }
  Test 'broadcast pageInfo'  (Hit '/api/broadcast' @{ tabIds=@($tid, $tid2); command=@{ action='pageInfo' } })  { param($r) ($r.results.PSObject.Properties | Measure-Object).Count -eq 2 }
  Test 'syncBarrier'         (Hit '/api/sync-barrier' @{ tabIds=@($tid, $tid2); predicate='document.readyState === "complete"'; timeout=5000 })  { param($r) $r.ok }

  # Tag tabs and resolve
  Test 'tag tid'    (Hit '/api/tag-tab' @{ name='example'; tabId=$tid })  { param($r) $r.ok }
  Test 'resolve example' (Hit '/api/resolve-tag' @{ name='example' })     { param($r) $r.tabId -eq $tid }

  # Tab pool
  Test 'tab-pool init'  (Hit '/api/tab-pool' @{ size=2; url='about:blank' })  { param($r) $r.size -eq 2 }
  $ack = Hit '/api/pool-acquire' @{}
  Test 'pool-acquire'   $ack  { param($r) $r.tabId -gt 0 }
  if ($ack.tabId) {
    Test 'pool-release' (Hit '/api/pool-release' @{ tabId=$ack.tabId })   { param($r) $r.ok }
  }
  Test 'tab-pool shrink' (Hit '/api/tab-pool' @{ size=0; url='about:blank' })  { param($r) $r.size -eq 0 }

  # Cleanup
  $null = Hit '/api/close-tab' @{ tabId=$tid }
  $null = Hit '/api/close-tab' @{ tabId=$tid2 }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "RESULTS: $pass passed, $fail failed, $skip skipped" -ForegroundColor Cyan
if ($fail -gt 0) {
  Write-Host "`nFailed tests:" -ForegroundColor Red
  $results | Where-Object { -not $_.ok } | ForEach-Object { Write-Host "  - $($_.name) :: $($_.why)" -ForegroundColor Red }
}
exit $fail
