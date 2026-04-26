param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "runtime-gate", "live-check", "acceptance-smoke", "route-audit", "frontend-preflight", "ui-readiness", "ui-seed", "ui-open", "ui-record", "set-frontend-target", "show-frontend-target", "env-check", "print-boot", "use-local-db", "set-db-url", "show-db-url", "set-public-urls", "show-public-urls", "set-local-port", "set-access-mode", "show-access-mode", "set-billing-mode", "show-billing", "db-up", "db-down", "preflight", "docker-diagnose", "db-check", "floor-check", "api", "api-bg", "api-status", "api-stop", "options-paper-readiness")]
    [string]$Action

    ,
    [string]$DatabaseUrl,
    [string]$FrontendUrl,
    [string]$ApiBaseUrl,
    [string]$Port,
    [string]$AccessMode,
    [string]$BillingMode,
    [string]$StripePublishableKey,
    [string]$StripeSecretKey,
    [string]$StripeWebhookSecret,
    [string]$Result,
    [string]$FirstFailedStep,
    [string]$Blockers,
    [string]$Notes
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$RunWithEnv = Join-Path $ProjectRoot "scripts\run_with_env.py"
$RuntimeManager = Join-Path $ProjectRoot "scripts\manage_api_runtime.py"
$OptionsReadiness = Join-Path $ProjectRoot "scripts\check_options_paper_readiness.py"
$EnvFile = Join-Path $ProjectRoot ".env.staging"
$ComposeFile = Join-Path $ProjectRoot "docker-compose.staging.yml"

function Get-DockerCommand {
    $candidate = Get-Command docker -ErrorAction SilentlyContinue
    if ($candidate) {
        return $candidate.Source
    }

    $fallbacks = @(
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        "C:\Program Files (x86)\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($path in $fallbacks) {
        if (Test-Path $path) {
            return $path
        }
    }

    throw "Docker CLI was not found. Install Docker Desktop or add docker.exe to PATH before using the local staging Postgres lane."
}

