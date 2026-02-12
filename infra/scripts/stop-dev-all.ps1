param(
  [switch]$KeepStack,
  [switch]$WithVolumes
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path ".").Path
$pidFilePath = Join-Path $root "config/dev-frontends.pids.json"

function Stop-IfRunning([int]$pid, [string]$name) {
  try {
    $proc = Get-Process -Id $pid -ErrorAction Stop
    Stop-Process -Id $pid -Force -ErrorAction Stop
    Write-Host "Stopped $name (PID: $pid, Name: $($proc.ProcessName))"
  } catch {
    Write-Host "$name process not running (PID: $pid)"
  }
}

if (Test-Path $pidFilePath) {
  $pids = Get-Content $pidFilePath -Raw | ConvertFrom-Json
  if ($pids.debug_web_pid) {
    Stop-IfRunning -pid ([int]$pids.debug_web_pid) -name "debug-web"
  }
  if ($pids.kb_admin_web_pid) {
    Stop-IfRunning -pid ([int]$pids.kb_admin_web_pid) -name "kb-admin-web"
  }
  Remove-Item $pidFilePath -Force
  Write-Host "Removed PID file: $pidFilePath"
} else {
  Write-Host "PID file not found, skip frontend process stop: $pidFilePath"
}

if (-not $KeepStack) {
  $stackArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "infra/scripts/stop-stack.ps1"
  )
  if ($WithVolumes) { $stackArgs += "-WithVolumes" }
  Write-Host "Stopping docker stack..."
  & powershell @stackArgs
  if ($LASTEXITCODE -ne 0) {
    throw "stop-stack.ps1 failed (exit code: $LASTEXITCODE)"
  }
}
