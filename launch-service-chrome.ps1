param(
  [string]$ProfileDir = "$env:USERPROFILE\PraktisServiceChrome",
  [int]$Port = 9222
)

$ErrorActionPreference = "Stop"

$connection = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($connection) {
  Write-Host "Chrome remote debugging is already listening on port $Port."
  return
}

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
  throw "Google Chrome was not found. Install Chrome or update launch-service-chrome.ps1 with the correct path."
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

$arguments = @(
  "--remote-debugging-port=$Port",
  "--remote-debugging-address=0.0.0.0",
  "--remote-allow-origins=*",
  "--user-data-dir=$ProfileDir",
  "--no-first-run",
  "--new-window",
  "https://praktis.bg"
)

Start-Process -FilePath $chrome -ArgumentList $arguments -WindowStyle Normal
Write-Host "Started service Chrome profile at $ProfileDir on port $Port."
