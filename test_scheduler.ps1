$ErrorActionPreference = 'Stop'

$base = 'http://127.0.0.1:8765'
$pass = 0
$fail = 0

function Hit($path, $body = $null, $method = 'POST', $timeout = 20) {
  $opts = @{
    Uri = "$base$path"
    Method = $method
    TimeoutSec = $timeout
  }
  if ($null -ne $body) {
    $opts.ContentType = 'application/json'
    $opts.Body = ($body | ConvertTo-Json -Depth 12 -Compress)
  }
  return Invoke-RestMethod @opts
}

function Check($name, [scriptblock]$test) {
  try {
    $ok = & $test
    if ($ok) {
      $script:pass++
      Write-Host "[PASS] $name" -ForegroundColor Green
    } else {
      $script:fail++
      Write-Host "[FAIL] $name" -ForegroundColor Red
    }
  } catch {
    $script:fail++
    Write-Host "[FAIL] $name :: $($_.Exception.Message)" -ForegroundColor Red
  }
}

function Ensure-Tab($url, $match) {
  $tabs = Hit '/api/tabs' $null 'GET'
  $tab = @($tabs.tabs | Where-Object { $_.url -like "*$match*" } | Select-Object -First 1)[0]
  if ($tab) { return [int]$tab.id }

  $created = Hit '/api/newTab' @{ url = $url }
  Start-Sleep -Seconds 2
  return [int]$created.tabId
}

function Start-JsJob($tabId, $label, $ms) {
  Start-Job -ArgumentList $base,$tabId,$label,$ms -ScriptBlock {
    param($base,$tabId,$label,$ms)
    $code = "const __s=Date.now(); while (Date.now() - __s < $ms) {}; JSON.stringify({label:'$label', tabId:$tabId, title:document.title, waitedMs:Date.now()-__s});"
    $body = @{ tabId = $tabId; code = $code } | ConvertTo-Json -Compress
    $started = Get-Date
    $result = Invoke-RestMethod -Uri "$base/api/executeJS" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 20
    [pscustomobject]@{
      label = $label
      tabId = $tabId
      elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
      result = $result.result
    }
  }
}

function Watch-Scheduler($durationMs, $intervalMs) {
  $samples = @()
  $deadline = (Get-Date).AddMilliseconds($durationMs)
  while ((Get-Date) -lt $deadline) {
    $samples += Hit '/api/scheduler' $null 'GET'
    Start-Sleep -Milliseconds $intervalMs
  }
  return $samples
}

$health = Hit '/api/health' $null 'GET'
Check 'bridge 2.0.8+ connected' { $health.extension_connected -and ([version]$health.bridge_version -ge [version]'2.0.8') }

$after = Ensure-Tab 'https://afterimageworks.dev/' 'afterimageworks.dev'
$satchel = Ensure-Tab 'https://opensatchel.dev/' 'opensatchel.dev'
$gate = Ensure-Tab 'https://gateglass.com/' 'gateglass.com'

Write-Host "Tabs: after=$after satchel=$satchel gate=$gate" -ForegroundColor Cyan

$pageInfo = Hit '/api/pageInfo' @{ tabId = $after }
Check 'camelCase /api/pageInfo alias' { $pageInfo.title -match 'Afterimage' }

$executeJs = Hit '/api/executeJS' @{ tabId = $after; code = 'document.title' }
Check 'camelCase /api/executeJS alias' { $executeJs.result -match 'Afterimage' }

$waitForElement = Hit '/api/waitForElement' @{ tabId = $after; selector = 'body'; timeout = 1000 }
Check 'camelCase /api/waitForElement alias' { $waitForElement.found -eq $true }

$crossJobs = @(
  Start-JsJob $after 'cross-after' 900
  Start-JsJob $satchel 'cross-satchel' 900
  Start-JsJob $gate 'cross-gate' 900
)
Start-Sleep -Milliseconds 120
$crossSamples = Watch-Scheduler 1400 120
$crossResults = $crossJobs | Wait-Job | Receive-Job
$crossJobs | Remove-Job

$crossMaxPending = ($crossSamples | Measure-Object pending_commands -Maximum).Maximum
$crossMaxQueued = ($crossSamples | Measure-Object queued_commands -Maximum).Maximum
Check 'cross-tab commands overlap' { $crossResults.Count -eq 3 -and $crossMaxPending -ge 2 -and $crossMaxQueued -eq 0 }

$sameJobs = @(
  Start-JsJob $after 'same-a' 700
  Start-JsJob $after 'same-b' 700
  Start-JsJob $after 'same-c' 700
)
Start-Sleep -Milliseconds 120
$sameSamples = Watch-Scheduler 2600 120
$sameResults = $sameJobs | Wait-Job | Receive-Job
$sameJobs | Remove-Job

$sameMaxPending = ($sameSamples | Measure-Object pending_commands -Maximum).Maximum
$sameMaxQueued = ($sameSamples | Measure-Object queued_commands -Maximum).Maximum
Check 'same-tab commands queue visibly' { $sameResults.Count -eq 3 -and $sameMaxPending -eq 1 -and $sameMaxQueued -ge 1 }

$tabJob = Start-JsJob $after 'tab-before-global' 900
Start-Sleep -Milliseconds 120
$switchJob = Start-Job -ArgumentList $base,$satchel -ScriptBlock {
  param($base,$tabId)
  $body = @{ tabId = $tabId } | ConvertTo-Json -Compress
  $started = Get-Date
  $result = Invoke-RestMethod -Uri "$base/api/switchTab" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 20
  [pscustomobject]@{
    elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
    ok = $result.ok
  }
}
$globalSamples = Watch-Scheduler 1300 100
$tabResult = $tabJob | Wait-Job | Receive-Job
$switchResult = $switchJob | Wait-Job | Receive-Job
$tabJob | Remove-Job
$switchJob | Remove-Job
$globalWaitSeen = @($globalSamples | Where-Object {
  $_.queued_commands -ge 1 -and ($_.queued_scopes.PSObject.Properties.Name -contains 'global:switchTab')
}).Count -gt 0
Check 'global actions wait for active tab work' {
  $tabResult.Count -eq 1 -and $switchResult.ok -and $switchResult.elapsedMs -ge 500 -and $globalWaitSeen
}

$finalHealth = Hit '/api/health' $null 'GET'
Check 'scheduler drains after stress' {
  $finalHealth.scheduler.pending_commands -eq 0 -and
  $finalHealth.scheduler.queued_commands -eq 0 -and
  $finalHealth.extension_connected
}

Write-Host "`nResult: $pass passed, $fail failed" -ForegroundColor Cyan
if ($fail -gt 0) { exit 1 }
