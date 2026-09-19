param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$At = "08:00"
)
$ErrorActionPreference = "Stop"
$radarRoot = Split-Path -Parent $PSScriptRoot
$Python = (Resolve-Path -LiteralPath $Python).Path
$env:PYTHONUTF8 = "1"
& $Python (Join-Path $radarRoot "local_runner.py") --check
if ($LASTEXITCODE -ne 0) { throw "Complete local settings and ChatGPT login before installing the task." }
$taskName = "IdeaRadar-Daily"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "IdeaRadar-Daily already exists. Inspect it before replacing it."
}
$runner = Join-Path $PSScriptRoot "run-local.ps1"
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Python "{1}"' -f $runner, $Python
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments -WorkingDirectory $radarRoot
$firstRun = [datetime]::Today.Add(([datetime]::ParseExact($At, "HH:mm", [cultureinfo]::InvariantCulture)).TimeOfDay)
if ($firstRun -le (Get-Date)) { $firstRun = $firstRun.AddDays(1) }
$trigger = New-ScheduledTaskTrigger -Daily -At $firstRun
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Idea-radar: local ChatGPT subscription analysis and Discord delivery" | Out-Null
Write-Output "Installed IdeaRadar-Daily at $At (PC local time; signed-in user required)."
