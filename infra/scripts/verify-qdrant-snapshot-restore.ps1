param(
  [string]$OutputDirectory = "data/backups/qdrant",
  [string]$QdrantBaseUrl = "http://127.0.0.1:6333",
  [string]$ApiKey = $env:QDRANT_API_KEY
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-Qdrant {
  param(
    [Parameter(Mandatory = $true)][ValidateSet("GET", "POST", "PUT", "DELETE")][string]$Method,
    [Parameter(Mandatory = $true)][string]$Path,
    $Body = $null
  )
  $parameters = @{
    Method = $Method
    Uri = "$($QdrantBaseUrl.TrimEnd('/'))$Path"
    TimeoutSec = 30
  }
  if ($ApiKey) {
    $parameters.Headers = @{ "api-key" = $ApiKey }
  }
  if ($null -ne $Body) {
    $parameters.ContentType = "application/json"
    $parameters.Body = $Body | ConvertTo-Json -Depth 12 -Compress
  }
  return Invoke-RestMethod @parameters
}

function Invoke-QdrantSnapshotUpload {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$FilePath
  )
  Add-Type -AssemblyName System.Net.Http
  $client = New-Object System.Net.Http.HttpClient
  $multipart = New-Object System.Net.Http.MultipartFormDataContent
  $stream = $null
  $streamContent = $null
  try {
    if ($ApiKey) { $client.DefaultRequestHeaders.Add("api-key", $ApiKey) }
    $stream = [System.IO.File]::OpenRead($FilePath)
    $streamContent = New-Object System.Net.Http.StreamContent($stream)
    $streamContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue("application/octet-stream")
    $multipart.Add($streamContent, "snapshot", [System.IO.Path]::GetFileName($FilePath))
    $response = $client.PostAsync($Url, $multipart).GetAwaiter().GetResult()
    $responseText = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
      throw "Qdrant snapshot upload failed (status=$([int]$response.StatusCode))."
    }
    return $responseText
  } finally {
    if ($streamContent) { $streamContent.Dispose() }
    if ($stream) { $stream.Dispose() }
    $multipart.Dispose()
    $client.Dispose()
  }
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

$health = Invoke-Qdrant -Method GET -Path "/collections"
if ($health.status -ne "ok") {
  throw "Qdrant collections endpoint is not healthy."
}

$suffix = [guid]::NewGuid().ToString("N")
$sourceCollection = "apf-drill-source-$suffix"
$restoreCollection = "apf-drill-restore-$suffix"
$safeCollectionPattern = '^apf-drill-(source|restore)-[a-f0-9]{32}$'
if ($sourceCollection -notmatch $safeCollectionPattern -or $restoreCollection -notmatch $safeCollectionPattern) {
  throw "Generated collection names failed safety validation."
}

$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$snapshotPath = Join-Path $resolvedOutput "agentpowflow-qdrant-$timestamp-$suffix.snapshot"
$manifestPath = Join-Path $resolvedOutput "agentpowflow-qdrant-$timestamp-$suffix.manifest.json"
$sourceCreated = $false
$restoreCreated = $false
$sourceRemoved = $false
$restoreRemoved = $false
$operationError = $null
$cleanupErrors = @()
$verificationSucceeded = $false

