param(
    [string]$EnvFile = ".env",
    [string]$TenantSlug = "systematic-equities",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api",
    [string]$FrontendUrl = "http://localhost:5173",
    [int]$IntervalSeconds = 15,
    [int]$RestartCooldownSeconds = 300,
    [int]$TimeoutSeconds = 12,
    [string]$PythonPath = "",
    [switch]$RegisterStartup
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OpsRoot = Join-Path $Root "runtime-exports\continuous-ops"
$PidPath = Join-Path $OpsRoot "continuous-watch.pid"
$LauncherPath = Join-Path $OpsRoot "launcher.json"
New-Item -ItemType Directory -Force -Path $OpsRoot | Out-Null

if (-not $PythonPath) {
    $BundledPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $BundledPython) {
        $PythonPath = $BundledPython
    } else {
        $PythonPath = "python"
    }
}

$ExistingPid = $null
if (Test-Path -LiteralPath $PidPath) {
    $PidText = (Get-Content -LiteralPath $PidPath -Raw).Trim()
    if ([int]::TryParse($PidText, [ref]$ExistingPid)) {
        $ExistingProcess = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            $payload = [ordered]@{
                ok = $true
                status = "already_running"
                root = $Root
                pid = $ExistingPid
                pid_path = $PidPath
                next_action = "Continuous Ops is already running."
            }
            $payload | ConvertTo-Json -Depth 6
            exit 0
        }
    }
}

$Arguments = @(
    "scripts\trading_safety_tools.py",
    "continuous-watch",
    "--env-file", $EnvFile,
    "--tenant-slug", $TenantSlug,
    "--api-base-url", $ApiBaseUrl,
    "--frontend-url", $FrontendUrl,
    "--interval-seconds", [string]$IntervalSeconds,
    "--restart-cooldown-seconds", [string]$RestartCooldownSeconds,
    "--timeout-seconds", [string]$TimeoutSeconds
)

$Process = Start-Process -FilePath $PythonPath -ArgumentList $Arguments -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$launcher = [ordered]@{
    ok = $true
    status = "started"
    root = $Root
    launcher_pid = $Process.Id
    env_file = $EnvFile
    tenant_slug = $TenantSlug
    api_base_url = $ApiBaseUrl
    frontend_url = $FrontendUrl
    interval_seconds = $IntervalSeconds
    restart_cooldown_seconds = $RestartCooldownSeconds
    timeout_seconds = $TimeoutSeconds
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    pid_path = $PidPath
    command = "$PythonPath $($Arguments -join ' ')"
    register_startup_requested = [bool]$RegisterStartup
    next_action = "Use scripts\status-continuous-market-ops.ps1 to verify the first heartbeat."
}
$launcher | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $LauncherPath -Encoding UTF8

if ($RegisterStartup) {
    $launcher.startup_registration = "not_applied"
    $launcher.startup_next_action = "Startup registration is intentionally explicit; add a Windows scheduled task only after this supervisor is proven stable."
}

$launcher | ConvertTo-Json -Depth 8
