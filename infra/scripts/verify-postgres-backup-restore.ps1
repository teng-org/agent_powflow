param(
  [string]$OutputDirectory = "data/backups/postgres",
  [string]$ContainerName = "agentpowflow-postgres",
  [string]$DatabaseName = "agentpowflow",
  [string]$DatabaseUser = "apf"
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

function Invoke-DatabaseScalar {
  param(
    [Parameter(Mandatory = $true)][string]$TargetDatabase,
    [Parameter(Mandatory = $true)][string]$Sql
  )

  return Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "psql", "-X", "-v", "ON_ERROR_STOP=1",
    "-U", $DatabaseUser,
    "-d", $TargetDatabase,
    "-At", "-c", $Sql
  )
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

Assert-SafeIdentifier -Value $ContainerName -Label "ContainerName"
Assert-SafeIdentifier -Value $DatabaseName -Label "DatabaseName"
Assert-SafeIdentifier -Value $DatabaseUser -Label "DatabaseUser"

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
  throw "PostgreSQL container is not running: $ContainerName"
}

$suffix = [guid]::NewGuid().ToString("N")
$restoreDatabase = "apf_restore_$suffix"
$containerBackup = "/tmp/apf_backup_$suffix.dump"
if ($restoreDatabase -notmatch '^apf_restore_[a-f0-9]{32}$') {
  throw "Generated restore database name failed safety validation."
}
if ($containerBackup -notmatch '^/tmp/apf_backup_[a-f0-9]{32}\.dump$') {
  throw "Generated container backup path failed safety validation."
}

$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backupPath = Join-Path $resolvedOutput "agentpowflow-$timestamp-$suffix.dump"
$manifestPath = Join-Path $resolvedOutput "agentpowflow-$timestamp-$suffix.manifest.json"
$restoreCreated = $false
$restoreDropped = $false
$containerBackupRemoved = $false
$verificationSucceeded = $false
$operationError = $null
$cleanupErrors = @()

$requiredTables = @(
  "flyway_schema_history",
  "execution_schema_migrations",
  "chat_schema_migrations",
  "runs",
  "run_approvals",
  "approval_audit_facts",
  "approval_alerts",
  "approval_alert_deliveries",
  "documents",
  "chat_sessions"
)
$requiredValues = ($requiredTables | ForEach-Object { "('$_')" }) -join ","
$tableCountSql = "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';"
$ledgerSql = @"
SELECT concat_ws('|',
  'control', (SELECT coalesce(max(version::int), 0) FROM flyway_schema_history WHERE success), (SELECT count(*) FROM flyway_schema_history WHERE success),
  'execution', (SELECT coalesce(max(version), 0) FROM execution_schema_migrations), (SELECT count(*) FROM execution_schema_migrations),
  'chat', (SELECT coalesce(max(version), 0) FROM chat_schema_migrations), (SELECT count(*) FROM chat_schema_migrations)
);
"@
$missingRequiredSql = @"
WITH required(name) AS (VALUES $requiredValues)
SELECT coalesce(string_agg(required.name, ',' ORDER BY required.name), '')
FROM required
LEFT JOIN information_schema.tables actual
  ON actual.table_schema = 'public'
 AND actual.table_type = 'BASE TABLE'
 AND actual.table_name = required.name
WHERE actual.table_name IS NULL;
"@

