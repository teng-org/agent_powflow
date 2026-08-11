param(
  [string]$ControlBase = "http://localhost:8099",
  [string]$ExecutionBase = "http://localhost:8090",
  [string]$TenantId = "platform",
  [string]$Username = "admin",
  [string]$Password = "admin123",
  [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

function Invoke-Json {
  param([string]$Method, [string]$Uri, [hashtable]$Headers = @{}, [object]$Body = $null)
  $parameters = @{ Method = $Method; Uri = $Uri; Headers = $Headers; UseBasicParsing = $true }
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

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$kbId = ""

try {
  $controlLogin = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId
    username = $Username
    password = $Password
  }
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$controlLogin.accessToken)) "Control access token missing"
  $controlHeaders = @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Trace-Id" = "task-authority-smoke-$suffix"
  }

  $kb = Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "task-authority-smoke-$suffix" }
  $kbId = [string]$kb.item.kb_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($kbId)) "Created KB id missing"

  $docId = "doc-$suffix"
  $marker = "task-authority-evidence-$suffix"
  $accepted = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$kbId/documents/ingest" $controlHeaders @{
    doc_id = $docId
    filename = "authority-smoke.txt"
    content = "Agent Powflow $marker verifies Control-owned reliable task dispatch and worker callback."
    chunk_size = 100
    chunk_overlap = 0
    auto_summary = $false
  }
  $taskId = [string]$accepted.task_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($taskId)) "Accepted task id missing"

  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  $task = $null
  do {
    $status = Invoke-Json GET "$ControlBase/api/v1/tasks/$taskId" $controlHeaders
    $task = $status.record
    if ($null -ne $task -and [string]$task.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 500
  } while ([DateTimeOffset]::UtcNow -lt $deadline)

  Assert-True ($null -ne $task) "Control task record missing"
  Assert-True ([string]$task.status -eq "SUCCEEDED") "Task did not succeed: status=$($task.status), error=$($task.error_message)"
  Assert-True ([string]$task.state_owner -eq "CONTROL") "Task authority regressed from CONTROL"

  $eventDeadline = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Min(15, $TimeoutSeconds))
  do {
    $events = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/$taskId/events?limit=100" $controlHeaders
    $eventTypes = @($events.items | ForEach-Object { [string]$_.event_type })
    $callbackEvents = @($events.items | Where-Object { [string]$_.payload.source_service -eq "execution-plane" })
    if ($eventTypes -contains "TASK_COMPLETED" -and $callbackEvents.Count -gt 0) { break }
    Start-Sleep -Milliseconds 200
  } while ([DateTimeOffset]::UtcNow -lt $eventDeadline)
  Assert-True ($eventTypes -contains "TASK_CREATED") "TASK_CREATED event missing"
  Assert-True ($eventTypes -contains "TASK_COMPLETED") "TASK_COMPLETED callback missing"
  Assert-True ($callbackEvents.Count -gt 0) "No event proves the Execution-to-Control callback path"

  $dispatches = Invoke-Json GET "$ControlBase/api/v1/admin/task-dispatches" $controlHeaders
  $dispatch = @($dispatches.items | Where-Object { [string]$_.task_id -eq $taskId }) | Select-Object -First 1
  Assert-True ($null -ne $dispatch) "Task dispatch outbox item missing"
  Assert-True ([string]$dispatch.status -eq "PUBLISHED") "Task dispatch was not published: $($dispatch.status)"

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{
    username = $Username
    password = $Password
  }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username
    "X-Trace-Id" = "task-authority-smoke-$suffix"
    "X-Source-Service" = "task-authority-smoke"
  }
  $documents = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents" $executionHeaders
  $document = @($documents.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ($null -ne $document) "Indexed document missing"
  Assert-True ([string]$document.status -eq "INDEXED") "Document is not indexed: $($document.status)"

  [pscustomobject]@{
    status = "ok"
    kb_id = $kbId
    doc_id = $docId
    task_id = $taskId
    task_state = [string]$task.status
    state_owner = [string]$task.state_owner
    outbox_state = [string]$dispatch.status
    document_state = [string]$document.status
    events = $eventTypes.Count
    callback_events = $callbackEvents.Count
  } | ConvertTo-Json -Compress
}
finally {
  if (-not [string]::IsNullOrWhiteSpace($kbId) -and $null -ne $controlHeaders) {
    Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $controlHeaders | Out-Null
  }
}
