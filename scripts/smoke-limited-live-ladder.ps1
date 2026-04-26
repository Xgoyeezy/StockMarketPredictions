param(
    [string]$BackendUrl = "http://127.0.0.1:8000/api/healthz",
    [string]$FrontendUrl = "http://localhost:5173",
    [switch]$SkipHealth
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
        & $Body
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
        $response = Invoke-WebRequest -UseBasicParsing -Uri $BackendUrl -TimeoutSec 10
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

Invoke-Step "pip check" {
    & $Python -m pip check
}

Invoke-Step "action schema validation" {
@'
from backend.schemas import OrganizationTradeAutomationActionRequest

actions = [
    "run_limited_live_next_tier_cap_canary_review",
    "run_limited_live_broker_reconciliation",
    "run_limited_live_session_closeout",
    "run_limited_live_higher_cap_report",
    "submit_limited_live_operator_checklist",
]
for action in actions:
    assert OrganizationTradeAutomationActionRequest(action=action).action == action
print("validated", len(actions), "limited-live ladder actions")
'@ | & $Python -
}

Invoke-Step "snapshot shape validation" {
@'
from backend.services import trade_automation_service

state = trade_automation_service._normalize_trade_automation_profile_state({})
required_settings = [
    "limited_live_next_tier_cap_canary_enabled",
    "limited_live_higher_cap_report_enabled",
    "limited_live_operator_checklist_required",
]
required_runtime = [
    "limited_live_next_tier_cap_canary_last_report",
    "limited_live_broker_reconciliation_last_report",
    "limited_live_session_closeout_last_report",
    "limited_live_higher_cap_report_last_report",
    "limited_live_approval_ledger",
]
for key in required_settings:
    assert key in state["settings"], key
for key in required_runtime:
    assert key in state["runtime"], key
print("snapshot defaults validated")
'@ | & $Python -
}

Invoke-Step "targeted limited-live ladder tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_limited_live_safety_ladder_service.py"
}

Invoke-Step "limited-live regression tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_limited_live*.py"
}

Invoke-Step "full automation tests" {
    & $Python -m unittest discover -s (Join-Path $RepoRoot "tests") -p "test_automation_*.py"
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
    Write-Warning "Limited-live ladder smoke failed:"
    foreach ($failure in $Failures) {
        Write-Warning " - $failure"
    }
    exit 1
}

Write-Host ""
Write-Host "Limited-live ladder smoke passed."
