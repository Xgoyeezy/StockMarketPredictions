param(
    [string]$Symbols = "AAPL",
    [string]$EnvFile = "..\.env.staging",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [int]$SliceCycles = 20,
    [int]$PollIntervalMs = 10,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$HftRoot = Join-Path $RepoRoot "hft_system"

$readinessScript = Join-Path $PSScriptRoot "market-open-readiness.ps1"
& powershell -ExecutionPolicy Bypass -File $readinessScript -ApiBaseUrl $ApiBaseUrl -EnvFile ($EnvFile -replace "^\.\.\\", ".\") | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Error "Market-open readiness failed. Refusing to start unattended paper trading."
    exit 1
}

Push-Location $HftRoot
try {
    $statusJson = & python -c "import json; from hft.millisecond.watchdog import read_watchdog_status; print(json.dumps(read_watchdog_status('data')))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $statusJson) {
        $status = $statusJson | ConvertFrom-Json
        if ([int]$status.active_lock_count -gt 0) {
            Write-Error "An HFT watchdog lock is already active. Refusing to launch a duplicate watchdog."
            exit 1
        }
    }

    $submitFlag = if ($DryRun) { "--dry-run" } else { "--submit-paper" }
    $argumentList = @(
        "-c",
        "from hft.millisecond.cli import run_watchdog_main; raise SystemExit(run_watchdog_main())",
        "--base-dir",
        "data",
        "--env-file",
        $EnvFile,
        "--symbols",
        $Symbols,
        "--slice-cycles",
        [string]$SliceCycles,
        "--poll-interval-ms",
        [string]$PollIntervalMs,
        "--wait-for-window",
        $submitFlag
    )
    $process = Start-Process -FilePath "python" -ArgumentList $argumentList -WorkingDirectory $HftRoot -WindowStyle Hidden -PassThru
    Write-Output (@{
        ok = $true
        message = "Started supervised Alpaca paper HFT watchdog."
        pid = $process.Id
        symbols = $Symbols
        submit_mode = if ($DryRun) { "dry_run" } else { "alpaca_paper" }
    } | ConvertTo-Json -Depth 5)
} finally {
    Pop-Location
}
