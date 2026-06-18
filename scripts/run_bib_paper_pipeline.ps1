<#
.SYNOPSIS
Run the BibTeX paper pipeline wrapper.

.DESCRIPTION
Thin PowerShell wrapper around `bib_paper_pipeline.py`. Pass a subcommand such as
`init-bib-run`, `import-bib`, `enrich-dois`, `build-queues`, `ingest-results`, or `finalize-run`
followed by its normal Python arguments.

.PARAMETER PythonExe
Python executable to use.

.PARAMETER ScriptPath
Optional override for the Python entrypoint.

.PARAMETER RemainingArgs
Subcommand and arguments forwarded to the Python entrypoint.
#>
param(
    [string]$PythonExe = "python",

    [string]$ScriptPath = "",

    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
    $ScriptPath = Join-Path $PSScriptRoot "bib_paper_pipeline.py"
}

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

& $PythonExe -B $ScriptPath @RemainingArgs