switch ($Action) {
    "status" {
        & $Python (Join-Path $ProjectRoot "scripts\staging_status.py") $EnvFile
    }
    "runtime-gate" {
        & $Python (Join-Path $ProjectRoot "scripts\staging_runtime_gate.py") $EnvFile
    }
    "live-check" {
        & $Python (Join-Path $ProjectRoot "scripts\check_live_staging.py") $EnvFile
    }
    "acceptance-smoke" {
        & $Python (Join-Path $ProjectRoot "scripts\run_staging_acceptance.py") $EnvFile
    }
    "route-audit" {
        & $Python (Join-Path $ProjectRoot "scripts\audit_non_chart_routes.py")
    }
    "set-frontend-target" {
        if (-not $ApiBaseUrl) {
            throw "ApiBaseUrl is required for -Action set-frontend-target"
        }
        & $Python (Join-Path $ProjectRoot "scripts\set_frontend_api_target.py") --api-base-url $ApiBaseUrl
    }
    "show-frontend-target" {
        & $Python (Join-Path $ProjectRoot "scripts\inspect_frontend_api_target.py")
    }
    "frontend-preflight" {
        & $Python (Join-Path $ProjectRoot "scripts\check_frontend_ui_preflight.py")
    }
    "ui-readiness" {
        & $Python (Join-Path $ProjectRoot "scripts\check_non_chart_ui_readiness.py")
    }
    "ui-seed" {
        & $Python (Join-Path $ProjectRoot "scripts\seed_non_chart_ui_tenant.py") $EnvFile
    }
    "ui-open" {
        & $Python (Join-Path $ProjectRoot "scripts\open_non_chart_ui_session.py")
    }
    "ui-record" {
        if (-not $Result) {
            throw "Result is required for -Action ui-record"
        }
        $command = @(
            (Join-Path $ProjectRoot "scripts\record_non_chart_ui_result.py"),
            "--result", $Result
        )
        if ($FirstFailedStep) { $command += @("--first-failed-step", $FirstFailedStep) }
        if ($Blockers) { $command += @("--blockers", $Blockers) }
        if ($Notes) { $command += @("--notes", $Notes) }
        & $Python @command
    }
    "env-check" {
        & $Python (Join-Path $ProjectRoot "scripts\validate_staging_env.py") $EnvFile
    }
    "print-boot" {
        & $Python (Join-Path $ProjectRoot "scripts\print_staging_boot_command.py")
    }
    "use-local-db" {
        & $Python (Join-Path $ProjectRoot "scripts\use_local_staging_postgres.py") $EnvFile
    }
    "set-db-url" {
        if (-not $DatabaseUrl) {
            throw "DatabaseUrl is required for -Action set-db-url"
        }
        & $Python (Join-Path $ProjectRoot "scripts\set_staging_database_url.py") $DatabaseUrl $EnvFile
    }
    "show-db-url" {
        & $Python (Join-Path $ProjectRoot "scripts\inspect_staging_database_url.py") $EnvFile
    }
    "set-public-urls" {
        if (-not $FrontendUrl -or -not $ApiBaseUrl) {
            throw "FrontendUrl and ApiBaseUrl are required for -Action set-public-urls"
        }
        & $Python (Join-Path $ProjectRoot "scripts\set_staging_public_urls.py") --frontend-url $FrontendUrl --api-base-url $ApiBaseUrl $EnvFile
    }
    "show-public-urls" {
        & $Python (Join-Path $ProjectRoot "scripts\inspect_staging_public_urls.py") $EnvFile
    }
    "set-local-port" {
        if (-not $Port) {
            throw "Port is required for -Action set-local-port"
        }
        & $Python (Join-Path $ProjectRoot "scripts\set_staging_local_port.py") --port $Port $EnvFile
    }
    "set-access-mode" {
        if (-not $AccessMode) {
            throw "AccessMode is required for -Action set-access-mode"
        }
        & $Python (Join-Path $ProjectRoot "scripts\set_staging_access_mode.py") --mode $AccessMode $EnvFile
    }
    "show-access-mode" {
        & $Python (Join-Path $ProjectRoot "scripts\inspect_staging_access_mode.py") $EnvFile
    }
    "set-billing-mode" {
        if (-not $BillingMode) {
            throw "BillingMode is required for -Action set-billing-mode"
        }
        $command = @(
            (Join-Path $ProjectRoot "scripts\set_staging_billing_mode.py"),
            "--mode", $BillingMode
        )
        if ($StripePublishableKey) { $command += @("--publishable-key", $StripePublishableKey) }
        if ($StripeSecretKey) { $command += @("--secret-key", $StripeSecretKey) }
        if ($StripeWebhookSecret) { $command += @("--webhook-secret", $StripeWebhookSecret) }
        $command += $EnvFile
        & $Python @command
    }
    "show-billing" {
        & $Python (Join-Path $ProjectRoot "scripts\inspect_staging_billing.py") $EnvFile
    }
    "db-up" {
        $Docker = Get-DockerCommand
        & $Docker compose -f $ComposeFile up -d postgres
    }
    "db-down" {
        $Docker = Get-DockerCommand
        & $Docker compose -f $ComposeFile down
    }
    "preflight" {
        & $Python (Join-Path $ProjectRoot "scripts\check_local_staging_prereqs.py")
    }
    "docker-diagnose" {
        & $Python (Join-Path $ProjectRoot "scripts\diagnose_docker_desktop.py")
    }
    "db-check" {
        & $Python $RunWithEnv $EnvFile -- $Python (Join-Path $ProjectRoot "scripts\check_staging_database.py")
    }
    "floor-check" {
        & $Python $RunWithEnv $EnvFile -- $Python (Join-Path $ProjectRoot "scripts\production_floor_check.py") --probe-worker
    }
    "api" {
        & $Python (Join-Path $ProjectRoot "scripts\staging_runtime_gate.py") $EnvFile
        if ($LASTEXITCODE -ne 0) {
            throw "Staging runtime gate is blocked. Fix the reported runtime blocker before starting the API."
        }
        & $Python $RunWithEnv $EnvFile -- $Python -m backend.app
    }
    "api-bg" {
        & $Python $RuntimeManager start --env-file $EnvFile
    }
    "api-status" {
        & $Python $RuntimeManager status --env-file $EnvFile
    }
    "api-stop" {
        & $Python $RuntimeManager stop --env-file $EnvFile
    }
    "options-paper-readiness" {
        & $Python $OptionsReadiness $EnvFile
    }
}
