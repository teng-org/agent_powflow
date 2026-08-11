param(
  [string]$ControlBase = "http://127.0.0.1:8099",
  [string]$ExecutionBase = "http://127.0.0.1:8091",
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
  param([string]$TaskId, [hashtable]$Headers)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $task = (Invoke-Json GET "$ControlBase/api/v1/tasks/$TaskId" $Headers).record
    if ($null -ne $task -and [string]$task.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) {
      Assert-True ([string]$task.status -eq "SUCCEEDED") "Task $TaskId failed: status=$($task.status), error=$($task.error_message)"
      Assert-True ([string]$task.state_owner -eq "CONTROL") "Task $TaskId is not Control-owned"
      return $task
    }
    Start-Sleep -Milliseconds 400
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Task $TaskId did not finish within $TimeoutSeconds seconds"
}

function Assert-ControlCallback {
  param([string]$TaskId, [hashtable]$Headers)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
  do {
    $events = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/$TaskId/events?limit=100" $Headers
    $callbacks = @($events.items | Where-Object { [string]$_.payload.source_service -eq "execution-plane" })
    $completed = @($events.items | Where-Object { [string]$_.event_type -eq "TASK_COMPLETED" })
    if ($callbacks.Count -gt 0 -and $completed.Count -gt 0) { return $callbacks.Count }
    Start-Sleep -Milliseconds 250
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Task $TaskId has no completed Execution callback"
}

function Submit-Batch {
  param([string]$KbId, [string]$Operation, [string[]]$DocIds, [hashtable]$Headers, [hashtable]$Parameters = @{})
  $body = @{ operation = $Operation; doc_ids = $DocIds; batch_name = "$Operation-$suffix" }
  foreach ($entry in $Parameters.GetEnumerator()) { $body[$entry.Key] = $entry.Value }
  $accepted = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$KbId/documents/batch" $Headers $body
  Assert-True ([int]$accepted.accepted_count -eq $DocIds.Count) "Batch $Operation did not accept every document"
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$accepted.parent_task_id)) "Batch $Operation parent task is missing"
  Wait-ControlTask ([string]$accepted.parent_task_id) $Headers | Out-Null
  $detail = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/batches/$($accepted.batch_id)" $Headers
  Assert-True ([string]$detail.item.status -eq "SUCCEEDED") "Batch $Operation aggregate is $($detail.item.status)"
  Assert-True ([int]$detail.item.total_items -eq $DocIds.Count) "Batch $Operation total count mismatch"
  Assert-True ([int]$detail.item.succeeded_items -eq $DocIds.Count) "Batch $Operation success count mismatch"
  Assert-True ([int]$detail.item.failed_items -eq 0 -and [int]$detail.item.cancelled_items -eq 0 -and [int]$detail.item.running_items -eq 0) "Batch $Operation terminal counters are inconsistent"
  $children = @($detail.items | Where-Object { -not [bool]$_.batch_is_parent })
  Assert-True ($children.Count -eq $DocIds.Count) "Batch $Operation child count mismatch"
  return @{ accepted = $accepted; detail = $detail; children = $children }
}

function Remove-SmokeKb {
  param([string]$KbId, [hashtable]$Headers)
  if (-not [string]::IsNullOrWhiteSpace($KbId)) {
    Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$KbId" $Headers | Out-Null
  }
}

function Assert-DatabaseCleanup {
  param([string[]]$KbIds, [string[]]$TaskIds)
  Assert-True ($TenantId -match '^[A-Za-z0-9_-]+$') "Unsafe tenant id for cleanup verification"
  foreach ($kbId in $KbIds) {
    Assert-True ($kbId -match '^[0-9a-f-]{36}$') "Unsafe KB id for cleanup verification"
    $sql = "SELECT COALESCE(SUM(c),0) FROM (" +
      "SELECT count(*) c FROM knowledge_bases WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM chunks WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM chunk_drafts WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM summary_segment_cache WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM tasks WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM task_events WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM task_batches WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM task_dedup_locks WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
      "SELECT count(*) FROM kb_tenant_bindings WHERE owner_tenant_id='$TenantId' AND kb_id='$kbId') x"
    $remaining = (& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $sql).Trim()
    Assert-True ($LASTEXITCODE -eq 0 -and [int]$remaining -eq 0) "PostgreSQL cleanup failed for KB ${kbId}: $remaining rows"
  }
  foreach ($taskId in $TaskIds) {
    Assert-True ($taskId -match '^[0-9a-f-]{36}$') "Unsafe task id for cleanup verification"
  }
  if ($TaskIds.Count -gt 0) {
    $taskIdsSql = ($TaskIds | ForEach-Object { "'$_'" }) -join ','
    $remainingOutboxes = (& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc "SELECT count(*) FROM task_dispatch_outbox WHERE task_id IN ($taskIdsSql)").Trim()
    Assert-True ($LASTEXITCODE -eq 0 -and [int]$remainingOutboxes -eq 0) "PostgreSQL outbox cleanup failed: $remainingOutboxes rows"
  }
}

