param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$EnvFile = ".env.staging",
    [string]$Symbols = "AAPL",
    [string]$ReportPath = "runtime-logs/market-open-readiness.json",
    [switch]$SkipHftFeedCheck
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ReportFullPath = Join-Path $RepoRoot $ReportPath
$ReportDir = Split-Path -Parent $ReportFullPath
if (-not (Test-Path $ReportDir)) {
    New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
}

function New-CheckResult {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Status,
        [string]$Detail,
        [object]$Metadata = @{}
    )
    [ordered]@{
        name = $Name
        ok = $Ok
        status = $Status
        detail = $Detail
        metadata = $Metadata
        checked_at = (Get-Date).ToUniversalTime().ToString("o")
    }
}

function Invoke-JsonCheck {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 10
        return New-CheckResult -Name $Name -Ok $true -Status "ok" -Detail "Endpoint responded." -Metadata $response
    } catch {
        return New-CheckResult -Name $Name -Ok $false -Status "error" -Detail $_.Exception.Message
    }
}

$checks = New-Object System.Collections.Generic.List[object]
$checks.Add((Invoke-JsonCheck -Name "backend_healthz" -Url "$ApiBaseUrl/api/healthz"))
$checks.Add((Invoke-JsonCheck -Name "backend_readyz" -Url "$ApiBaseUrl/api/readyz"))
$checks.Add((Invoke-JsonCheck -Name "trade_automation_safety_state" -Url "$ApiBaseUrl/api/orgs/trade-automation/safety-state"))
$checks.Add((Invoke-JsonCheck -Name "alpaca_paper_readiness" -Url "$ApiBaseUrl/api/orgs/trade-automation/alpaca-paper-readiness"))

$frontendPackage = Test-Path (Join-Path $RepoRoot "frontend/package.json")
$checks.Add((New-CheckResult -Name "frontend_project" -Ok $frontendPackage -Status ($(if ($frontendPackage) { "ok" } else { "missing" })) -Detail ($(if ($frontendPackage) { "Frontend package is present." } else { "frontend/package.json is missing." }))))

$hftProject = Join-Path $RepoRoot "hft_system"
$hftConfig = Test-Path (Join-Path $hftProject "configs/millisecond.yaml")
$checks.Add((New-CheckResult -Name "hft_config" -Ok $hftConfig -Status ($(if ($hftConfig) { "ok" } else { "missing" })) -Detail ($(if ($hftConfig) { "Millisecond HFT config is present." } else { "hft_system/configs/millisecond.yaml is missing." }))))

$envPath = Join-Path $RepoRoot $EnvFile
$envPresent = Test-Path $envPath
$checks.Add((New-CheckResult -Name "env_file" -Ok $envPresent -Status ($(if ($envPresent) { "ok" } else { "missing" })) -Detail ($(if ($envPresent) { "$EnvFile is present." } else { "$EnvFile is missing." }))))

try {
    $marketReadyOutput = & python (Join-Path $RepoRoot "scripts/trading_safety_tools.py") market-ready --env-file $EnvFile --tenant-slug systematic-equities 2>&1
    $checks.Add((New-CheckResult -Name "market_ready_cli" -Ok ($LASTEXITCODE -eq 0) -Status ($(if ($LASTEXITCODE -eq 0) { "ok" } else { "error" })) -Detail (($marketReadyOutput | Out-String).Trim())))
} catch {
    $checks.Add((New-CheckResult -Name "market_ready_cli" -Ok $false -Status "error" -Detail $_.Exception.Message))
}

if (-not $SkipHftFeedCheck -and $hftConfig -and $envPresent) {
    Push-Location $hftProject
    try {
        $statusOutput = & python -c "import json; from hft.millisecond.watchdog import read_watchdog_status; print(json.dumps(read_watchdog_status('data'), indent=2))" 2>&1
        $checks.Add((New-CheckResult -Name "hft_watchdog_status_command" -Ok ($LASTEXITCODE -eq 0) -Status ($(if ($LASTEXITCODE -eq 0) { "ok" } else { "error" })) -Detail (($statusOutput | Out-String).Trim())))
    } catch {
        $checks.Add((New-CheckResult -Name "hft_watchdog_status_command" -Ok $false -Status "error" -Detail $_.Exception.Message))
    } finally {
        Pop-Location
    }
} else {
    $checks.Add((New-CheckResult -Name "hft_watchdog_status_command" -Ok $true -Status "skipped" -Detail "Skipped HFT status check by flag or missing prerequisites."))
}

$strongFailures = @($checks | Where-Object { -not $_.ok -and $_.name -in @("backend_healthz", "backend_readyz", "env_file", "hft_config") })
$weakFailures = @($checks | Where-Object { -not $_.ok -and $_.name -notin @("backend_healthz", "backend_readyz", "env_file", "hft_config") })
$report = [ordered]@{
    ok = ($strongFailures.Count -eq 0)
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    strong_failure_count = $strongFailures.Count
    weak_failure_count = $weakFailures.Count
    checks = $checks
}
$report | ConvertTo-Json -Depth 12 | Set-Content -Path $ReportFullPath -Encoding UTF8
$report | ConvertTo-Json -Depth 12

if ($strongFailures.Count -gt 0) {
    exit 1
}
exit 0
