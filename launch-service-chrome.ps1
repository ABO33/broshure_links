param(
  [string]$ProfileDir = "$env:USERPROFILE\PraktisServiceChrome",
  [int]$Port = 9222,
  [switch]$Visible,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"

function Get-ServiceChromeProcesses {
  param([string]$ProfileDir)

  $resolvedProfile = [System.IO.Path]::GetFullPath($ProfileDir).TrimEnd("\")
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
      Where-Object {
        $_.CommandLine -and
        $_.CommandLine.IndexOf("--user-data-dir", [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.CommandLine.IndexOf($resolvedProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
      }
  }
  catch {
    @()
  }
}

function Stop-ServiceChromeProcesses {
  param(
    [string]$ProfileDir,
    [int]$Port
  )

  $listeners = @(Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess })
  foreach ($listener in $listeners) {
    Write-Host "Stopping Chrome DevTools listener process $($listener.OwningProcess)."
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
  }

  $processes = @(Get-ServiceChromeProcesses -ProfileDir $ProfileDir)
  foreach ($process in $processes) {
    Write-Host "Stopping service Chrome process $($process.ProcessId)."
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($processes.Count -gt 0) {
    Start-Sleep -Seconds 2
  }
}

function Test-DevToolsEndpoint {
  param([int]$Port)

  try {
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
    return [bool]$version.webSocketDebuggerUrl
  }
  catch {
    return $false
  }
}

function Join-ProcessArguments {
  param([string[]]$Arguments)

  ($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') {
      '"' + ($_ -replace '"', '\"') + '"'
    }
    else {
      $_
    }
  }) -join " "
}

if ($Restart) {
  Stop-ServiceChromeProcesses -ProfileDir $ProfileDir -Port $Port
}

if (Test-DevToolsEndpoint -Port $Port) {
  Write-Host "Chrome DevTools is already ready on port $Port."
  return
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
  throw "Port $Port is listening, but it is not a Chrome DevTools endpoint. Stop the process using this port or change the port."
}

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
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
  "--window-size=1366,768",
  "--lang=bg-BG",
  "--disable-blink-features=AutomationControlled",
  "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

if ($Visible) {
  $arguments += @("--new-window", "https://praktis.bg")
  $windowStyle = "Normal"
  $mode = "visible"
}
else {
  $logFile = Join-Path $ProfileDir "chrome-headless.log"
  $arguments += @(
    "--headless",
    "--disable-gpu",
    "--enable-logging",
    "--v=1",
    "--log-file=$logFile",
    "https://praktis.bg"
  )
  $windowStyle = "Hidden"
  $mode = "headless"
}

$argumentLine = Join-ProcessArguments -Arguments $arguments
Start-Process -FilePath $chrome -ArgumentList $argumentLine -WindowStyle $windowStyle
for ($attempt = 1; $attempt -le 30; $attempt++) {
  if (Test-DevToolsEndpoint -Port $Port) {
    Write-Host "Started $mode service Chrome profile at $ProfileDir on port $Port."
    return
  }
  Start-Sleep -Seconds 1
}

throw "Chrome started, but DevTools did not become ready on http://127.0.0.1:$Port/json/version. Check $ProfileDir\chrome-headless.log or run with PRAKTIS_SERVICE_CHROME_VISIBLE=1."
