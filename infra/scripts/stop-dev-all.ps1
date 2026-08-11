param(
  [switch]$WithVolumes,
  [switch]$Observability,
  [switch]$MinioTls,
  [switch]$KeepLegacyFrontends
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path ".").Path
$portsPath = Join-Path $root "config/service-ports.json"
$pidFilePath = Join-Path $root "config/dev-frontends.pids.json"

function Stop-ProcessIfRunning([int]$ProcessId, [string]$Name) {
  try {
    $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    $cmd = [string]$procInfo.CommandLine
    if ($cmd -notmatch "vite|npm run dev|node.*vite") {
      Write-Host "Skip legacy $Name PID $ProcessId because it is not a Vite frontend process."
      return
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction Stop
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped legacy $Name (PID: $ProcessId, Name: $($proc.ProcessName))"
  } catch {
    Write-Host "Legacy $Name process not running (PID: $ProcessId)"
  }
}

function Stop-LegacyFrontendProcesses {
  if (Test-Path $pidFilePath) {
    $pids = Get-Content $pidFilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($pids.debug_web_pid) { Stop-ProcessIfRunning -ProcessId ([int]$pids.debug_web_pid) -Name "debug-web" }
    if ($pids.kb_admin_web_pid) { Stop-ProcessIfRunning -ProcessId ([int]$pids.kb_admin_web_pid) -Name "kb-admin-web" }
    if ($pids.web_app_pid) { Stop-ProcessIfRunning -ProcessId ([int]$pids.web_app_pid) -Name "web-app" }
    if ($pids.web_admin_pid) { Stop-ProcessIfRunning -ProcessId ([int]$pids.web_admin_pid) -Name "web-admin" }
    Remove-Item $pidFilePath -Force
    Write-Host "Removed legacy frontend PID file: $pidFilePath"
  } else {
    Write-Host "Legacy frontend PID file not found: $pidFilePath"
  }

  if (!(Test-Path $portsPath)) { return }
  $ports = Get-Content $portsPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $frontendPorts = @(
    [int]$ports.debug_web_port,
    [int]$ports.kb_admin_web_port,
    [int]$ports.web_app_port,
    [int]$ports.web_admin_port
  )
  foreach ($port in $frontendPorts) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $listeners) {
      try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop
        $cmd = [string]$proc.CommandLine
        if ($cmd -match "vite|npm run dev|node.*vite") {
          Stop-Process -Id $procId -Force -ErrorAction Stop
          Write-Host "Stopped legacy frontend listener on port $port (PID: $procId)"
        }
      } catch {
        Write-Host "Skip listener cleanup on port $port (PID: $procId)"
      }
    }
  }
}

if (-not $KeepLegacyFrontends) {
  Stop-LegacyFrontendProcesses
}

$stackArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "infra/scripts/stop-stack.ps1"
)
if ($WithVolumes) { $stackArgs += "-WithVolumes" }
if ($Observability) { $stackArgs += "-Observability" }
if ($MinioTls) { $stackArgs += "-MinioTls" }

Write-Host "Stopping Agent Powflow docker stack..."
& powershell @stackArgs
if ($LASTEXITCODE -ne 0) {
  throw "stop-stack.ps1 failed (exit code: $LASTEXITCODE)"
}

Write-Host "All Docker-managed services stopped."
