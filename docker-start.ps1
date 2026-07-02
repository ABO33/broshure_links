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
@(
  "PRAKTIS_CDP_URL=$env:PRAKTIS_CDP_URL"
) | Set-Content -LiteralPath (Join-Path $scriptDir ".env") -Encoding ASCII

Write-Host "Using Chrome DevTools endpoint for Docker: $env:PRAKTIS_CDP_URL"

docker compose up -d
