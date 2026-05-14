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
$StartupDebugLog = Join-Path $LogsRoot "start-local-app.debug.log"

function Write-StartupPhase {
    param([string]$Phase)
    try {
        $timestamp = (Get-Date).ToUniversalTime().ToString("o")
        "$timestamp $Phase" | Add-Content -Path $StartupDebugLog -Encoding UTF8
    } catch { }
}

Write-StartupPhase -Phase "script_started"

function Get-ListenerPid {
    param([int]$Port)
    try {
        $netstatPath = Join-Path $env:SystemRoot "System32\netstat.exe"
        $netstatOut = Join-Path $env:TEMP "stock-signals-netstat-$PID-$Port.out"
        $netstatErr = Join-Path $env:TEMP "stock-signals-netstat-$PID-$Port.err"
        Remove-Item $netstatOut, $netstatErr -ErrorAction SilentlyContinue
        $netstatProc = Start-Process `
            -FilePath $netstatPath `
            -ArgumentList @("-ano", "-p", "tcp") `
            -RedirectStandardOutput $netstatOut `
            -RedirectStandardError $netstatErr `
            -WindowStyle Hidden `
            -PassThru
        if (-not $netstatProc.WaitForExit(5000)) {
            Stop-Process -Id $netstatProc.Id -Force -ErrorAction SilentlyContinue
            Write-StartupPhase -Phase "listener_check_timeout port=$Port"
            return $null
        }
        $line = (Get-Content $netstatOut -ErrorAction SilentlyContinue | Select-String -Pattern (":$Port\s+.*LISTENING") | Select-Object -First 1)
        if ($line) {
            $pidText = ($line.ToString().Trim() -replace "\\s+"," " -split " ")[-1]
            try { return [int]$pidText } catch { }
        }
    } catch {
        Write-StartupPhase -Phase "listener_check_error port=$Port"
    } finally {
        Remove-Item $netstatOut, $netstatErr -ErrorAction SilentlyContinue
    }

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

function New-NoListenerProbe {
    param([string]$Url)
    return [ordered]@{ ok=$false; url=$Url; status_code=$null; payload=$null; error="No listener is bound for this local app port." }
}

function New-RunStamp { return (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") }
$stamp = New-RunStamp

function Set-DefaultEnv {
    param([string]$Name, [string]$Value)
    if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

Set-DefaultEnv -Name "ENTERPRISE_RUNTIME_PROFILE" -Value "operator-local"
Set-DefaultEnv -Name "JOB_WORKER_ENABLED" -Value "false"
Set-DefaultEnv -Name "TRADE_AUTOMATION_WORKER_ENABLED" -Value "false"
Set-DefaultEnv -Name "EVIDENCE_ACCELERATOR_ENABLED" -Value "false"
Set-DefaultEnv -Name "REALTIME_STREAM_ENABLED" -Value "false"

$healthzUrl = "$ApiBaseUrl/api/healthz"
$backendPython = Join-Path $RepoRoot "backend\\.venv\\Scripts\\python.exe"
$backendSitePackages = Join-Path $RepoRoot "backend\\.venv\\Lib\\site-packages"
if (-not (Test-Path $backendPython)) { throw "Backend python not found: $backendPython" }
if (-not (Test-Path $backendSitePackages)) { throw "Backend site-packages not found: $backendSitePackages" }
Write-StartupPhase -Phase "backend_runtime_checked"

$initialBackendListenerPid = Get-ListenerPid -Port $ApiPort
$initialFrontendListenerPid = Get-ListenerPid -Port $FrontendPort
Write-StartupPhase -Phase "initial_listener_check backend=$initialBackendListenerPid frontend=$initialFrontendListenerPid"

$needRestart = $false
$initial = [ordered]@{
    backend = $(if ($initialBackendListenerPid) { Probe-Json -Url $healthzUrl -TimeoutSec 2 } else { New-NoListenerProbe -Url $healthzUrl })
    frontend = $(if ($initialFrontendListenerPid) { Probe-Http -Url $FrontendUrl -TimeoutSec 3 } else { New-NoListenerProbe -Url $FrontendUrl })
}
Write-StartupPhase -Phase "initial_probe_complete backend_ok=$($initial.backend.ok) frontend_ok=$($initial.frontend.ok)"
if (-not ($initial.backend.ok -and (($initial.backend.payload.status -as [string]).ToLower() -eq "ok"))) { $needRestart = $true }
if (-not $initial.frontend.ok) { $needRestart = $true }

function Get-ProcessPathSafe {
    param([int]$ProcessId)
    try { return (Get-Process -Id $ProcessId -ErrorAction Stop).Path } catch { return $null }
}

function Get-ProcessCommandLineSafe {
    param([int]$ProcessId)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if ($process -and $process.CommandLine) { return [string]$process.CommandLine }
    } catch { }
    return $null
}

function Normalize-ComparablePathText {
    param([string]$PathText)
    if (-not $PathText) { return "" }
    return (($PathText -replace "/", "\").ToLowerInvariant() -replace "\\{2,}", "\")
}

function Test-BackendListenerMatchesRepo {
    param(
        [int]$ProcessId,
        [string]$ExpectedPython,
        [string]$RepoRootPath
    )
    $expectedPythonText = Normalize-ComparablePathText -PathText $ExpectedPython
    $repoRootText = Normalize-ComparablePathText -PathText $RepoRootPath
    $procPathText = Normalize-ComparablePathText -PathText (Get-ProcessPathSafe -ProcessId $ProcessId)
    if ($procPathText -and ($procPathText -eq $expectedPythonText)) { return $true }

    $commandLineText = Normalize-ComparablePathText -PathText (Get-ProcessCommandLineSafe -ProcessId $ProcessId)
    if (-not $commandLineText) { return $false }

    $launchesBackendApp = $commandLineText.Contains("-m backend.app")
    $usesExpectedVenv = $commandLineText.Contains($expectedPythonText)
    $runsFromRepo = $commandLineText.Contains($repoRootText)
    return ($launchesBackendApp -and ($usesExpectedVenv -or $runsFromRepo))
}

if ($ForceRestart) { $needRestart = $true }

if (-not $needRestart -and $RequireBackendVenv) {
    if ($initialBackendListenerPid) {
        if (-not (Test-BackendListenerMatchesRepo -ProcessId $initialBackendListenerPid -ExpectedPython $backendPython -RepoRootPath $RepoRoot)) {
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
Write-StartupPhase -Phase "stopped_existing_listeners"

# Backend: start with required PYTHONPATH (repo root + backend venv site-packages).
# Start python directly so the reported PID is the backend process and failures
# can be stopped cleanly without leaving a wrapper process behind.
$pyPathValue = "$RepoRoot;$backendSitePackages"
$previousPyPath = $env:PYTHONPATH
$previousEnvFile = $env:ENV_FILE
try {
    $env:PYTHONPATH = $pyPathValue
    $env:ENV_FILE = $EnvFile
    Write-StartupPhase -Phase "starting_backend"
    $backendProc = Start-Process `
        -FilePath $backendPython `
        -ArgumentList @("-m", "backend.app") `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $result.backend.logs.stdout `
        -RedirectStandardError $result.backend.logs.stderr `
        -WindowStyle Hidden `
        -PassThru
    Write-StartupPhase -Phase "backend_started pid=$($backendProc.Id)"
} finally {
    $env:PYTHONPATH = $previousPyPath
    $env:ENV_FILE = $previousEnvFile
}
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
Write-StartupPhase -Phase "backend_wait_complete"

$result.backend.listener_pid = Get-ListenerPid -Port $ApiPort
if (-not $result.backend.healthz) {
    $result.backend.healthz = Probe-Json -Url $healthzUrl -TimeoutSec 2
    if (-not $result.backend.healthz.ok) {
        $result.blocker = "Backend failed to become healthy within ${BackendWaitSeconds}s."
    }
}

if (-not ($result.backend.healthz.ok -and (($result.backend.healthz.payload.status -as [string]).ToLower() -eq "ok"))) {
    try {
        if ($backendProc -and -not $backendProc.HasExited) {
            Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    $result.frontend.http = [ordered]@{
        ok = $false
        url = $FrontendUrl
        status_code = $null
        error = "Frontend not started because backend health check failed."
    }
    $result.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    $result.ok = $false

    $reportPath = Join-Path $LogsRoot "start-local-app.$stamp.json"
    $result | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8
    $result.report_path = $reportPath

    $result | ConvertTo-Json -Depth 10
    exit 1
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
