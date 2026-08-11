param(
  [string]$ComposeFile = "infra/compose/docker-compose.stack.yml",
  [string]$EnvFile = "config/service-ports.env"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Invoke-Json {
  param(
    [ValidateSet("GET", "POST", "PUT", "DELETE")][string]$Method,
    [string]$Uri,
    [hashtable]$Headers = @{},
    [object]$Body = $null,
    [int]$TimeoutSec = 45
  )
  $script:LastRequest = "$Method $Uri"
  $params = @{ Method = $Method; Uri = $Uri; Headers = $Headers; TimeoutSec = $TimeoutSec }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = $Body | ConvertTo-Json -Depth 12 -Compress
  }
  Invoke-RestMethod @params
}

function Wait-HttpReady {
  param([string]$Uri, [int]$Attempts = 60)
  for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3 | Out-Null
      return
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "Service did not become ready: $Uri"
}

$composePath = (Resolve-Path -LiteralPath $ComposeFile).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$composeJson = docker compose --env-file $envPath -f $composePath config --format json
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Compose configuration." }
$compose = $composeJson | ConvertFrom-Json
$executionEnv = $compose.services.'execution-api'.environment
$executionUser = [string]$executionEnv.EXECUTION_ADMIN_USERNAME
$executionPassword = [string]$executionEnv.EXECUTION_ADMIN_PASSWORD
Assert-True (-not [string]::IsNullOrWhiteSpace($executionUser)) "Execution admin username is missing."
Assert-True (-not [string]::IsNullOrWhiteSpace($executionPassword)) "Execution admin password is missing."

$suffix = [guid]::NewGuid().ToString("N").Substring(0, 12)
$tenantId = "fault-smoke-$suffix"
$kbId = "kb-$suffix"
$bucket = [string]$executionEnv.MINIO_DOCUMENT_BUCKET
if ([string]::IsNullOrWhiteSpace($bucket)) { $bucket = "agent-powflow-documents" }
$objectKey = "fault-smoke/$suffix/evidence.txt"
$collectionName = "apf_fault_$suffix"
$executionBase = "http://127.0.0.1:8091"
$qdrantBase = "http://127.0.0.1:6333"
$minioStopped = $false
$qdrantStopped = $false
$succeeded = $false

