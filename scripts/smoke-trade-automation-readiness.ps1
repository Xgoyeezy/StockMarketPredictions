param(
    [string]$BackendApiUrl = "http://127.0.0.1:8000/api",
    [string]$FrontendUrl = "http://localhost:5173",
    [switch]$SkipHealth,
    [switch]$SkipFullAutomation
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
$Npm = "npm.cmd"
$Failures = New-Object System.Collections.Generic.List[string]

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    Write-Host "==> $Name"
    try {
        $global:LASTEXITCODE = 0
        & $Body
        if ($LASTEXITCODE -ne 0) {
            throw "Native command exited with code $LASTEXITCODE"
        }
        Write-Host "OK  $Name"
    } catch {
        $Failures.Add("$Name :: $($_.Exception.Message)") | Out-Null
        Write-Warning "FAIL $Name :: $($_.Exception.Message)"
    }
}

if (!(Test-Path $Python)) {
    throw "Backend virtualenv Python not found at $Python"
}

if (-not $SkipHealth) {
    Invoke-Step "backend health" {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$BackendApiUrl/healthz" -TimeoutSec 10
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            throw "Unexpected backend health status $($response.StatusCode)"
        }
    }
    Invoke-Step "frontend health" {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $FrontendUrl -TimeoutSec 10
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "Unexpected frontend status $($response.StatusCode)"
        }
    }
}

Invoke-Step "backup readiness manifest" {
    & $Python (Join-Path $RepoRoot "scripts\trade_automation_readiness.py") backup --env-file (Join-Path $RepoRoot ".env")
}

Invoke-Step "runtime route readiness" {
    & $Python (Join-Path $RepoRoot "scripts\trade_automation_readiness.py") runtime --backend-url $BackendApiUrl --frontend-url $FrontendUrl --timeout 30
}

if (-not $SkipHealth) {
    Invoke-Step "backend readyz" {
        $lastError = $null
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri "$BackendApiUrl/readyz" -TimeoutSec 10
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                    return
                }
                $lastError = "Unexpected backend readyz status $($response.StatusCode)"
            } catch {
                $lastError = $_.Exception.Message
            }
            Start-Sleep -Seconds 2
        }
        throw $lastError
    }
}

Invoke-Step "pip check" {
    & $Python -m pip check
}

Invoke-Step "action schema validation" {
@'
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services.automation_trade_readiness_service import EXPECTED_ACTION_FLAGS

for action in EXPECTED_ACTION_FLAGS:
    assert OrganizationTradeAutomationActionRequest(action=action).action == action
print("validated", len(EXPECTED_ACTION_FLAGS), "trade automation actions")
'@ | & $Python -
}

Invoke-Step "snapshot shape validation" {
@'
from backend.services import trade_automation_service
from backend.services.automation_trade_readiness_service import REQUIRED_MODULES

state = trade_automation_service._normalize_trade_automation_profile_state({})
snapshot = {}
for key, _label in REQUIRED_MODULES:
    if key in {"trade_automation_readiness"}:
        continue
    snapshot[key] = {"status": "ready"}
assert "limited_live_next_tier_cap_canary_enabled" in state["settings"]
assert "exit_watchdog_last_report" in state["runtime"]
assert "limited_live_approval_ledger" in state["runtime"]
print("snapshot defaults validated")
'@ | & $Python -
}

Invoke-Step "readiness service tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_trade_readiness_service.py"
}

Invoke-Step "runtime tooling tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_runtime_tooling.py"
}

Invoke-Step "limited-live ladder tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_limited_live*.py"
}

if (-not $SkipFullAutomation) {
    Invoke-Step "full automation tests" {
        & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_*.py"
    }
}

Invoke-Step "frontend build" {
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        & $Npm run build
    } finally {
        Pop-Location
    }
}

if ($Failures.Count -gt 0) {
    Write-Host ""
    Write-Warning "Trade Automation readiness smoke failed:"
    foreach ($failure in $Failures) {
        Write-Warning " - $failure"
    }
    exit 1
}

Write-Host ""
Write-Host "Trade Automation readiness smoke passed."
