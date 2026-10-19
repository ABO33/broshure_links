$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

if (Test-Path ".git") {
  git pull --ff-only
  if ($LASTEXITCODE -ne 0) {
    throw "Git pull failed. Resolve the repository error before starting Docker."
  }
}

$chromeProfile = $env:PRAKTIS_SERVICE_CHROME_PROFILE
if ([string]::IsNullOrWhiteSpace($chromeProfile)) {
  $chromeProfile = Join-Path $env:USERPROFILE "PraktisServiceChrome"
}

$chromeArgs = @{
  ProfileDir = $chromeProfile
  Restart = $true
}
if ($env:PRAKTIS_SERVICE_CHROME_VISIBLE -match "^(1|true|yes)$") {
  $chromeArgs.Visible = $true
}

& (Join-Path $scriptDir "launch-service-chrome.ps1") @chromeArgs

docker compose build brochure-linker
if ($LASTEXITCODE -ne 0) {
  throw "Building the brochure-linker image failed."
}

$dockerHostProbe = docker compose run --rm --no-deps --entrypoint python brochure-linker -c "import socket; print(socket.gethostbyname('host.docker.internal'))"
if ($LASTEXITCODE -ne 0) {
  throw "Docker could not probe the Chrome host address."
}
$dockerHostIp = ($dockerHostProbe | Select-String -Pattern "\b(?:\d{1,3}\.){3}\d{1,3}\b" -AllMatches).Matches.Value | Select-Object -Last 1

if (-not $dockerHostIp) {
  throw "Could not resolve host.docker.internal to an IPv4 address from Docker."
}

$env:PRAKTIS_CDP_URL = "http://${dockerHostIp}:9222"

# Verify the actual Playwright connection, not just the Windows HTTP listener.
# Chrome must be reachable from inside Docker before brochure jobs can run.
$browserProbe = @'
import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(os.environ['PRAKTIS_CDP_URL'], timeout=15000)
        if not browser.contexts:
            raise RuntimeError('Service Chrome did not expose a browser context.')
        print('Docker Playwright connected to service Chrome: ' + browser.version)
        await browser.close()

asyncio.run(main())
'@
docker compose run --rm --no-deps --entrypoint python brochure-linker -c $browserProbe
if ($LASTEXITCODE -ne 0) {
  throw "Docker cannot connect to service Chrome at $env:PRAKTIS_CDP_URL. Check Chrome and Docker networking before processing brochures."
}

$envPath = Join-Path $scriptDir ".env"
$envLines = @()
if (Test-Path -LiteralPath $envPath) {
  $envLines = @(Get-Content -LiteralPath $envPath)
}

$updated = $false
$envLines = @(
  foreach ($line in $envLines) {
    if ($line -match "^\s*PRAKTIS_CDP_URL\s*=") {
      "PRAKTIS_CDP_URL=$env:PRAKTIS_CDP_URL"
      $updated = $true
    } else {
      $line
    }
  }
)
if (-not $updated) {
  $envLines += "PRAKTIS_CDP_URL=$env:PRAKTIS_CDP_URL"
}
if (-not ($envLines -match "^\s*DISCORD_WEBHOOK_URL\s*=")) {
  $envLines += "DISCORD_WEBHOOK_URL="
}
if (-not ($envLines -match "^\s*DISCORD_INFO_WEBHOOK_URL\s*=")) {
  $envLines += "DISCORD_INFO_WEBHOOK_URL="
}
if (-not ($envLines -match "^\s*DISCORD_ERROR_WEBHOOK_URL\s*=")) {
  $envLines += "DISCORD_ERROR_WEBHOOK_URL="
}
if (-not ($envLines -match "^\s*DISCORD_ERROR_LEVEL\s*=")) {
  $envLines += "DISCORD_ERROR_LEVEL=WARNING"
}
if (-not ($envLines -match "^\s*DISCORD_LOG_LEVEL\s*=")) {
  $envLines += "DISCORD_LOG_LEVEL=ERROR"
}
$envLines | Set-Content -LiteralPath $envPath -Encoding ASCII

Write-Host "Using Chrome DevTools endpoint for Docker: $env:PRAKTIS_CDP_URL"

docker compose up -d
if ($LASTEXITCODE -ne 0) {
  throw "Starting the Docker services failed."
}
