param(
  [string]$OutputDirectory = "data/backups/minio",
  [string]$ExecutionContainer = "agentpowflow-execution-api"
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

if ($ExecutionContainer -notmatch '^[A-Za-z_][A-Za-z0-9_-]{0,62}$') {
  throw "ExecutionContainer contains unsupported characters: $ExecutionContainer"
}
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$candidateOutput = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
  [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $OutputDirectory))
}
$workspacePrefix = $workspaceRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $candidateOutput.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "OutputDirectory must stay inside workspace: $workspaceRoot"
}
New-Item -ItemType Directory -Path $candidateOutput -Force | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $candidateOutput).Path
if (-not $resolvedOutput.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Resolved OutputDirectory escaped workspace: $resolvedOutput"
}

$running = Invoke-DockerText -DockerArguments @("inspect", "-f", "{{.State.Running}}", $ExecutionContainer)
if ($running -ne "true") { throw "Execution container is not running: $ExecutionContainer" }

$suffix = [guid]::NewGuid().ToString("N")
$containerScript = "/tmp/apf_minio_drill_$suffix.py"
$containerArchive = "/tmp/apf_minio_backup_$suffix.tar.gz"
if ($containerScript -notmatch '^/tmp/apf_minio_drill_[a-f0-9]{32}\.py$' -or $containerArchive -notmatch '^/tmp/apf_minio_backup_[a-f0-9]{32}\.tar\.gz$') {
  throw "Generated container paths failed safety validation."
}
$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backupPath = Join-Path $resolvedOutput "agentpowflow-minio-$timestamp-$suffix.tar.gz"
$manifestPath = Join-Path $resolvedOutput "agentpowflow-minio-$timestamp-$suffix.manifest.json"
$helperPath = Join-Path $PSScriptRoot "minio_canary_backup_restore.py"
$operationError = $null
$cleanupErrors = @()
$verificationSucceeded = $false
$containerFilesRemoved = $false

try {
  Invoke-DockerText -DockerArguments @("cp", $helperPath, "${ExecutionContainer}:$containerScript") | Out-Null
  Write-Host "Running isolated MinIO canary backup and restore..."
  $resultText = Invoke-DockerText -DockerArguments @(
    "exec", $ExecutionContainer, "python", $containerScript,
    "--suffix", $suffix, "--archive", $containerArchive
  )
  $result = $resultText | ConvertFrom-Json
  if (-not $result.restored_objects_verified -or -not $result.bucket_versioning_verified -or [int]$result.object_count -ne 3 -or [int]$result.version_count -ne 5) {
    throw "MinIO helper did not confirm all restored object versions: $resultText"
  }
  Invoke-DockerText -DockerArguments @("cp", "${ExecutionContainer}:$containerArchive", $backupPath) | Out-Null
  $backupFile = Get-Item -LiteralPath $backupPath
  if ($backupFile.Length -lt 1) { throw "Copied MinIO archive is empty." }
  $sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $verificationSucceeded = $true
} catch {
  $operationError = $_
} finally {
  try {
    # Idempotent fallback for host interruption after a bucket was created.
    Invoke-DockerText -DockerArguments @(
      "exec", $ExecutionContainer, "python", $containerScript,
      "--suffix", $suffix, "--cleanup-only"
    ) | Out-Null
  } catch {
    $cleanupErrors += "MinIO canary bucket cleanup: $($_.Exception.Message)"
  }
  try {
    if ($containerScript -notmatch '^/tmp/apf_minio_drill_[a-f0-9]{32}\.py$' -or $containerArchive -notmatch '^/tmp/apf_minio_backup_[a-f0-9]{32}\.tar\.gz$') {
      throw "Refusing to remove unsafe container paths."
    }
    # docker cp creates the helper as root while the service runs as UID 10001.
    # Cleanup uses root only for the two exact, regex-validated /tmp paths.
    Invoke-DockerText -DockerArguments @("exec", "--user", "0", $ExecutionContainer, "rm", "-f", "--", $containerScript, $containerArchive) | Out-Null
    $containerFilesRemoved = $true
  } catch {
    $cleanupErrors += "container temporary file cleanup: $($_.Exception.Message)"
  }
}

if ($operationError -or $cleanupErrors.Count -gt 0) {
  $failureParts = @()
  if ($operationError) { $failureParts += "operation: $($operationError.Exception.Message)" }
  if ($cleanupErrors.Count -gt 0) { $failureParts += ($cleanupErrors -join "; ") }
  throw "MinIO backup/restore drill failed. $($failureParts -join ' | ')"
}
if (-not $verificationSucceeded -or -not $containerFilesRemoved) {
  throw "MinIO backup restore verification or cleanup did not complete."
}

$manifest = [ordered]@{
  format_version = 1
  created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
  backup_file = [System.IO.Path]::GetFileName($backupPath)
  backup_bytes = $backupFile.Length
  sha256 = $sha256
  object_count = [int]$result.object_count
  version_count = [int]$result.version_count
  payload_bytes = [int]$result.payload_bytes
  document_bucket = [string]$result.document_bucket
  objects = $result.objects
  bucket_versioning_verified = $true
  restored_objects_verified = $true
  canary_prefixes_removed = $true
  container_temporary_files_removed = $containerFilesRemoved
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "MINIO_BACKUP_RESTORE_OK"
Write-Host "backup=$backupPath"
Write-Host "manifest=$manifestPath"
Write-Host "sha256=$sha256"
Write-Host "bytes=$($backupFile.Length) objects=$($result.object_count) versions=$($result.version_count) payload_bytes=$($result.payload_bytes) cleanup=1"
