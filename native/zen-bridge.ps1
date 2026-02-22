# Claude-Zen Bridge Helper
# Usage: . .\zen-bridge.ps1
# Then call functions like: Zen-Screenshot, Zen-Click, Zen-Navigate, etc.

$BRIDGE_URL = "http://localhost:8765"

function Zen-Status {
    try {
        $r = Invoke-RestMethod "$BRIDGE_URL/api/status" -TimeoutSec 3
        $r | ConvertTo-Json
    } catch {
        "Bridge not running. Start it with: python bridge.py"
    }
}

function Zen-Screenshot {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/screenshot" -TimeoutSec 10
    $r | ConvertTo-Json
}

function Zen-PageInfo {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/page-info" -TimeoutSec 5
    $r | ConvertTo-Json
}

function Zen-PageText {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/page-text" -TimeoutSec 5
    $r | ConvertTo-Json
}

function Zen-Tabs {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/tabs" -TimeoutSec 5
    $r | ConvertTo-Json -Depth 5
}

function Zen-DOM {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/dom" -TimeoutSec 10
    $r | ConvertTo-Json -Depth 10 -Compress
}

function Zen-Forms {
    $r = Invoke-RestMethod "$BRIDGE_URL/api/forms" -TimeoutSec 5
    $r | ConvertTo-Json -Depth 5
}

function Zen-Navigate([string]$url) {
    $body = @{ url = $url } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/navigate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    $r | ConvertTo-Json
}

function Zen-Click([string]$selector, [int]$x = -1, [int]$y = -1) {
    $body = @{}
    if ($selector) { $body.selector = $selector }
    if ($x -ge 0 -and $y -ge 0) { $body.coords = @{ x = $x; y = $y } }
    $json = $body | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/click" -Method Post -Body $json -ContentType "application/json" -TimeoutSec 10
    $r | ConvertTo-Json
}

function Zen-Type([string]$selector, [string]$text, [switch]$clear) {
    $body = @{ selector = $selector; text = $text; clear = $clear.IsPresent }
    $json = $body | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/type" -Method Post -Body $json -ContentType "application/json" -TimeoutSec 10
    $r | ConvertTo-Json
}

function Zen-Scroll([string]$direction = "down", [int]$amount = 500) {
    $body = @{ direction = $direction; amount = $amount } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/scroll" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    $r | ConvertTo-Json
}

function Zen-Hover([string]$selector) {
    $body = @{ selector = $selector } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/hover" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    $r | ConvertTo-Json
}

function Zen-Fill([string]$selector, [string]$value) {
    $body = @{ selector = $selector; value = $value } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/fill" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    $r | ConvertTo-Json
}

function Zen-Find([string]$query) {
    $body = @{ query = $query } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/find" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    $r | ConvertTo-Json -Depth 5
}

function Zen-JS([string]$code) {
    $body = @{ code = $code } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/js" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    $r | ConvertTo-Json
}

function Zen-Highlight([string]$selector) {
    $body = @{ selector = $selector } | ConvertTo-Json
    $r = Invoke-RestMethod "$BRIDGE_URL/api/highlight" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 5
    $r | ConvertTo-Json
}

Write-Host "Claude-Zen Bridge loaded. Functions available:" -ForegroundColor Cyan
Write-Host "  Zen-Status, Zen-Screenshot, Zen-PageInfo, Zen-PageText" -ForegroundColor Gray
Write-Host "  Zen-Tabs, Zen-DOM, Zen-Forms" -ForegroundColor Gray
Write-Host "  Zen-Navigate <url>, Zen-Click <selector>, Zen-Type <selector> <text>" -ForegroundColor Gray
Write-Host "  Zen-Scroll <direction>, Zen-Hover <selector>, Zen-Fill <selector> <value>" -ForegroundColor Gray
Write-Host "  Zen-Find <query>, Zen-JS <code>, Zen-Highlight <selector>" -ForegroundColor Gray
