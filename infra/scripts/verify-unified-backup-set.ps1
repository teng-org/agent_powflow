param(
  [string]$OutputDirectory = "data/backups/sets",
  [string]$MongoDatabasePassword = $env:MONGO_INITDB_ROOT_PASSWORD,
  [string]$QdrantApiKey = $env:QDRANT_API_KEY
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

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

$suffix = [guid]::NewGuid().ToString("N")
$startedAt = [DateTimeOffset]::UtcNow
$setName = "agentpowflow-backup-set-$($startedAt.ToString('yyyyMMddTHHmmssZ'))-$suffix"
if ($setName -notmatch '^agentpowflow-backup-set-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{32}$') {
  throw "Generated backup set name failed safety validation."
}
$setDirectory = Join-Path $resolvedOutput $setName
New-Item -ItemType Directory -Path $setDirectory | Out-Null
$setManifestPath = Join-Path $setDirectory "backup-set.manifest.json"
$components = @()

function Invoke-BackupComponent {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$ScriptName,
    [Parameter(Mandatory = $true)][hashtable]$Arguments
  )
  if ($Name -notmatch '^(postgres|mongodb|qdrant|minio)$') { throw "Unsupported backup component: $Name" }
  $scriptPath = Join-Path $PSScriptRoot $ScriptName
  if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Backup component script is missing: $ScriptName" }
  $componentDirectory = Join-Path $setDirectory $Name
  New-Item -ItemType Directory -Path $componentDirectory | Out-Null
  $Arguments.OutputDirectory = $componentDirectory
  $componentStartedAt = [DateTimeOffset]::UtcNow
  Write-Host "BACKUP_SET_COMPONENT_START name=$Name"
  & $scriptPath @Arguments
  $componentFinishedAt = [DateTimeOffset]::UtcNow

  $manifests = @(Get-ChildItem -LiteralPath $componentDirectory -File -Filter "*.manifest.json")
  if ($manifests.Count -ne 1) { throw "Expected exactly one component manifest for $Name, found $($manifests.Count)." }
  $componentManifest = Get-Content -LiteralPath $manifests[0].FullName -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([int]$componentManifest.format_version -ne 1) { throw "Unsupported $Name manifest format." }
  $backupFileName = [string]$componentManifest.backup_file
  if (-not $backupFileName -or [System.IO.Path]::GetFileName($backupFileName) -ne $backupFileName) {
    throw "Unsafe backup_file in $Name manifest."
  }
  $backupPath = Join-Path $componentDirectory $backupFileName
  if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) { throw "Backup artifact referenced by $Name manifest is missing." }
  $backupFile = Get-Item -LiteralPath $backupPath
  if ($backupFile.Length -ne [long]$componentManifest.backup_bytes) { throw "$Name artifact size does not match its manifest." }
  $actualHash = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne ([string]$componentManifest.sha256).ToLowerInvariant()) { throw "$Name artifact checksum does not match its manifest." }

  if ($Name -eq "postgres" -and (
      -not $componentManifest.isolated_restore_verified -or
      -not $componentManifest.restore_database_removed -or
      -not $componentManifest.container_temporary_backup_removed)) {
    throw "PostgreSQL manifest does not prove restore and cleanup completion."
  }
  if ($Name -eq "mongodb" -and (
      -not $componentManifest.isolated_restore_verified -or
      -not $componentManifest.restore_database_removed -or
      -not $componentManifest.container_temporary_archive_removed)) {
    throw "MongoDB manifest does not prove restore and cleanup completion."
  }
  if ($Name -eq "qdrant" -and (
      -not $componentManifest.restored_points_verified -or
      -not $componentManifest.source_collection_removed -or
      -not $componentManifest.restore_collection_removed)) {
    throw "Qdrant manifest does not prove restore and cleanup completion."
  }
  if ($Name -eq "minio" -and (
      -not $componentManifest.restored_objects_verified -or
      -not $componentManifest.bucket_versioning_verified -or
      -not $componentManifest.canary_prefixes_removed -or
      -not $componentManifest.container_temporary_files_removed -or
      [int]$componentManifest.version_count -lt 2)) {
    throw "MinIO manifest does not prove versioned restore and cleanup completion."
  }

  Write-Host "BACKUP_SET_COMPONENT_OK name=$Name"
  return [ordered]@{
    name = $Name
    started_at_utc = $componentStartedAt.ToString("o")
    finished_at_utc = $componentFinishedAt.ToString("o")
    duration_seconds = [Math]::Round(($componentFinishedAt - $componentStartedAt).TotalSeconds, 3)
    manifest_file = "$Name/$($manifests[0].Name)"
    backup_file = "$Name/$backupFileName"
    backup_bytes = $backupFile.Length
    sha256 = $actualHash
    isolated_restore_verified = $true
  }
}

try {
  $components += Invoke-BackupComponent -Name "postgres" -ScriptName "verify-postgres-backup-restore.ps1" -Arguments @{}
  $mongoArguments = @{}
  if ($MongoDatabasePassword) { $mongoArguments.DatabasePassword = $MongoDatabasePassword }
  $components += Invoke-BackupComponent -Name "mongodb" -ScriptName "verify-mongodb-backup-restore.ps1" -Arguments $mongoArguments
  $qdrantArguments = @{}
  if ($QdrantApiKey) { $qdrantArguments.ApiKey = $QdrantApiKey }
  $components += Invoke-BackupComponent -Name "qdrant" -ScriptName "verify-qdrant-snapshot-restore.ps1" -Arguments $qdrantArguments
  $components += Invoke-BackupComponent -Name "minio" -ScriptName "verify-minio-backup-restore.ps1" -Arguments @{}
} catch {
  [ordered]@{
    format_version = 1
    set_id = $setName
    status = "FAILED"
    started_at_utc = $startedAt.ToString("o")
    failed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    completed_components = $components
    atomic_cross_store_snapshot = $false
    consistency_model = "online_fuzzy_window"
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $setManifestPath -Encoding UTF8
  throw
}

$finishedAt = [DateTimeOffset]::UtcNow
$setManifest = [ordered]@{
  format_version = 1
  set_id = $setName
  status = "VERIFIED"
  started_at_utc = $startedAt.ToString("o")
  finished_at_utc = $finishedAt.ToString("o")
  backup_window_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
  consistency_model = "online_fuzzy_window"
  atomic_cross_store_snapshot = $false
  restore_requires_write_quiescence = $true
  restore_requires_cross_store_reconciliation = $true
  component_count = $components.Count
  components = $components
}
$setManifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $setManifestPath -Encoding UTF8

Write-Host "UNIFIED_BACKUP_SET_OK"
Write-Host "set=$setDirectory"
Write-Host "manifest=$setManifestPath"
Write-Host "components=$($components.Count) window_seconds=$($setManifest.backup_window_seconds) atomic=0"
