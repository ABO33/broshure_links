$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

if (Test-Path ".git") {
  git pull --ff-only
}

$chromeArgs = @("-Restart")
if ($env:PRAKTIS_SERVICE_CHROME_VISIBLE -match "^(1|true|yes)$") {
  $chromeArgs += "-Visible"
}

& (Join-Path $scriptDir "launch-service-chrome.ps1") @chromeArgs

docker compose build brochure-linker

$dockerHostProbe = docker compose run --rm --no-deps --entrypoint python brochure-linker -c "import socket; print(socket.gethostbyname('host.docker.internal'))"
$dockerHostIp = ($dockerHostProbe | Select-String -Pattern "\b(?:\d{1,3}\.){3}\d{1,3}\b" -AllMatches).Matches.Value | Select-Object -Last 1

if (-not $dockerHostIp) {
  throw "Could not resolve host.docker.internal to an IPv4 address from Docker."
}

$env:PRAKTIS_CDP_URL = "http://${dockerHostIp}:9222"
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
