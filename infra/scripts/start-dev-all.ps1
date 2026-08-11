param(
  [switch]$BuildStack,
  [switch]$ForceRecreateStack,
  [switch]$SkipPrePull,
  [switch]$Observability,
  [switch]$MinioTls,
  [switch]$KeepLegacyFrontends
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path ".").Path
$portsPath = Join-Path $root "config/service-ports.json"
$pidFilePath = Join-Path $root "config/dev-frontends.pids.json"

if (!(Test-Path $portsPath)) {
  throw "Ports config not found: $portsPath"
}

$ports = Get-Content $portsPath -Raw -Encoding UTF8 | ConvertFrom-Json

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
  }

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
  "-File", "infra/scripts/start-stack.ps1"
)
if ($BuildStack) { $stackArgs += "-Build" }
if ($ForceRecreateStack) { $stackArgs += "-ForceRecreate" }
if ($SkipPrePull) { $stackArgs += "-SkipPrePull" }
if ($Observability) { $stackArgs += "-Observability" }
if ($MinioTls) { $stackArgs += "-MinioTls" }

Write-Host "Starting Agent Powflow docker stack, including Vue frontends..."
& powershell @stackArgs
if ($LASTEXITCODE -ne 0) {
  throw "start-stack.ps1 failed (exit code: $LASTEXITCODE)"
}

Write-Host "All services are managed by Docker Compose."
Write-Host "web-app     : http://localhost:$($ports.web_app_port)"
Write-Host "web-admin   : http://localhost:$($ports.web_admin_port)"
Write-Host "debug-web   : http://localhost:$($ports.debug_web_port)"
Write-Host "kb-admin-web: http://localhost:$($ports.kb_admin_web_port)"
Write-Host "control     : http://localhost:$($ports.control_plane_port)"
Write-Host "execution   : http://localhost:$($ports.execution_api_port)"
Write-Host "chat        : http://localhost:$($ports.chat_plane_port)"
if ($Observability) {
  Write-Host "prometheus  : http://127.0.0.1:9090"
  Write-Host "alertmanager: http://127.0.0.1:9093"
}
if ($MinioTls) {
  Write-Host "minio      : https://127.0.0.1:$($ports.minio_api_port)"
  Write-Host "minio-ui   : https://127.0.0.1:$($ports.minio_console_port)"
}
