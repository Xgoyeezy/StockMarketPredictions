param()

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OpsRoot = Join-Path $Root "runtime-exports\continuous-ops"
$PidPath = Join-Path $OpsRoot "continuous-watch.pid"
$Stopped = @()
$Candidates = @()

if (Test-Path -LiteralPath $PidPath) {
    $PidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    $PidValue = 0
    if ([int]::TryParse($PidText, [ref]$PidValue)) {
        $Candidates += $PidValue
    }
}

$escapedRoot = $Root.Replace("\", "\\")
$matching = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*trading_safety_tools.py*" -and
        $_.CommandLine -like "*continuous-watch*" -and
        ($_.CommandLine -like "*$Root*" -or $_.CommandLine -match [regex]::Escape($Root))
    }
foreach ($item in $matching) {
    if ($Candidates -notcontains [int]$item.ProcessId) {
        $Candidates += [int]$item.ProcessId
    }
}

foreach ($CandidatePid in $Candidates | Select-Object -Unique) {
    $process = Get-Process -Id $CandidatePid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $CandidatePid -Force
        $Stopped += $CandidatePid
    }
}

$payload = [ordered]@{
    ok = $true
    status = if ($Stopped.Count) { "stopped" } else { "not_running" }
    root = $Root
    stopped_pids = $Stopped
    pid_path = $PidPath
    stopped_at = (Get-Date).ToUniversalTime().ToString("o")
    next_action = "Continuous Ops was stopped; backend/frontend are not stopped by this command."
}
$payload | ConvertTo-Json -Depth 6
