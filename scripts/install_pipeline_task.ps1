$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$orchestrator = Join-Path $root "scripts\run_pipeline.py"
$taskName = "ASG Airlines Data Pipeline"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python executable not found: $python"
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ('"{0}"' -f $orchestrator) `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed scheduled task '$taskName' to run every 30 minutes."
Write-Host "Repository: $root"
Write-Host "Power BI Desktop refresh remains a manual final step."
