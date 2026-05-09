param(
    [string]$EnvFile = "..\.env.staging",
    [string]$Symbols = "AAPL",
    [switch]$SubmitPaper,
    [int]$MaxSlices = 0
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Command = @(
    "..\backend\.venv\Scripts\hft-watch-millisecond-engine.exe",
    "--env-file", $EnvFile,
    "--symbols", $Symbols,
    "--wait-for-window",
    "--max-slices", "$MaxSlices"
)

if ($SubmitPaper) {
    $Command += "--submit-paper"
} else {
    $Command += "--dry-run"
}

& $Command[0] @($Command[1..($Command.Length - 1)])
exit $LASTEXITCODE
