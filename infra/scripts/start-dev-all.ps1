param(
  [switch]$BuildStack,
  [switch]$ForceRecreateStack,
  [switch]$SkipStack
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path ".").Path
$portsPath = Join-Path $root "config/service-ports.json"
$pidFilePath = Join-Path $root "config/dev-frontends.pids.json"

if (!(Test-Path $portsPath)) {
  throw "Ports config not found: $portsPath"
}

$ports = Get-Content $portsPath -Raw | ConvertFrom-Json

if (-not $SkipStack) {
  $stackArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "infra/scripts/start-stack.ps1"
  )
  if ($BuildStack) { $stackArgs += "-Build" }
  if ($ForceRecreateStack) { $stackArgs += "-ForceRecreate" }
  Write-Host "Starting docker stack..."
  & powershell @stackArgs
  if ($LASTEXITCODE -ne 0) {
    throw "start-stack.ps1 failed (exit code: $LASTEXITCODE)"
  }
}

Write-Host "Starting debug-web..."
$debugCmd = "`$env:DEBUG_WEB_PORT='$($ports.debug_web_port)'; npm run dev"
$debugProc = Start-Process -FilePath "powershell" -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command", $debugCmd
) -WorkingDirectory (Join-Path $root "apps/debug-web") -PassThru

Write-Host "Starting kb-admin-web..."
$kbCmd = "`$env:KB_ADMIN_WEB_PORT='$($ports.kb_admin_web_port)'; npm run dev"
$kbProc = Start-Process -FilePath "powershell" -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-Command", $kbCmd
) -WorkingDirectory (Join-Path $root "apps/kb-admin-web") -PassThru

$pidPayload = [ordered]@{
  generated_at = (Get-Date).ToString("s")
  debug_web_pid = $debugProc.Id
  kb_admin_web_pid = $kbProc.Id
}
$pidPayload | ConvertTo-Json | Set-Content -Path $pidFilePath -Encoding utf8

Write-Host "Started frontends."
Write-Host "debug-web   : http://localhost:$($ports.debug_web_port) (PID: $($debugProc.Id))"
Write-Host "kb-admin-web: http://localhost:$($ports.kb_admin_web_port) (PID: $($kbProc.Id))"
Write-Host "PID file: $pidFilePath"