$sourceKbId = ""
$targetKbId = ""
$controlHeaders = $null
$objectRefs = [System.Collections.Generic.List[string]]::new()
$batchIds = [System.Collections.Generic.List[string]]::new()
$childTaskIds = [System.Collections.Generic.List[string]]::new()
$dispatchedTaskIds = [System.Collections.Generic.List[string]]::new()
$callbackCount = 0
$succeeded = $false
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)

try {
  $login = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId; username = $Username; password = $Password
  }
  $controlHeaders = @{
    Authorization = "Bearer $($login.accessToken)"
    "X-Trace-Id" = "document-batch-authority-smoke-$suffix"
  }
  $sourceKbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "batch-source-$suffix" }).item.kb_id
  $targetKbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "batch-target-$suffix" }).item.kb_id
  Assert-True ($sourceKbId -match '^[0-9a-f-]{36}$' -and $targetKbId -match '^[0-9a-f-]{36}$') "Smoke KB ids are invalid"

  $docIds = [System.Collections.Generic.List[string]]::new()
  foreach ($index in 1..2) {
    $ingest = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/ingest" $controlHeaders @{
      filename = "batch-$index-$suffix.txt"
      content = "Agent Powflow batch authority document $index $suffix validates aggregate task state, tags, move and delete."
      chunk_size = 100; chunk_overlap = 0; auto_summary = $false
    }
    $docIds.Add([string]$ingest.doc_id)
    $dispatchedTaskIds.Add([string]$ingest.task_id)
    Wait-ControlTask ([string]$ingest.task_id) $controlHeaders | Out-Null
    $callbackCount += Assert-ControlCallback ([string]$ingest.task_id) $controlHeaders
  }

  $chunk = Submit-Batch $sourceKbId "chunk" $docIds.ToArray() $controlHeaders @{
    chunk_method = "sentence"; chunk_size = 100; chunk_overlap = 0
  }
  $tag = Submit-Batch $sourceKbId "tag" $docIds.ToArray() $controlHeaders @{
    tags = @("batch-authority-$suffix"); mode = "replace"
  }
  $move = Submit-Batch $sourceKbId "move" $docIds.ToArray() $controlHeaders @{
    target_kb_id = $targetKbId
  }
  foreach ($batch in @($chunk, $tag, $move)) {
    $batchIds.Add([string]$batch.accepted.batch_id)
    foreach ($child in $batch.children) {
      $childTaskIds.Add([string]$child.task_id)
      $dispatchedTaskIds.Add([string]$child.task_id)
      $callbackCount += Assert-ControlCallback ([string]$child.task_id) $controlHeaders
    }
  }

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"; "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username; "X-Trace-Id" = "document-batch-authority-smoke-$suffix"
    "X-Source-Service" = "document-batch-authority-smoke"
  }
  $sourceDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$sourceKbId/documents" $executionHeaders
  $targetDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  foreach ($docId in $docIds) {
    Assert-True (@($sourceDocs.items | Where-Object { [string]$_.doc_id -eq $docId }).Count -eq 0) "Moved document $docId remains in source KB"
    $moved = @($targetDocs.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
    Assert-True ($null -ne $moved) "Moved document $docId is missing from target KB"
    Assert-True ([int]$moved.chunk_overlap -eq 0) "Zero overlap was not preserved for $docId"
    Assert-True (@($moved.tags_json) -contains "batch-authority-$suffix") "Replace tags were not preserved for $docId"
  }

  $idsSql = ($docIds | ForEach-Object { "'$_'" }) -join ','
  $refsSql = "SELECT source_object_bucket||'|'||source_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$targetKbId' AND doc_id IN ($idsSql) AND source_object_key<>'' UNION SELECT source_object_bucket||'|'||extracted_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$targetKbId' AND doc_id IN ($idsSql) AND extracted_object_key<>''"
  foreach ($line in @(& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $refsSql)) {
    if (-not [string]::IsNullOrWhiteSpace($line)) { $objectRefs.Add($line.Trim()) }
  }
  Assert-True ($LASTEXITCODE -eq 0 -and $objectRefs.Count -eq 4) "Expected four exact MinIO object references"

  $delete = Submit-Batch $targetKbId "delete" $docIds.ToArray() $controlHeaders
  $batchIds.Add([string]$delete.accepted.batch_id)
  foreach ($child in $delete.children) {
    $childTaskIds.Add([string]$child.task_id)
    $dispatchedTaskIds.Add([string]$child.task_id)
    $callbackCount += Assert-ControlCallback ([string]$child.task_id) $controlHeaders
  }
  $afterDelete = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  Assert-True (@($afterDelete.items | Where-Object { $docIds -contains [string]$_.doc_id }).Count -eq 0) "Batch-deleted documents remain visible"

  $dispatches = Invoke-Json GET "$ControlBase/api/v1/admin/task-dispatches" $controlHeaders
  foreach ($taskId in $childTaskIds) {
    $dispatch = @($dispatches.items | Where-Object { [string]$_.task_id -eq $taskId }) | Select-Object -First 1
    Assert-True ([string]$dispatch.status -eq "PUBLISHED") "Outbox for child $taskId is not PUBLISHED"
  }

  Remove-SmokeKb $sourceKbId $controlHeaders
  Remove-SmokeKb $targetKbId $controlHeaders
  Assert-DatabaseCleanup @($sourceKbId, $targetKbId) $dispatchedTaskIds.ToArray()

  $collections = (Invoke-Json GET "http://127.0.0.1:6333/collections").result.collections
  foreach ($kbId in @($sourceKbId, $targetKbId)) {
    $normalized = $kbId.Replace('-', '_')
    Assert-True (@($collections | Where-Object { [string]$_.name -like "*$normalized*" }).Count -eq 0) "Qdrant collection remains for KB $kbId"
  }

  & docker exec agentpowflow-minio sh -c 'mc alias set root https://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" --insecure >/dev/null'
  Assert-True ($LASTEXITCODE -eq 0) "Unable to configure exact MinIO cleanup verifier"
  foreach ($reference in $objectRefs) {
    $parts = $reference.Split('|', 2)
    Assert-True ($parts.Count -eq 2 -and $parts[0] -match '^[A-Za-z0-9._-]+$' -and $parts[1] -match '^v1/tenants/[0-9a-f]+/kbs/[0-9a-f]+/documents/') "Unsafe MinIO reference"
    $remaining = @(& docker exec agentpowflow-minio mc ls --insecure --versions "root/$($parts[0])/$($parts[1])")
    Assert-True ($LASTEXITCODE -eq 0 -and $remaining.Count -eq 0) "MinIO versions remain for $($parts[1])"
  }

  $succeeded = $true
  [pscustomobject]@{
    status = "ok"; batches = $batchIds.Count; batch_children = $childTaskIds.Count
    ingest_tasks = 2; callback_events = $callbackCount; aggregate_counts_verified = $true
    zero_overlap_preserved = $true; moved = $true; deleted = $true
    postgres_zero_residual = $true; qdrant_zero_residual = $true; minio_all_versions_zero = $true
  } | ConvertTo-Json -Compress
}
finally {
  if (-not $succeeded -and $null -ne $controlHeaders) {
    if (-not [string]::IsNullOrWhiteSpace($sourceKbId)) { try { Remove-SmokeKb $sourceKbId $controlHeaders } catch {} }
    if (-not [string]::IsNullOrWhiteSpace($targetKbId)) { try { Remove-SmokeKb $targetKbId $controlHeaders } catch {} }
  }
}