try {
  Write-Host "Creating isolated Qdrant canary collection: $sourceCollection"
  $created = Invoke-Qdrant -Method PUT -Path "/collections/$sourceCollection" -Body @{
    vectors = @{ size = 4; distance = "Cosine" }
  }
  if ($created.status -ne "ok") { throw "Qdrant did not confirm source collection creation." }
  $sourceCreated = $true

  $upsert = Invoke-Qdrant -Method PUT -Path "/collections/$sourceCollection/points?wait=true" -Body @{
    points = @(
      @{ id = 101; vector = @(1.0, 0.0, 0.0, 0.0); payload = @{ marker = "alpha"; ordinal = 1 } },
      @{ id = 202; vector = @(0.0, 1.0, 0.0, 0.0); payload = @{ marker = "beta"; ordinal = 2 } }
    )
  }
  if ($upsert.status -ne "ok") { throw "Qdrant did not confirm canary point upsert." }

  Write-Host "Creating and downloading Qdrant snapshot..."
  $snapshot = Invoke-Qdrant -Method POST -Path "/collections/$sourceCollection/snapshots?wait=true"
  if ($snapshot.status -ne "ok" -or -not $snapshot.result.name) {
    throw "Qdrant did not return a snapshot name."
  }
  $snapshotName = [string]$snapshot.result.name
  if ($snapshotName -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "Qdrant returned an unsafe snapshot name: $snapshotName"
  }
  $downloadParameters = @{
    Uri = "$($QdrantBaseUrl.TrimEnd('/'))/collections/$sourceCollection/snapshots/$snapshotName"
    OutFile = $snapshotPath
    TimeoutSec = 60
  }
  if ($ApiKey) { $downloadParameters.Headers = @{ "api-key" = $ApiKey } }
  Invoke-WebRequest @downloadParameters
  $snapshotFile = Get-Item -LiteralPath $snapshotPath
  if ($snapshotFile.Length -lt 1) { throw "Downloaded Qdrant snapshot is empty." }
  $sha256 = (Get-FileHash -LiteralPath $snapshotPath -Algorithm SHA256).Hash.ToLowerInvariant()

  Write-Host "Uploading snapshot into isolated collection: $restoreCollection"
  $uploadUrl = "$($QdrantBaseUrl.TrimEnd('/'))/collections/$restoreCollection/snapshots/upload?priority=snapshot&wait=true"
  $uploadText = Invoke-QdrantSnapshotUpload -Url $uploadUrl -FilePath $snapshotPath
  $upload = $uploadText | ConvertFrom-Json
  if ($upload.status -ne "ok") { throw "Qdrant snapshot upload did not return ok: $uploadText" }
  $restoreCreated = $true

  $restoredInfo = Invoke-Qdrant -Method GET -Path "/collections/$restoreCollection"
  if ($restoredInfo.status -ne "ok" -or [int]$restoredInfo.result.points_count -ne 2) {
    throw "Restored Qdrant collection point count mismatch."
  }
  $scroll = Invoke-Qdrant -Method POST -Path "/collections/$restoreCollection/points/scroll" -Body @{
    limit = 10
    with_payload = $true
    with_vector = $true
  }
  $points = @($scroll.result.points | Sort-Object id)
  if ($points.Count -ne 2 -or [int]$points[0].id -ne 101 -or [string]$points[0].payload.marker -ne "alpha" -or [int]$points[1].id -ne 202 -or [string]$points[1].payload.marker -ne "beta") {
    throw "Restored Qdrant points or payload markers do not match the canary data."
  }
  $verificationSucceeded = $true
} catch {
  $operationError = $_
} finally {
  if ($restoreCreated) {
    try {
      if ($restoreCollection -notmatch $safeCollectionPattern) { throw "Unsafe restore collection name." }
      $removed = Invoke-Qdrant -Method DELETE -Path "/collections/$restoreCollection"
      if ($removed.status -ne "ok") { throw "Qdrant did not confirm restore collection deletion." }
      $restoreRemoved = $true
    } catch {
      $cleanupErrors += "restore collection cleanup: $($_.Exception.Message)"
    }
  }
  if ($sourceCreated) {
    try {
      if ($sourceCollection -notmatch $safeCollectionPattern) { throw "Unsafe source collection name." }
      $removed = Invoke-Qdrant -Method DELETE -Path "/collections/$sourceCollection"
      if ($removed.status -ne "ok") { throw "Qdrant did not confirm source collection deletion." }
      $sourceRemoved = $true
    } catch {
      $cleanupErrors += "source collection cleanup: $($_.Exception.Message)"
    }
  }
}

if ($operationError -or $cleanupErrors.Count -gt 0) {
  $failureParts = @()
  if ($operationError) { $failureParts += "operation: $($operationError.Exception.Message)" }
  if ($cleanupErrors.Count -gt 0) { $failureParts += ($cleanupErrors -join "; ") }
  throw "Qdrant snapshot restore drill failed. $($failureParts -join ' | ')"
}
if (-not $verificationSucceeded -or -not $sourceRemoved -or -not $restoreRemoved) {
  throw "Qdrant snapshot restore verification or cleanup did not complete."
}

$manifest = [ordered]@{
  format_version = 1
  created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
  qdrant_base_url = $QdrantBaseUrl
  backup_file = [System.IO.Path]::GetFileName($snapshotPath)
  backup_bytes = $snapshotFile.Length
  sha256 = $sha256
  snapshot_name = $snapshotName
  vector_size = 4
  point_count = 2
  restored_points_verified = $true
  source_collection_removed = $sourceRemoved
  restore_collection_removed = $restoreRemoved
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "QDRANT_SNAPSHOT_RESTORE_OK"
Write-Host "snapshot=$snapshotPath"
Write-Host "manifest=$manifestPath"
Write-Host "sha256=$sha256"
Write-Host "bytes=$($snapshotFile.Length) points=2 cleanup=1"
