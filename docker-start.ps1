$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

if (Test-Path ".git") {
  git pull --ff-only
}

& (Join-Path $scriptDir "launch-service-chrome.ps1")
$env:PRAKTIS_CDP_URL = "http://host.docker.internal:9222"

docker compose up -d --build
