param(
    [switch]$Raw
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OpsRoot = Join-Path $Root "runtime-exports\continuous-ops"
$PidPath = Join-Path $OpsRoot "continuous-watch.pid"
$LatestPath = Join-Path $OpsRoot "latest.json"

$PidValue = $null
if (Test-Path -LiteralPath $PidPath) {
    $PidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    [void][int]::TryParse($PidText, [ref]$PidValue)
}
$Process = if ($PidValue) { Get-Process -Id $PidValue -ErrorAction SilentlyContinue } else { $null }
$Latest = if (Test-Path -LiteralPath $LatestPath) { Get-Content -LiteralPath $LatestPath -Raw } else { "{}" }
if ($Raw) {
    $Latest
    exit 0
}
$LatestObject = $Latest | ConvertFrom-Json
$payload = [ordered]@{
    ok = $true
    root = $Root
    supervisor_running = [bool]$Process
    pid = $PidValue
    latest_status = $LatestObject.status
    latest_label = $LatestObject.label
    latest_heartbeat_at = $LatestObject.generated_at
    restart_count = $LatestObject.supervisor.restart_count
    evidence_observed = $LatestObject.evidence_million.observed_event_count
    evidence_rate_per_hour = $LatestObject.evidence_million.rate_per_hour
    evidence_eta_hours = $LatestObject.evidence_million.eta_hours
    kill_switch_active = $LatestObject.kill_switch.active
    ready_for_operator_clear = $LatestObject.kill_switch.ready_for_operator_clear
    next_action = if ($LatestObject.next_action) { $LatestObject.next_action } else { "Start Continuous Ops with scripts\start-continuous-market-ops.ps1." }
    latest_path = $LatestPath
}
$payload | ConvertTo-Json -Depth 8
