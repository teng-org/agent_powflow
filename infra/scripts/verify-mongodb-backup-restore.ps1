param(
  [string]$OutputDirectory = "data/backups/mongodb",
  [string]$ContainerName = "agentpowflow-mongodb",
  [string]$DatabaseName = "agentpowflow_execution",
  [string]$DatabaseUser = "root",
  [string]$DatabasePassword = "root",
  [string]$AuthenticationDatabase = "admin"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-DockerText {
  param([Parameter(Mandatory = $true)][string[]]$DockerArguments)
  $previousErrorAction = $ErrorActionPreference
  try {
    # Windows PowerShell 5 wraps native stderr lines as ErrorRecord objects.
    # Capture them for diagnostics and decide success from the native exit code.
    $ErrorActionPreference = "Continue"
    $raw = & docker @DockerArguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  $output = (($raw | ForEach-Object { [string]$_ }) -join "`n").Trim()
  if ($exitCode -ne 0) {
    $summary = if ($output) { $output } else { "no command output" }
    throw "Docker command failed (exit=$exitCode): $summary"
  }
  return $output
}

function Assert-SafeIdentifier {
  param(
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )
  if ($Value -notmatch '^[A-Za-z_][A-Za-z0-9_-]{0,62}$') {
    throw "$Label contains unsupported characters: $Value"
  }
}

function Get-CollectionInventory {
  param([Parameter(Mandatory = $true)][string]$TargetDatabase)
  $javascript = "JSON.stringify(db.getCollectionNames().sort().map(function(n){return {name:n,count:db.getCollection(n).countDocuments({})};}))"
  $json = Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "mongosh", "--quiet",
    "--username", $DatabaseUser,
    "--password", $DatabasePassword,
    "--authenticationDatabase", $AuthenticationDatabase,
    $TargetDatabase,
    "--eval", $javascript
  )
  if (-not $json) { return @() }
  return @($json | ConvertFrom-Json)
}

Assert-SafeIdentifier -Value $ContainerName -Label "ContainerName"
Assert-SafeIdentifier -Value $DatabaseName -Label "DatabaseName"
Assert-SafeIdentifier -Value $DatabaseUser -Label "DatabaseUser"
Assert-SafeIdentifier -Value $AuthenticationDatabase -Label "AuthenticationDatabase"

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

$running = Invoke-DockerText -DockerArguments @("inspect", "-f", "{{.State.Running}}", $ContainerName)
if ($running -ne "true") {
  throw "MongoDB container is not running: $ContainerName"
}

$suffix = [guid]::NewGuid().ToString("N")
$restoreDatabase = "apf_restore_$suffix"
$containerBackup = "/tmp/apf_mongo_backup_$suffix.archive.gz"
if ($restoreDatabase -notmatch '^apf_restore_[a-f0-9]{32}$') {
  throw "Generated restore database name failed safety validation."
}
if ($containerBackup -notmatch '^/tmp/apf_mongo_backup_[a-f0-9]{32}\.archive\.gz$') {
  throw "Generated container backup path failed safety validation."
}

$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backupPath = Join-Path $resolvedOutput "agentpowflow-mongodb-$timestamp-$suffix.archive.gz"
$manifestPath = Join-Path $resolvedOutput "agentpowflow-mongodb-$timestamp-$suffix.manifest.json"
$operationError = $null
$cleanupErrors = @()
$restoreCreated = $false
$restoreDropped = $false
$containerBackupRemoved = $false
$verificationSucceeded = $false

try {
  $sourceInventory = Get-CollectionInventory -TargetDatabase $DatabaseName
  if ($sourceInventory.Count -lt 1) {
    throw "Source database has no collections: $DatabaseName"
  }

  Write-Host "Creating MongoDB gzip archive..."
  Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "mongodump",
    "--username", $DatabaseUser,
    "--password", $DatabasePassword,
    "--authenticationDatabase", $AuthenticationDatabase,
    "--db", $DatabaseName,
    "--gzip", "--archive=$containerBackup"
  ) | Out-Null

  Invoke-DockerText -DockerArguments @("cp", "${ContainerName}:$containerBackup", $backupPath) | Out-Null
  $backupFile = Get-Item -LiteralPath $backupPath
  if ($backupFile.Length -lt 1) {
    throw "Copied MongoDB archive is empty: $backupPath"
  }
  $sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()

  Write-Host "Restoring archive into isolated database: $restoreDatabase"
  Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "mongorestore",
    "--username", $DatabaseUser,
    "--password", $DatabasePassword,
    "--authenticationDatabase", $AuthenticationDatabase,
    "--gzip", "--archive=$containerBackup",
    "--nsFrom=$DatabaseName.*", "--nsTo=$restoreDatabase.*"
  ) | Out-Null
  $restoreCreated = $true

  $restoredInventory = Get-CollectionInventory -TargetDatabase $restoreDatabase
  $sourceCanonical = $sourceInventory | ConvertTo-Json -Compress
  $restoredCanonical = $restoredInventory | ConvertTo-Json -Compress
  if ($sourceCanonical -ne $restoredCanonical) {
    throw "MongoDB collection inventory mismatch. source=$sourceCanonical restored=$restoredCanonical"
  }

  $sourceDocumentCount = [long](($sourceInventory | Measure-Object -Property count -Sum).Sum)
  $restoredDocumentCount = [long](($restoredInventory | Measure-Object -Property count -Sum).Sum)
  if ($restoredDocumentCount -ne $sourceDocumentCount) {
    throw "MongoDB document count mismatch: source=$sourceDocumentCount restored=$restoredDocumentCount"
  }
  $verificationSucceeded = $true
} catch {
  $operationError = $_
} finally {
  if ($restoreCreated) {
    try {
      if ($restoreDatabase -notmatch '^apf_restore_[a-f0-9]{32}$') {
        throw "Refusing to drop an unsafe restore database name: $restoreDatabase"
      }
      $dropResult = Invoke-DockerText -DockerArguments @(
        "exec", $ContainerName,
        "mongosh", "--quiet",
        "--username", $DatabaseUser,
        "--password", $DatabasePassword,
        "--authenticationDatabase", $AuthenticationDatabase,
        $restoreDatabase,
        "--eval", "JSON.stringify(db.dropDatabase())"
      )
      if ($dropResult -notmatch '"ok"\s*:\s*1') {
        throw "MongoDB did not confirm isolated database removal: $dropResult"
      }
      $restoreDropped = $true
    } catch {
      $cleanupErrors += "restore database cleanup: $($_.Exception.Message)"
    }
  }
  try {
    if ($containerBackup -notmatch '^/tmp/apf_mongo_backup_[a-f0-9]{32}\.archive\.gz$') {
      throw "Refusing to remove an unsafe container path: $containerBackup"
    }
    Invoke-DockerText -DockerArguments @("exec", $ContainerName, "rm", "-f", "--", $containerBackup) | Out-Null
    $containerBackupRemoved = $true
  } catch {
    $cleanupErrors += "container temporary archive cleanup: $($_.Exception.Message)"
  }
}

