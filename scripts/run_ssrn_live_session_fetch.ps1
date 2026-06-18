<#
.SYNOPSIS
Run the SSRN live-session fetcher.

.DESCRIPTION
PowerShell wrapper around `ssrn_live_session_fetch.py` for serial PDF downloads
through an already authorized Edge remote-debugging session.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$InputCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutDir,

    [string]$PythonExe = "python",

    [string]$ScriptPath = "",

    [int]$DebugPort = 9222,

    [bool]$PrepareLogin = $true,

    [bool]$LaunchEdgeIfMissing = $true,

    [bool]$WaitForUserLogin = $true,

    [string]$PrepareLoginsScriptPath = "",

    [int]$PageWaitSeconds = 10,

    [int]$InterItemSleepSeconds = 5,

    [int]$ViewerWsTimeoutSeconds = 240,

    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
    $ScriptPath = Join-Path $PSScriptRoot "ssrn_live_session_fetch.py"
}

if ([string]::IsNullOrWhiteSpace($PrepareLoginsScriptPath)) {
    $PrepareLoginsScriptPath = Join-Path $PSScriptRoot "prepare_live_session_logins.ps1"
}

if (-not (Test-Path -LiteralPath $InputCsv)) {
    throw "Input CSV not found: $InputCsv"
}

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

if ($PrepareLogin) {
    if (-not (Test-Path -LiteralPath $PrepareLoginsScriptPath)) {
        throw "Pre-login script not found: $PrepareLoginsScriptPath"
    }

    & $PrepareLoginsScriptPath `
        -DebugPort $DebugPort `
        -LaunchIfMissing $LaunchEdgeIfMissing `
        -WaitForUser $WaitForUserLogin
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$args = @(
    $ScriptPath,
    "--input-csv", $InputCsv,
    "--out-dir", $OutDir,
    "--debug-port", $DebugPort,
    "--page-wait-seconds", $PageWaitSeconds,
    "--inter-item-sleep-seconds", $InterItemSleepSeconds,
    "--viewer-ws-timeout-seconds", $ViewerWsTimeoutSeconds
)

if ($Limit -gt 0) {
    $args += @("--limit", $Limit)
}

& $PythonExe @args

Write-Host "Done. Output directory: $OutDir"
