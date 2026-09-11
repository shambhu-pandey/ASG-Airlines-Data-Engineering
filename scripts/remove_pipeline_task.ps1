$ErrorActionPreference = "Stop"
$taskName = "ASG Airlines Data Pipeline"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed scheduled task '$taskName' if it existed."
