param(
  [string]$ControlBase = "http://localhost:8099",
  [string]$ExecutionBase = "http://localhost:8091",
  [string]$TenantId = "platform",
  [string]$Username = "admin",
  [string]$Password = "admin123",
  [switch]$SkipProviderCommands,
  [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

function Invoke-Json {
  param([string]$Method, [string]$Uri, [hashtable]$Headers = @{}, [object]$Body = $null)
  $parameters = @{ Method = $Method; Uri = $Uri; Headers = $Headers; UseBasicParsing = $true; TimeoutSec = 30 }
  if ($null -ne $Body) {
    $parameters.ContentType = "application/json"
    $parameters.Body = ($Body | ConvertTo-Json -Depth 12 -Compress)
  }
  return Invoke-RestMethod @parameters
}

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Wait-ControlTask {
  param([string]$TaskId, [hashtable]$Headers, [string]$ControlBase, [int]$TimeoutSeconds)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $response = Invoke-Json GET "$ControlBase/api/v1/tasks/$TaskId" $Headers
    $task = $response.record
    if ($null -ne $task -and [string]$task.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) {
      Assert-True ([string]$task.status -eq "SUCCEEDED") "Task $TaskId failed: status=$($task.status), error=$($task.error_message)"
      Assert-True ([string]$task.state_owner -eq "CONTROL") "Task $TaskId is not Control-owned"
      return $task
    }
    Start-Sleep -Milliseconds 500
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Task $TaskId did not reach a terminal state within $TimeoutSeconds seconds"
}

function Assert-ControlCallback {
  param([string]$TaskId, [hashtable]$Headers, [string]$ControlBase)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
  do {
    $events = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/$TaskId/events?limit=100" $Headers
    $callbacks = @($events.items | Where-Object { [string]$_.payload.source_service -eq "execution-plane" })
    $completed = @($events.items | Where-Object { [string]$_.event_type -eq "TASK_COMPLETED" })
    if ($callbacks.Count -gt 0 -and $completed.Count -gt 0) { return $callbacks.Count }
    Start-Sleep -Milliseconds 250
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Task $TaskId has no completed Execution-to-Control callback evidence"
}

$sourceKbId = ""
$targetKbId = ""
$taskIds = [System.Collections.Generic.List[string]]::new()
$callbackCount = 0
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)

try {
  $login = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId
    username = $Username
    password = $Password
  }
  $controlHeaders = @{
    Authorization = "Bearer $($login.accessToken)"
    "X-Trace-Id" = "document-command-authority-smoke-$suffix"
  }
  $source = Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "document-command-source-$suffix" }
  $target = Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "document-command-target-$suffix" }
  $sourceKbId = [string]$source.item.kb_id
  $targetKbId = [string]$target.item.kb_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($sourceKbId)) "Source KB id missing"
  Assert-True (-not [string]::IsNullOrWhiteSpace($targetKbId)) "Target KB id missing"

  $ingest = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/ingest" $controlHeaders @{
    filename = "document-command-$suffix.txt"
    content = "Agent Powflow document command authority $suffix verifies chunk summary commit tag move and delete callbacks."
    chunk_size = 100
    chunk_overlap = 0
    auto_summary = $false
  }
  $docId = [string]$ingest.doc_id
  $taskIds.Add([string]$ingest.task_id)
  Wait-ControlTask $ingest.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $ingest.task_id $controlHeaders $ControlBase

  $chunk = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/chunk" $controlHeaders @{
    chunk_method = "sentence"
    chunk_size = 100
    chunk_overlap = 0
  }
  $taskIds.Add([string]$chunk.task_id)
  Wait-ControlTask $chunk.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $chunk.task_id $controlHeaders $ControlBase

  if (-not $SkipProviderCommands) {
    $summary = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/summarize" $controlHeaders @{}
    $taskIds.Add([string]$summary.task_id)
    Wait-ControlTask $summary.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
    $callbackCount += Assert-ControlCallback $summary.task_id $controlHeaders $ControlBase
  }

  $commit = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/chunks/commit" $controlHeaders @{}
  $taskIds.Add([string]$commit.task_id)
  Wait-ControlTask $commit.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $commit.task_id $controlHeaders $ControlBase

  $tag = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/tags" $controlHeaders @{
    tags = @("authority-$suffix")
    mode = "replace"
  }
  $taskIds.Add([string]$tag.task_id)
  Wait-ControlTask $tag.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $tag.task_id $controlHeaders $ControlBase

  $move = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$sourceKbId/documents/$docId/move" $controlHeaders @{
    target_kb_id = $targetKbId
  }
  $taskIds.Add([string]$move.task_id)
  Wait-ControlTask $move.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $move.task_id $controlHeaders $ControlBase

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username
    "X-Trace-Id" = "document-command-authority-smoke-$suffix"
    "X-Source-Service" = "document-command-authority-smoke"
  }
  $sourceDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$sourceKbId/documents" $executionHeaders
  $targetDocs = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  Assert-True (-not (@($sourceDocs.items | Where-Object { [string]$_.doc_id -eq $docId }).Count)) "Moved document remains in source KB"
  $movedDoc = @($targetDocs.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ($null -ne $movedDoc) "Moved document missing from target KB"
  Assert-True ([int]$movedDoc.chunk_overlap -eq 0) "Zero chunk overlap was not preserved"
  Assert-True (@($movedDoc.tags_json) -contains "authority-$suffix") "Replace-tag command was not applied"
  if (-not $SkipProviderCommands) {
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$movedDoc.summary_text)) "Document summary was not persisted"
  }

  $delete = Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$targetKbId/documents/$docId" $controlHeaders
  $taskIds.Add([string]$delete.task_id)
  Wait-ControlTask $delete.task_id $controlHeaders $ControlBase $TimeoutSeconds | Out-Null
  $callbackCount += Assert-ControlCallback $delete.task_id $controlHeaders $ControlBase
  $afterDelete = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$targetKbId/documents" $executionHeaders
  Assert-True (-not (@($afterDelete.items | Where-Object { [string]$_.doc_id -eq $docId }).Count)) "Deleted document remains visible"

  $dispatches = Invoke-Json GET "$ControlBase/api/v1/admin/task-dispatches" $controlHeaders
  foreach ($taskId in $taskIds) {
    $dispatch = @($dispatches.items | Where-Object { [string]$_.task_id -eq $taskId }) | Select-Object -First 1
    Assert-True ([string]$dispatch.status -eq "PUBLISHED") "Outbox for task $taskId was not published"
  }

  [pscustomobject]@{
    status = "ok"
    source_kb_id = $sourceKbId
    target_kb_id = $targetKbId
    doc_id = $docId
    command_tasks = $taskIds.Count
    control_owned_tasks = $taskIds.Count
    callback_events = $callbackCount
    zero_overlap_preserved = $true
    summary_persisted = (-not $SkipProviderCommands)
    provider_commands_skipped = [bool]$SkipProviderCommands
    moved = $true
    deleted = $true
  } | ConvertTo-Json -Compress
}
finally {
  if ($null -ne $controlHeaders) {
    if (-not [string]::IsNullOrWhiteSpace($sourceKbId)) {
      try { Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$sourceKbId" $controlHeaders | Out-Null } catch {}
    }
    if (-not [string]::IsNullOrWhiteSpace($targetKbId)) {
      try { Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$targetKbId" $controlHeaders | Out-Null } catch {}
    }
  }
}