try {
  Write-Host "Creating PostgreSQL custom-format backup..."
  Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "pg_dump", "-U", $DatabaseUser, "-d", $DatabaseName,
    "--format=custom", "--file=$containerBackup"
  ) | Out-Null

  $archiveList = Invoke-DockerText -DockerArguments @("exec", $ContainerName, "pg_restore", "--list", $containerBackup)
  $archiveObjectCount = @($archiveList -split "`n" | Where-Object { $_ -and -not $_.StartsWith(";") }).Count
  if ($archiveObjectCount -lt 1) {
    throw "Backup archive contains no restorable objects."
  }

  Invoke-DockerText -DockerArguments @("cp", "${ContainerName}:$containerBackup", $backupPath) | Out-Null
  $backupFile = Get-Item -LiteralPath $backupPath
  if ($backupFile.Length -lt 1) {
    throw "Copied backup is empty: $backupPath"
  }
  $sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()

  $sourceTableCount = [int](Invoke-DatabaseScalar -TargetDatabase $DatabaseName -Sql $tableCountSql)
  $sourceLedger = Invoke-DatabaseScalar -TargetDatabase $DatabaseName -Sql $ledgerSql

  Write-Host "Restoring backup into isolated database: $restoreDatabase"
  Invoke-DockerText -DockerArguments @("exec", $ContainerName, "createdb", "-U", $DatabaseUser, "-O", $DatabaseUser, $restoreDatabase) | Out-Null
  $restoreCreated = $true
  Invoke-DockerText -DockerArguments @(
    "exec", $ContainerName,
    "pg_restore", "-U", $DatabaseUser, "-d", $restoreDatabase,
    "--no-owner", "--no-privileges", $containerBackup
  ) | Out-Null

  $restoredTableCount = [int](Invoke-DatabaseScalar -TargetDatabase $restoreDatabase -Sql $tableCountSql)
  $restoredLedger = Invoke-DatabaseScalar -TargetDatabase $restoreDatabase -Sql $ledgerSql
  $missingRequiredTables = Invoke-DatabaseScalar -TargetDatabase $restoreDatabase -Sql $missingRequiredSql
  $unvalidatedForeignKeys = [int](Invoke-DatabaseScalar -TargetDatabase $restoreDatabase -Sql "SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated;")

  if ($restoredTableCount -ne $sourceTableCount) {
    throw "Restored table count mismatch: source=$sourceTableCount restored=$restoredTableCount"
  }
  if ($restoredLedger -ne $sourceLedger) {
    throw "Migration ledger mismatch: source=$sourceLedger restored=$restoredLedger"
  }
  if ($missingRequiredTables) {
    throw "Restored database is missing required tables: $missingRequiredTables"
  }
  if ($unvalidatedForeignKeys -ne 0) {
    throw "Restored database has unvalidated foreign keys: $unvalidatedForeignKeys"
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
      Invoke-DockerText -DockerArguments @("exec", $ContainerName, "dropdb", "-U", $DatabaseUser, "--if-exists", $restoreDatabase) | Out-Null
      $restoreDropped = $true
    } catch {
      $cleanupErrors += "restore database cleanup: $($_.Exception.Message)"
    }
  }
  try {
    if ($containerBackup -notmatch '^/tmp/apf_backup_[a-f0-9]{32}\.dump$') {
      throw "Refusing to remove an unsafe container path: $containerBackup"
    }
    Invoke-DockerText -DockerArguments @("exec", $ContainerName, "rm", "-f", "--", $containerBackup) | Out-Null
    $containerBackupRemoved = $true
  } catch {
    $cleanupErrors += "container temporary backup cleanup: $($_.Exception.Message)"
  }
}

if ($operationError -or $cleanupErrors.Count -gt 0) {
  $failureParts = @()
  if ($operationError) {
    $failureParts += "operation: $($operationError.Exception.Message)"
  }
  if ($cleanupErrors.Count -gt 0) {
    $failureParts += ($cleanupErrors -join "; ")
  }
  throw "PostgreSQL backup/restore drill failed. $($failureParts -join ' | ')"
}

if (-not $verificationSucceeded -or -not $restoreDropped -or -not $containerBackupRemoved) {
  throw "Backup restore verification or cleanup did not complete."
}

$manifest = [ordered]@{
  format_version = 1
  created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
  database = $DatabaseName
  container = $ContainerName
  backup_file = [System.IO.Path]::GetFileName($backupPath)
  backup_bytes = $backupFile.Length
  sha256 = $sha256
  archive_object_count = $archiveObjectCount
  public_table_count = $restoredTableCount
  migration_ledger = $restoredLedger
  required_tables = $requiredTables
  unvalidated_foreign_keys = $unvalidatedForeignKeys
  isolated_restore_verified = $true
  restore_database_removed = $restoreDropped
  container_temporary_backup_removed = $containerBackupRemoved
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "POSTGRES_BACKUP_RESTORE_OK"
Write-Host "backup=$backupPath"
Write-Host "manifest=$manifestPath"
Write-Host "sha256=$sha256"
Write-Host "bytes=$($backupFile.Length) tables=$restoredTableCount archive_objects=$archiveObjectCount ledger=$restoredLedger cleanup=1"
