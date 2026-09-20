param(
    [string]$Python = "python",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$radarRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $radarRoot "logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logPath = Join-Path $logRoot ((Get-Date -Format "yyyy-MM-dd_HHmmss") + ".log")
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$radarArgs = @((Join-Path $radarRoot "local_runner.py"))
if ($DryRun) { $radarArgs += "--dry-run" }
Set-Location -LiteralPath $radarRoot
# PowerShell 5.1 treats native stderr as an error record; preserve the Python exit code.
$ErrorActionPreference = "Continue"
& $Python @radarArgs *> $logPath
$radarExit = $LASTEXITCODE
Write-Output "Idea-radar exit=$radarExit log=$logPath"
exit $radarExit
