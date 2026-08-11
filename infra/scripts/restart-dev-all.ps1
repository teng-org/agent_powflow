param(
  [switch]$BuildStack,
  [switch]$ForceRecreateStack,
  [switch]$WithVolumes,
  [switch]$SkipPrePull,
  [switch]$Observability,
  [switch]$MinioTls,
  [switch]$KeepLegacyFrontends
)

$ErrorActionPreference = "Stop"

$stopArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "infra/scripts/stop-dev-all.ps1"
)
if ($WithVolumes) { $stopArgs += "-WithVolumes" }
if ($KeepLegacyFrontends) { $stopArgs += "-KeepLegacyFrontends" }
if ($Observability) { $stopArgs += "-Observability" }
if ($MinioTls) { $stopArgs += "-MinioTls" }

Write-Host "Restart step 1/2: stopping all services..."
& powershell @stopArgs
if ($LASTEXITCODE -ne 0) {
  throw "stop-dev-all.ps1 failed (exit code: $LASTEXITCODE)"
}

$startArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "infra/scripts/start-dev-all.ps1"
)
if ($BuildStack) { $startArgs += "-BuildStack" }
if ($ForceRecreateStack) { $startArgs += "-ForceRecreateStack" }
if ($SkipPrePull) { $startArgs += "-SkipPrePull" }
if ($KeepLegacyFrontends) { $startArgs += "-KeepLegacyFrontends" }
if ($Observability) { $startArgs += "-Observability" }
if ($MinioTls) { $startArgs += "-MinioTls" }

Write-Host "Restart step 2/2: starting all services..."
& powershell @startArgs
if ($LASTEXITCODE -ne 0) {
  throw "start-dev-all.ps1 failed (exit code: $LASTEXITCODE)"
}

Write-Host "Agent Powflow restart complete."
