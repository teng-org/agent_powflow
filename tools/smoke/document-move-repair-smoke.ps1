param(
  [string]$ControlBase = "http://127.0.0.1:8099",
  [string]$ExecutionBase = "http://127.0.0.1:8091",
  [string]$QdrantBase = "http://127.0.0.1:6333",
  [string]$TenantId = "platform",
  [string]$Username = "admin",
  [string]$Password = "admin123",
  [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"

function Invoke-Json {
  param([string]$Method, [string]$Uri, [hashtable]$Headers = @{}, [object]$Body = $null)
  $parameters = @{ Method = $Method; Uri = $Uri; Headers = $Headers; UseBasicParsing = $true; TimeoutSec = 30 }
  if ($null -ne $Body) {
    $parameters.ContentType = "application/json"
    $parameters.Body = ($Body | ConvertTo-Json -Depth 12 -Compress)
  }
  Invoke-RestMethod @parameters
}

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Wait-ControlTask {
  param([string]$TaskId, [string]$ExpectedStatus, [hashtable]$Headers)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $task = (Invoke-Json GET "$ControlBase/api/v1/tasks/$TaskId" $Headers).record
    if ($null -ne $task -and [string]$task.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) {
      Assert-True ([string]$task.status -eq $ExpectedStatus) "Task $TaskId ended as $($task.status), expected $ExpectedStatus"
      Assert-True ([string]$task.state_owner -eq "CONTROL") "Task $TaskId is not Control-owned"
      return $task
    }
    Start-Sleep -Milliseconds 400
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Task $TaskId did not finish within $TimeoutSeconds seconds"
}

function Wait-Qdrant {
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
  do {
    try {
      $health = Invoke-Json GET "$QdrantBase/healthz"
      if ([string]$health -match "healthz check passed") { return }
    } catch {}
    Start-Sleep -Milliseconds 500
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Qdrant did not recover within 60 seconds"
}

function Get-DocumentPointCount {
  param([string]$CollectionName, [string]$KbId, [string]$DocId)
  $collections = (Invoke-Json GET "$QdrantBase/collections").result.collections
  if (@($collections | Where-Object { [string]$_.name -eq $CollectionName }).Count -eq 0) { return 0 }
  $result = Invoke-Json POST "$QdrantBase/collections/$CollectionName/points/count" -Body @{
    exact = $true
    filter = @{ must = @(
      @{ key = "tenant_id"; match = @{ value = $TenantId } }
      @{ key = "kb_id"; match = @{ value = $KbId } }
      @{ key = "doc_id"; match = @{ value = $DocId } }
    ) }
  }
  return [int]$result.result.count
}

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$sourceKbId = ""
$targetKbId = ""
$docId = ""
$ingestTaskId = ""
$failedMoveTaskId = ""
$retryMoveTaskId = ""
$repairId = 0
$controlHeaders = $null
$executionHeaders = $null
$objectRefs = [System.Collections.Generic.List[string]]::new()
$qdrantStopped = $false
$succeeded = $false

try {
  $controlLogin = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId; username = $Username; password = $Password
  }
  $controlHeaders = @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Trace-Id" = "document-move-repair-smoke-$suffix"
  }
  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username
    "X-Trace-Id" = "document-move-repair-smoke-$suffix"
    "X-Source-Service" = "document-move-repair-smoke"
  }

  $sourceKbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "move-repair-source-$suffix" }).item.kb_id
  $targetKbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "move-repair-target-$suffix" }).item.kb_id
  Assert-True ($sourceKbId -match '^[0-9a-f-]{36}$' -and $targetKbId -match '^[0-9a-f-]{36}$') "Smoke KB ids are invalid"

  $ingest = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/ingest" $controlHeaders @{
    filename = "move-repair-$suffix.txt"
    content = "Agent Powflow move repair $suffix proves durable Qdrant reconciliation and fail-closed retrieval after a real outage."
    chunk_method = "sentence"; chunk_size = 120; chunk_overlap = 0; auto_summary = $false
  }
  $docId = [string]$ingest.doc_id
  $ingestTaskId = [string]$ingest.task_id
  Wait-ControlTask $ingestTaskId "SUCCEEDED" $controlHeaders | Out-Null

  Assert-True ($TenantId -match '^[A-Za-z0-9_-]+$') "Unsafe tenant id"
  $refsSql = "SELECT source_object_bucket||'|'||source_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$sourceKbId' AND doc_id='$docId' AND source_object_key<>'' UNION SELECT source_object_bucket||'|'||extracted_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$sourceKbId' AND doc_id='$docId' AND extracted_object_key<>''"
  foreach ($line in @(& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $refsSql)) {
    if (-not [string]::IsNullOrWhiteSpace($line)) { $objectRefs.Add($line.Trim()) }
  }
  Assert-True ($LASTEXITCODE -eq 0 -and $objectRefs.Count -eq 2) "Expected two exact MinIO references"

  & docker stop agentpowflow-qdrant | Out-Null
  Assert-True ($LASTEXITCODE -eq 0) "Unable to stop Qdrant for fault injection"
  $qdrantStopped = $true

  $failedMove = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/move" $controlHeaders @{
    target_kb_id = $targetKbId
  }
  $failedMoveTaskId = [string]$failedMove.task_id
  $failedTask = Wait-ControlTask $failedMoveTaskId "FAILED" $controlHeaders
  Assert-True ([string]$failedTask.error_message -match "document move repair pending") "Failed move did not expose its durable repair identity"

  & docker start agentpowflow-qdrant | Out-Null
  Assert-True ($LASTEXITCODE -eq 0) "Unable to restart Qdrant"
  $qdrantStopped = $false
  Wait-Qdrant

  $sourceDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$sourceKbId/documents" $executionHeaders
  $targetDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  Assert-True (@($sourceDocs.items | Where-Object { [string]$_.doc_id -eq $docId }).Count -eq 0) "Logically moved document remains in source"
  $movingDoc = @($targetDocs.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ([string]$movingDoc.status -eq "MOVING") "Target document is not protected by the MOVING publication barrier"
  Assert-True ([string]$movingDoc.source_object_key -eq ($objectRefs[0].Split('|', 2)[1]) -or [string]$movingDoc.source_object_key -eq ($objectRefs[1].Split('|', 2)[1])) "Move changed the source object reference"

  $repairs = Invoke-Json GET "$ExecutionBase/api/v1/admin/system/cross-store-repairs?status=FAILED&limit=200" $executionHeaders
  $repair = @($repairs.items | Where-Object {
    [string]$_.operation -eq "MOVE_DOCUMENT_POINTS" -and [string]$_.doc_id -eq $docId -and
    [string]$_.payload_json.source_kb_id -eq $sourceKbId -and [string]$_.payload_json.target_kb_id -eq $targetKbId
  }) | Select-Object -First 1
  $repairId = [int]$repair.repair_id
  Assert-True ($repairId -gt 0) "Move failure did not persist a scoped repair obligation"

  $sourceCollection = "apf_$($TenantId)_$($sourceKbId)".ToLowerInvariant().Replace('-', '_')
  $targetCollection = "apf_$($TenantId)_$($targetKbId)".ToLowerInvariant().Replace('-', '_')
  Assert-True ((Get-DocumentPointCount $targetCollection $targetKbId $docId) -eq 0) "Target points became visible before repair"

  $repairResult = Invoke-Json POST "$ExecutionBase/api/v1/admin/system/cross-store-repairs/$repairId/execute" $executionHeaders @{}
  Assert-True ([bool]$repairResult.item.succeeded -and [string]$repairResult.item.record.status -eq "SUCCEEDED") "Single move repair did not succeed"
  Assert-True ((Get-DocumentPointCount $sourceCollection $sourceKbId $docId) -eq 0) "Source Qdrant points remain after repair"
  $targetPointCount = Get-DocumentPointCount $targetCollection $targetKbId $docId
  Assert-True ($targetPointCount -gt 0) "Target Qdrant points are missing after repair"

  $repairedDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  $repairedDoc = @($repairedDocs.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ([string]$repairedDoc.status -eq "INDEXED") "Repair did not restore the original publication status"

  $retryMove = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/move" $controlHeaders @{
    target_kb_id = $targetKbId
  }
  $retryMoveTaskId = [string]$retryMove.task_id
  Wait-ControlTask $retryMoveTaskId "SUCCEEDED" $controlHeaders | Out-Null

  Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$sourceKbId" $controlHeaders | Out-Null
  Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$targetKbId" $controlHeaders | Out-Null
  & docker exec agentpowflow-postgres psql -U apf -d agentpowflow -v ON_ERROR_STOP=1 -c "DELETE FROM cross_store_repairs WHERE repair_id=$repairId AND tenant_id='$TenantId' AND doc_id='$docId';" | Out-Null
  Assert-True ($LASTEXITCODE -eq 0) "Unable to remove the exact smoke repair audit row"

  $taskIds = @($ingestTaskId, $failedMoveTaskId, $retryMoveTaskId) | Where-Object { $_ -match '^[0-9a-f-]{36}$' }
  $taskIdsSql = ($taskIds | ForEach-Object { "'$_'" }) -join ','
  $dbSql = "SELECT COALESCE(SUM(c),0) FROM (" +
    "SELECT count(*) c FROM knowledge_bases WHERE tenant_id='$TenantId' AND kb_id IN ('$sourceKbId','$targetKbId') UNION ALL " +
    "SELECT count(*) FROM documents WHERE tenant_id='$TenantId' AND kb_id IN ('$sourceKbId','$targetKbId') UNION ALL " +
    "SELECT count(*) FROM chunks WHERE tenant_id='$TenantId' AND kb_id IN ('$sourceKbId','$targetKbId') UNION ALL " +
    "SELECT count(*) FROM tasks WHERE tenant_id='$TenantId' AND kb_id IN ('$sourceKbId','$targetKbId') UNION ALL " +
    "SELECT count(*) FROM task_events WHERE tenant_id='$TenantId' AND kb_id IN ('$sourceKbId','$targetKbId') UNION ALL " +
    "SELECT count(*) FROM task_dispatch_outbox WHERE task_id IN ($taskIdsSql) UNION ALL " +
    "SELECT count(*) FROM cross_store_repairs WHERE repair_id=$repairId) x"
  $dbRemaining = (& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $dbSql).Trim()
  Assert-True ($LASTEXITCODE -eq 0 -and [int]$dbRemaining -eq 0) "PostgreSQL cleanup left $dbRemaining rows"

  $collections = (Invoke-Json GET "$QdrantBase/collections").result.collections
  Assert-True (@($collections | Where-Object { [string]$_.name -in @($sourceCollection, $targetCollection) }).Count -eq 0) "Smoke Qdrant collection remains"
  & docker exec agentpowflow-minio sh -c 'mc alias set root https://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" --insecure >/dev/null'
  Assert-True ($LASTEXITCODE -eq 0) "Unable to configure MinIO verifier"
  foreach ($reference in $objectRefs) {
    $parts = $reference.Split('|', 2)
    $remaining = @(& docker exec agentpowflow-minio mc ls --insecure --versions "root/$($parts[0])/$($parts[1])")
    Assert-True ($LASTEXITCODE -eq 0 -and $remaining.Count -eq 0) "MinIO versions remain for $($parts[1])"
  }

  $succeeded = $true
  [pscustomobject]@{
    status = "ok"; injected_outage = "qdrant"; failed_move_task = $true
    moving_barrier = $true; durable_repair = $repairId; single_repair_api = $true
    retry_move_succeeded = $true; target_points = $targetPointCount
    object_references_preserved = $true; postgres_zero_residual = $true
    qdrant_zero_residual = $true; minio_all_versions_zero = $true
  } | ConvertTo-Json -Compress
}
finally {
  if ($qdrantStopped) {
    try { & docker start agentpowflow-qdrant | Out-Null; Wait-Qdrant } catch {}
  }
  if (-not $succeeded -and $null -ne $controlHeaders) {
    foreach ($kbId in @($sourceKbId, $targetKbId)) {
      if ($kbId -match '^[0-9a-f-]{36}$') { try { Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $controlHeaders | Out-Null } catch {} }
    }
  }
  if (-not $succeeded -and $repairId -gt 0 -and $docId -match '^[0-9a-f-]{36}$') {
    try { & docker exec agentpowflow-postgres psql -U apf -d agentpowflow -v ON_ERROR_STOP=1 -c "DELETE FROM cross_store_repairs WHERE repair_id=$repairId AND tenant_id='$TenantId' AND doc_id='$docId';" | Out-Null } catch {}
  }
}
