param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$FrontendUrl = "http://localhost:5173",
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$EnvFile = ".env",
    [int]$BackendWaitSeconds = 15,
    [switch]$ForceRestart,
    [switch]$RequireBackendVenv = $true
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogsRoot = Join-Path $RepoRoot "runtime-logs"
$BackendLogs = Join-Path $LogsRoot "backend"
$FrontendLogs = Join-Path $LogsRoot "frontend"
New-Item -ItemType Directory -Force -Path $BackendLogs | Out-Null
New-Item -ItemType Directory -Force -Path $FrontendLogs | Out-Null

function Get-ListenerPid {
    param([int]$Port)
    $line = (netstat -ano | Select-String -Pattern (":$Port\s+.*LISTENING") | Select-Object -First 1)
    if ($line) {
        $pidText = ($line.ToString().Trim() -replace "\\s+"," " -split " ")[-1]
        try { return [int]$pidText } catch { }
    }

    try {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1
        if ($conn -and $conn.OwningProcess) { return [int]$conn.OwningProcess }
    } catch { }

    return $null
}

function Stop-Listener {
    param([int]$Port)
    $listenerPid = Get-ListenerPid -Port $Port
    if (-not $listenerPid) { return [ordered]@{ port=$Port; stopped=$false; pid=$null; note="no_listener" } }
    try {
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
        return [ordered]@{ port=$Port; stopped=$true; pid=$listenerPid; note="stopped" }
    } catch {
        return [ordered]@{ port=$Port; stopped=$false; pid=$listenerPid; note=$_.Exception.Message }
    }
}

function Probe-Json {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec
        return [ordered]@{ ok=$true; url=$Url; status_code=200; payload=$response; error=$null }
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = $null }
        return [ordered]@{ ok=$false; url=$Url; status_code=$code; payload=$null; error=$_.Exception.Message }
    }
}

function Probe-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return [ordered]@{ ok=$true; url=$Url; status_code=[int]$r.StatusCode; error=$null }
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { $code = $null }
        return [ordered]@{ ok=$false; url=$Url; status_code=$code; error=$_.Exception.Message }
    }
}