if ($operationError -or $cleanupErrors.Count -gt 0) {
  $failureParts = @()
  if ($operationError) { $failureParts += "operation: $($operationError.Exception.Message)" }
  if ($cleanupErrors.Count -gt 0) { $failureParts += ($cleanupErrors -join "; ") }
  throw "MongoDB backup/restore drill failed. $($failureParts -join ' | ')"
}
if (-not $verificationSucceeded -or -not $restoreDropped -or -not $containerBackupRemoved) {
  throw "MongoDB backup restore verification or cleanup did not complete."
}

$manifest = [ordered]@{
  format_version = 1
  created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
  database = $DatabaseName
  container = $ContainerName
  backup_file = [System.IO.Path]::GetFileName($backupPath)
  backup_bytes = $backupFile.Length
  sha256 = $sha256
  collection_count = $restoredInventory.Count
  document_count = $restoredDocumentCount
  collections = $restoredInventory
  isolated_restore_verified = $true
  restore_database_removed = $restoreDropped
  container_temporary_archive_removed = $containerBackupRemoved
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "MONGODB_BACKUP_RESTORE_OK"
Write-Host "backup=$backupPath"
Write-Host "manifest=$manifestPath"
Write-Host "sha256=$sha256"
Write-Host "bytes=$($backupFile.Length) collections=$($restoredInventory.Count) documents=$restoredDocumentCount cleanup=1"