try {
  $login = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/auth/login" -Body @{
    username = $executionUser
    password = $executionPassword
  }
  $headers = @{
    Authorization = "Bearer $($login.access_token)"
    "X-Tenant-Id" = $tenantId
    "X-Actor-Id" = $executionUser
    "X-Trace-Id" = "cross-store-fault-$suffix"
  }

  $script:LastRequest = "seed MinIO object $objectKey"
  docker exec agentpowflow-execution-api python -c "from runtime.object_storage import object_storage; object_storage.put_bytes('$objectKey', b'cross-store-fault-evidence', content_type='text/plain')" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to seed the MinIO fault object." }
  $minioRepairId = docker exec agentpowflow-execution-api python -c "from retrieval.store import kb_store; print(kb_store.enqueue_cross_store_repair('MINIO','DELETE_OBJECT','$tenantId','$kbId',payload={'bucket':'$bucket','key':'$objectKey'}))"
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($minioRepairId -join '').Trim())) { throw "Unable to seed the MinIO repair obligation." }

  docker compose --env-file $envPath -f $composePath stop minio | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to stop MinIO." }
  $minioStopped = $true
  $failedMinio = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/execute?limit=20" -Headers $headers -Body @{} -TimeoutSec 90
  Assert-True ([int]$failedMinio.item.failed -ge 1) "MinIO outage did not produce a failed repair attempt."
  $health = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/stats" -Headers $headers
  Assert-True ($health.item.health -eq "CRITICAL") "Failed MinIO repair did not make repair health CRITICAL."

  docker compose --env-file $envPath -f $composePath start minio | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to restart MinIO." }
  $minioStopped = $false
  Wait-HttpReady -Uri "http://127.0.0.1:9000/minio/health/ready"
  $recoveredMinio = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/execute?limit=20" -Headers $headers -Body @{} -TimeoutSec 90
  Assert-True ([int]$recoveredMinio.item.succeeded -ge 1) "MinIO repair did not recover after restart."
  docker exec agentpowflow-execution-api python -c "from runtime.object_storage import object_storage; from minio.error import S3Error; import sys;`ntry: object_storage.client.stat_object('$bucket','$objectKey'); sys.exit(2)`nexcept S3Error as exc: sys.exit(0 if exc.code in ('NoSuchKey','NoSuchObject') else 3)"
  Assert-True ($LASTEXITCODE -eq 0) "MinIO object still exists after a successful repair."

  Invoke-Json -Method PUT -Uri "$qdrantBase/collections/$collectionName" -Body @{ vectors = @{ size = 3; distance = "Cosine" } } | Out-Null
  $qdrantRepairId = docker exec agentpowflow-execution-api python -c "from retrieval.store import kb_store; print(kb_store.enqueue_cross_store_repair('QDRANT','DELETE_COLLECTION','$tenantId','$kbId',payload={'collection_name':'$collectionName'}))"
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($qdrantRepairId -join '').Trim())) { throw "Unable to seed the Qdrant repair obligation." }

  docker compose --env-file $envPath -f $composePath stop qdrant | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to stop Qdrant." }
  $qdrantStopped = $true
  $failedQdrant = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/execute?limit=20" -Headers $headers -Body @{} -TimeoutSec 90
  Assert-True ([int]$failedQdrant.item.failed -ge 1) "Qdrant outage did not produce a failed repair attempt."

  docker compose --env-file $envPath -f $composePath start qdrant | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to restart Qdrant." }
  $qdrantStopped = $false
  Wait-HttpReady -Uri "$qdrantBase/readyz"
  $recoveredQdrant = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/execute?limit=20" -Headers $headers -Body @{} -TimeoutSec 90
  Assert-True ([int]$recoveredQdrant.item.succeeded -ge 1) "Qdrant repair did not recover after restart."
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$qdrantBase/collections/$collectionName" -TimeoutSec 10 | Out-Null
    throw "Qdrant collection still exists after a successful repair."
  } catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) { } else { throw }
  }

  $finalHealth = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/system/cross-store-repairs/stats" -Headers $headers
  $scoped = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/system/cross-store-repairs?status=SUCCEEDED&limit=500" -Headers $headers
  $scopedItems = @($scoped.items | Where-Object { $_.tenant_id -eq $tenantId })
  Assert-True ($scopedItems.Count -eq 2) "Expected two successful fault-smoke repair records."
  Assert-True ([int]$finalHealth.item.open_count -eq 0) "Repair health still reports open obligations after recovery."
  $succeeded = $true
  Write-Output "CROSS_STORE_FAULT_SMOKE_OK minio_failure=1 minio_recovery=1 qdrant_failure=1 qdrant_recovery=1 health=1 cleanup=1"
} catch {
  throw "Cross-store fault smoke failed at ${script:LastRequest}: $($_.Exception.Message)"
} finally {
  if ($minioStopped) {
    try { docker compose --env-file $envPath -f $composePath start minio | Out-Null } catch {}
  }
  if ($qdrantStopped) {
    try { docker compose --env-file $envPath -f $composePath start qdrant | Out-Null } catch {}
  }
  try {
    Wait-HttpReady -Uri "http://127.0.0.1:9000/minio/health/ready" -Attempts 30
    docker exec agentpowflow-execution-api python -c "from runtime.object_storage import object_storage; object_storage.delete_object('$bucket','$objectKey')" | Out-Null
  } catch {}
  try {
    Wait-HttpReady -Uri "$qdrantBase/readyz" -Attempts 30
    Invoke-RestMethod -Method DELETE -Uri "$qdrantBase/collections/$collectionName" -TimeoutSec 10 | Out-Null
  } catch {}
  try {
    docker exec agentpowflow-postgres psql -U apf -d agentpowflow -v ON_ERROR_STOP=1 -c "DELETE FROM cross_store_repairs WHERE tenant_id='$tenantId' AND kb_id='$kbId';" | Out-Null
  } catch {
    if ($succeeded) { throw "Fault smoke ledger cleanup failed: $($_.Exception.Message)" }
  }
}