function New-RunStamp { return (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") }
$stamp = New-RunStamp

$healthzUrl = "$ApiBaseUrl/api/healthz"
$backendPython = Join-Path $RepoRoot "backend\\.venv\\Scripts\\python.exe"
$backendSitePackages = Join-Path $RepoRoot "backend\\.venv\\Lib\\site-packages"
if (-not (Test-Path $backendPython)) { throw "Backend python not found: $backendPython" }
if (-not (Test-Path $backendSitePackages)) { throw "Backend site-packages not found: $backendSitePackages" }

$needRestart = $false
$initial = [ordered]@{
    backend = (Probe-Json -Url $healthzUrl -TimeoutSec 2)
    frontend = (Probe-Http -Url $FrontendUrl -TimeoutSec 3)
}
if (-not ($initial.backend.ok -and (($initial.backend.payload.status -as [string]).ToLower() -eq "ok"))) { $needRestart = $true }
if (-not $initial.frontend.ok) { $needRestart = $true }

$initialBackendListenerPid = Get-ListenerPid -Port $ApiPort
$initialFrontendListenerPid = Get-ListenerPid -Port $FrontendPort

function Get-ProcessPathSafe {
    param([int]$ProcessId)
    try { return (Get-Process -Id $ProcessId -ErrorAction Stop).Path } catch { return $null }
}

if ($ForceRestart) { $needRestart = $true }

if (-not $needRestart -and $RequireBackendVenv) {
    if ($initialBackendListenerPid) {
        $procPath = Get-ProcessPathSafe -ProcessId $initialBackendListenerPid
        if ($procPath -and ($procPath.ToLower() -ne $backendPython.ToLower())) {
            $needRestart = $true
        }
    }
}

$result = [ordered]@{
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    api_base_url = $ApiBaseUrl
    frontend_url = $FrontendUrl
    healthz_url = $healthzUrl
    initial_probe = $initial
    action = [ordered]@{
        restart = $needRestart
    }
    stop = [ordered]@{
        frontend = $null
        backend = $null
    }
    backend = [ordered]@{
        spawn_pid = $null
        listener_pid = $null
        healthz = $null
        logs = [ordered]@{
            stdout = (Join-Path $BackendLogs "api-$ApiPort-$stamp.out.log")
            stderr = (Join-Path $BackendLogs "api-$ApiPort-$stamp.err.log")
        }
    }
    frontend = [ordered]@{
        spawn_pid = $null
        listener_pid = $null
        http = $null
        logs = [ordered]@{
            stdout = (Join-Path $FrontendLogs "vite-$FrontendPort-$stamp.out.log")
            stderr = (Join-Path $FrontendLogs "vite-$FrontendPort-$stamp.err.log")
        }
    }
}

if (-not $needRestart) {
    $result.backend.listener_pid = $initialBackendListenerPid
    $result.frontend.listener_pid = $initialFrontendListenerPid
    $result.backend.healthz = $initial.backend
    $result.frontend.http = $initial.frontend
    $result.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    $result.ok = $true

    $reportPath = Join-Path $LogsRoot "start-local-app.$stamp.json"
    $result | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8
    $result.report_path = $reportPath

    $result | ConvertTo-Json -Depth 10
    exit 0
}

$result.stop.frontend = (Stop-Listener -Port $FrontendPort)
$result.stop.backend = (Stop-Listener -Port $ApiPort)

# Backend: start with required PYTHONPATH (repo root + backend venv site-packages)
$pyPathValue = "$RepoRoot;$backendSitePackages"
$escapedPyPath = $pyPathValue -replace "'", "''"
$escapedEnvFile = $EnvFile -replace "'", "''"
$escapedBackendPython = $backendPython -replace "'", "''"
$backendCommand = @(
    "`$ErrorActionPreference='Stop';",
    "`$env:PYTHONPATH='$escapedPyPath';",
    "`$env:ENV_FILE='$escapedEnvFile';",
    "& '$escapedBackendPython' -m backend.app"
) -join " "

$backendProc = Start-Process `
    -FilePath powershell `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand) `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $result.backend.logs.stdout `
    -RedirectStandardError $result.backend.logs.stderr `
    -WindowStyle Hidden `
    -PassThru
$result.backend.spawn_pid = $backendProc.Id

# Wait for backend healthz
$deadline = (Get-Date).AddSeconds([double]$BackendWaitSeconds)
do {
    Start-Sleep -Milliseconds 350
    $probe = Probe-Json -Url $healthzUrl -TimeoutSec 2
    if ($probe.ok -and (($probe.payload.status -as [string]).ToLower() -eq "ok")) {
        $result.backend.healthz = $probe
        break
    }
} while ((Get-Date) -lt $deadline)

$result.backend.listener_pid = Get-ListenerPid -Port $ApiPort
if (-not $result.backend.healthz) {
    $result.backend.healthz = Probe-Json -Url $healthzUrl -TimeoutSec 2
    if (-not $result.backend.healthz.ok) {
        $result.blocker = "Backend failed to become healthy within ${BackendWaitSeconds}s."
    }
}

# Frontend: start Vite only after backend is reachable (prevents ECONNREFUSED proxy spam)
$viteArgs = @(".\\node_modules\\vite\\bin\\vite.js", "--host", "localhost", "--port", "$FrontendPort", "--clearScreen", "false")
$frontendProc = Start-Process `
    -FilePath node `
    -ArgumentList $viteArgs `
    -WorkingDirectory (Join-Path $RepoRoot "frontend") `
    -RedirectStandardOutput $result.frontend.logs.stdout `
    -RedirectStandardError $result.frontend.logs.stderr `
    -WindowStyle Hidden `
    -PassThru
$result.frontend.spawn_pid = $frontendProc.Id

Start-Sleep -Seconds 2
$result.frontend.listener_pid = Get-ListenerPid -Port $FrontendPort
$result.frontend.http = Probe-Http -Url $FrontendUrl -TimeoutSec 5

$result.finished_at = (Get-Date).ToUniversalTime().ToString("o")
$result.ok = ($result.backend.healthz.ok -and (($result.backend.healthz.payload.status -as [string]).ToLower() -eq "ok") -and $result.frontend.http.ok)

$reportPath = Join-Path $LogsRoot "start-local-app.$stamp.json"
$result | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8
$result.report_path = $reportPath

$result | ConvertTo-Json -Depth 10
if (-not $result.ok) { exit 1 }
exit 0
