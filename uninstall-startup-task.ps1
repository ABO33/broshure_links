$ErrorActionPreference = "Stop"

$taskName = "Praktis Brochure Linker"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Removed startup task: $taskName"
