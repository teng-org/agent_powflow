param(
  [string]$ControlBase = "http://localhost:8099",
  [string]$ExecutionBase = "http://localhost:8091",
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
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempFile = [IO.Path]::GetFullPath((Join-Path $tempRoot "agent-powflow-multipart-$suffix.txt"))
Assert-True ($tempFile.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) "Unsafe temporary upload path"

try {
  [IO.File]::WriteAllText(
    $tempFile,
    "Agent Powflow multipart authority evidence $suffix proves staged object dispatch and Worker callback.",
    [Text.UTF8Encoding]::new($false)
  )
  $controlLogin = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId
    username = $Username
    password = $Password
  }
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$controlLogin.accessToken)) "Control access token missing"
  $controlHeaders = @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Trace-Id" = "multipart-task-authority-smoke-$suffix"
  }

  $kb = Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "multipart-task-authority-smoke-$suffix" }
  $kbId = [string]$kb.item.kb_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($kbId)) "Created KB id missing"

  $uploadRaw = & curl.exe --fail-with-body --silent --show-error `
    -H "Authorization: Bearer $($controlLogin.accessToken)" `
    -H "X-Trace-Id: multipart-task-authority-smoke-$suffix" `
    -F "file=@$tempFile;type=text/plain" `
    -F "process_mode=UPLOAD_CLEAN_CHUNK_EMBED" `
    -F "chunk_method=sentence" `
    -F "chunk_size=100" `
    -F "chunk_overlap=0" `
    -F "auto_summary=false" `
    "$ControlBase/api/v1/admin/kbs/$kbId/documents/upload"
  if ($LASTEXITCODE -ne 0) { throw "multipart upload request failed with curl exit code $LASTEXITCODE" }
  $upload = (($uploadRaw -join "`n") | ConvertFrom-Json)
  Assert-True ([string]$upload.status -eq "accepted") "Multipart upload was not accepted"
  Assert-True ([int]$upload.accepted_count -eq 1) "Expected exactly one accepted upload"
  $taskId = [string]$upload.accepted[0].task_id
  $docId = [string]$upload.accepted[0].doc_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($taskId)) "Accepted task id missing"
  Assert-True (-not [string]::IsNullOrWhiteSpace($docId)) "Accepted document id missing"

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

  $eventDeadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
  $events = $null
  $eventTypes = @()
  $callbackEvents = @()
  do {
    $events = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/$taskId/events?limit=100" $controlHeaders
    $eventTypes = @($events.items | ForEach-Object { [string]$_.event_type })
    $callbackEvents = @($events.items | Where-Object { [string]$_.payload.source_service -eq "execution-plane" })
    if (($eventTypes -contains "TASK_COMPLETED") -and $callbackEvents.Count -gt 0) { break }
    Start-Sleep -Milliseconds 250
  } while ([DateTimeOffset]::UtcNow -lt $eventDeadline)
  Assert-True ($eventTypes -contains "TASK_UPLOAD_STAGED") "TASK_UPLOAD_STAGED event missing"
  Assert-True ($eventTypes -contains "TASK_COMPLETED") "TASK_COMPLETED event missing"
  Assert-True ($callbackEvents.Count -gt 0) "No event proves the Execution-to-Control callback path"

  $dispatches = Invoke-Json GET "$ControlBase/api/v1/admin/task-dispatches" $controlHeaders
  $dispatch = @($dispatches.items | Where-Object { [string]$_.task_id -eq $taskId }) | Select-Object -First 1
  Assert-True ([string]$dispatch.status -eq "PUBLISHED") "Task dispatch was not published"

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username
    "X-Trace-Id" = "multipart-task-authority-smoke-$suffix"
    "X-Source-Service" = "multipart-task-authority-smoke"
  }
  $documents = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents" $executionHeaders
  $document = @($documents.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ([string]$document.status -eq "INDEXED") "Document is not indexed"

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
  if (Test-Path -LiteralPath $tempFile -PathType Leaf) {
    Remove-Item -LiteralPath $tempFile -Force
  }
}
