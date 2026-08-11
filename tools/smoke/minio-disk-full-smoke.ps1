param(
  [ValidateRange(64, 512)][int]$DataMiB = 128,
  [ValidateRange(1, 8)][int]$ObjectMiB = 4,
  [ValidateRange(2, 128)][int]$MaxObjects = 64,
  [string]$ExecutionContainer = "agentpowflow-execution-api",
  [string]$ProductionMinioContainer = "agentpowflow-minio",
  [string]$MinioImage = "minio/minio:RELEASE.2025-09-07T16-13-09Z"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-DockerText {
  param([Parameter(Mandatory = $true)][string[]]$DockerArguments)
  $previousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $raw = & docker @DockerArguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  $output = (($raw | ForEach-Object { [string]$_ }) -join "`n").Trim()
  if ($exitCode -ne 0) { throw "Docker command failed (exit=$exitCode): $output" }
  return $output
}

function Assert-RunningContainer {
  param([Parameter(Mandatory = $true)][string]$Name)
  if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_.-]{0,62}$') { throw "Unsafe container name: $Name" }
  $actual = Invoke-DockerText -DockerArguments @("inspect", "--format", "{{.Name}}|{{.State.Running}}", $Name)
  if ($actual -ne "/$Name|true") { throw "Expected running container is unavailable: $Name" }
}

Assert-RunningContainer -Name $ExecutionContainer
Assert-RunningContainer -Name $ProductionMinioContainer
if ($MinioImage -notmatch '^minio/minio:RELEASE\.[0-9TZ-]+$') { throw "MinioImage must be an immutable release tag." }
if (($DataMiB * 1MB) -gt 512MB -or ($ObjectMiB * $MaxObjects) -gt 1024) { throw "Configured capacity probe exceeds its safety bound." }

$networksText = Invoke-DockerText -DockerArguments @(
  "inspect", "--format", "{{range `$name,`$config := .NetworkSettings.Networks}}{{println `$name}}{{end}}", $ExecutionContainer
)
$networks = @($networksText -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($networks.Count -ne 1 -or $networks[0] -notmatch '^[A-Za-z0-9_.-]{1,64}$') {
  throw "Execution container must have exactly one safely named Docker network."
}
$network = $networks[0]

$suffix = [guid]::NewGuid().ToString("N")
$isolatedContainer = "agentpowflow-minio-diskfull-$suffix"
$bucket = "apf-diskfull-$suffix"
$containerProbe = "/tmp/apf_minio_diskfull_$suffix.py"
if ($isolatedContainer -notmatch '^agentpowflow-minio-diskfull-[a-f0-9]{32}$' -or
    $bucket -notmatch '^apf-diskfull-[a-f0-9]{32}$' -or
    $containerProbe -notmatch '^/tmp/apf_minio_diskfull_[a-f0-9]{32}\.py$') {
  throw "Generated isolation targets failed safety validation."
}

$accessKey = "apfdiskfull"
$secretKey = "apf-disk-full-test-only-secret"
$helperPath = Join-Path $PSScriptRoot "minio_disk_full_probe.py"
$isolatedStarted = $false
$helperCopied = $false
$operationError = $null
$cleanupErrors = @()
$result = $null

try {
  Write-Host "Starting isolated MinIO with bounded tmpfs: ${DataMiB}MiB"
  Invoke-DockerText -DockerArguments @(
    "run", "-d",
    "--name", $isolatedContainer,
    "--network", $network,
    "--tmpfs", "/data:rw,nosuid,nodev,size=${DataMiB}m",
    "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
    "--memory", "256m",
    "--cpus", "1",
    "--pids-limit", "128",
    "--security-opt", "no-new-privileges",
    "--cap-drop", "ALL",
    "-e", "MINIO_ROOT_USER=$accessKey",
    "-e", "MINIO_ROOT_PASSWORD=$secretKey",
    "-e", "MINIO_CI_CD=1",
    $MinioImage,
    "server", "/data"
  ) | Out-Null
  $isolatedStarted = $true

  Invoke-DockerText -DockerArguments @("cp", $helperPath, "${ExecutionContainer}:$containerProbe") | Out-Null
  $helperCopied = $true
  $resultText = Invoke-DockerText -DockerArguments @(
    "exec", $ExecutionContainer,
    "python", $containerProbe,
    "--endpoint", "${isolatedContainer}:9000",
    "--access-key", $accessKey,
    "--secret-key", $secretKey,
    "--bucket", $bucket,
    "--object-mib", [string]$ObjectMiB,
    "--max-objects", [string]$MaxObjects
  )
  $result = $resultText | ConvertFrom-Json
  if (-not $result.storage_full_observed -or $result.storage_error_code -ne "XMinioStorageFull" -or -not $result.recovery_write_verified) {
    throw "Isolated probe did not prove storage-full rejection and recovery: $resultText"
  }
} catch {
  $operationError = $_
} finally {
  if ($helperCopied) {
    try {
      Invoke-DockerText -DockerArguments @("exec", "--user", "0", $ExecutionContainer, "rm", "-f", "--", $containerProbe) | Out-Null
    } catch {
      $cleanupErrors += "helper cleanup: $($_.Exception.Message)"
    }
  }
  if ($isolatedStarted) {
    try {
      $actual = Invoke-DockerText -DockerArguments @("inspect", "--format", "{{.Name}}", $isolatedContainer)
      if ($actual -ne "/$isolatedContainer") { throw "Refusing to remove an unexpected container." }
      Invoke-DockerText -DockerArguments @("rm", "-f", $isolatedContainer) | Out-Null
    } catch {
      $cleanupErrors += "isolated container cleanup: $($_.Exception.Message)"
    }
  }
}

Assert-RunningContainer -Name $ProductionMinioContainer
if ($operationError -or $cleanupErrors.Count -gt 0) {
  $parts = @()
  if ($operationError) { $parts += "operation: $($operationError.Exception.Message)" }
  if ($cleanupErrors.Count -gt 0) { $parts += ($cleanupErrors -join "; ") }
  throw "MinIO isolated disk-full smoke failed. $($parts -join ' | ')"
}

Write-Host "MINIO_DISK_FULL_SMOKE_OK"
Write-Host "tmpfs_mib=$DataMiB object_mib=$ObjectMiB successful_objects=$($result.successful_object_count) payload_bytes=$($result.successful_payload_bytes) error=$($result.storage_error_code) recovery=1 cleanup=1 production_untouched=1"
