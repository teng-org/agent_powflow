param(
  [string]$ControlBase = "http://127.0.0.1:8099",
  [string]$ExecutionBase = "http://127.0.0.1:8091",
  [string]$TenantId = "platform",
  [string]$Username = "admin",
  [string]$Password = "admin123"
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

function Submit-Upload {
  param([string]$KbId, [string]$Mode, [string]$FilePath, [hashtable]$Headers, [string]$OutputPath)
  $httpCode = & curl.exe -sS --fail-with-body -o $OutputPath -w "%{http_code}" `
    -X POST "$ControlBase/api/v1/admin/kbs/$KbId/documents/upload" `
    -H "Authorization: $($Headers.Authorization)" `
    -H "X-Trace-Id: $($Headers.'X-Trace-Id')" `
    -F "file=@$FilePath;type=text/plain" `
    -F "process_mode=$Mode" `
    -F "chunk_method=sentence" `
    -F "chunk_size=500" `
    -F "chunk_overlap=50" `
    -F "auto_summary=false"
  Assert-True ($LASTEXITCODE -eq 0 -and [int]$httpCode -eq 200) "$Mode upload failed with HTTP $httpCode"
  $response = Get-Content -Raw -LiteralPath $OutputPath | ConvertFrom-Json
  Assert-True ([string]$response.status -eq "accepted" -and [int]$response.accepted_count -eq 1) "$Mode was not accepted"
  $item = @($response.accepted)[0]
  Assert-True ([string]$item.process_mode -eq $Mode) "$Mode response identity mismatch"
  Assert-True ([string]$item.task_id -match '^[0-9a-f-]{36}$') "$Mode did not return a durable Control task"
  Assert-True ([string]$item.doc_id -match '^[0-9a-f-]{36}$') "$Mode did not return a document id"
  return $item
}

function Assert-TaskFact {
  param([object]$Item, [string]$ExpectedStage, [string]$ExpectedMode, [hashtable]$Headers)
  $taskId = [string]$Item.task_id
  $task = (Invoke-Json GET "$ControlBase/api/v1/tasks/$taskId" $Headers).record
  Assert-True ([string]$task.task_type -eq "DOC_UPLOAD") "Task $taskId type is not DOC_UPLOAD"
  Assert-True ([string]$task.status -eq "SUCCEEDED" -and [string]$task.stage -eq $ExpectedStage) "Task $taskId terminal fact mismatch"
  Assert-True ([string]$task.state_owner -eq "CONTROL" -and [int]$task.progress_pct -eq 100) "Task $taskId is not Control-owned and complete"
  $events = Invoke-Json GET "$ControlBase/api/v1/admin/tasks/$taskId/events?limit=100" $Headers
  $completed = @($events.items | Where-Object {
    [string]$_.event_type -eq "TASK_UPLOAD_COMPLETED" -and
    [string]$_.stage -eq $ExpectedStage -and
    [string]$_.payload.process_mode -eq $ExpectedMode
  })
  Assert-True ($completed.Count -eq 1) "Task $taskId has no immutable completion event"
}

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "agentpowflow-non-index-$suffix"
$rawFile = Join-Path $tempRoot "raw-$suffix.txt"
$cleanFile = Join-Path $tempRoot "clean-$suffix.txt"
$rawResponse = Join-Path $tempRoot "raw-response.json"
$cleanResponse = Join-Path $tempRoot "clean-response.json"
$kbId = ""
$headers = $null
$taskIds = [System.Collections.Generic.List[string]]::new()
$objectRefs = [System.Collections.Generic.List[string]]::new()
$succeeded = $false

try {
  [IO.Directory]::CreateDirectory($tempRoot) | Out-Null
  [IO.File]::WriteAllText($rawFile, "  Raw upload authority $suffix keeps original spacing.  ")
  [IO.File]::WriteAllText($cleanFile, "  Clean   upload authority $suffix`r`n`r`nnormalizes text before persistence.  ")
  $login = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId; username = $Username; password = $Password
  }
  $headers = @{
    Authorization = "Bearer $($login.accessToken)"
    "X-Trace-Id" = "non-index-upload-authority-smoke-$suffix"
  }
  $kbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $headers @{ name = "non-index-upload-$suffix" }).item.kb_id
  Assert-True ($kbId -match '^[0-9a-f-]{36}$') "Smoke KB id is invalid"

  $raw = Submit-Upload $kbId "UPLOAD_ONLY" $rawFile $headers $rawResponse
  $clean = Submit-Upload $kbId "UPLOAD_AND_CLEAN" $cleanFile $headers $cleanResponse
  foreach ($item in @($raw, $clean)) { $taskIds.Add([string]$item.task_id) }
  Assert-TaskFact $raw "UPLOADED_RAW" "UPLOAD_ONLY" $headers
  Assert-TaskFact $clean "CLEANED" "UPLOAD_AND_CLEAN" $headers

  $dispatches = Invoke-Json GET "$ControlBase/api/v1/admin/task-dispatches" $headers
  foreach ($taskId in $taskIds) {
    $fact = @($dispatches.items | Where-Object { [string]$_.task_id -eq $taskId }) | Select-Object -First 1
    Assert-True ([string]$fact.command_type -eq "DOC_UPLOAD_ARTIFACT" -and [string]$fact.status -eq "COMPLETED") "Task $taskId persistent upload fact mismatch"
  }

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"; "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username; "X-Trace-Id" = "non-index-upload-authority-smoke-$suffix"
    "X-Source-Service" = "non-index-upload-authority-smoke"
  }
  $documents = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents" $executionHeaders
  $rawDoc = @($documents.items | Where-Object { [string]$_.doc_id -eq [string]$raw.doc_id }) | Select-Object -First 1
  $cleanDoc = @($documents.items | Where-Object { [string]$_.doc_id -eq [string]$clean.doc_id }) | Select-Object -First 1
  Assert-True ([string]$rawDoc.status -eq "UPLOADED_RAW" -and [int]$rawDoc.chunk_count -eq 0) "Raw document state mismatch"
  Assert-True ([string]$cleanDoc.status -eq "CLEANED" -and [int]$cleanDoc.chunk_count -eq 0) "Clean document state mismatch"

  Assert-True ($TenantId -match '^[A-Za-z0-9_-]+$') "Unsafe tenant id"
  $refsSql = "SELECT source_object_bucket||'|'||source_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' AND source_object_key<>'' UNION ALL SELECT source_object_bucket||'|'||extracted_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' AND extracted_object_key<>''"
  foreach ($line in @(& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $refsSql)) {
    if (-not [string]::IsNullOrWhiteSpace($line)) { $objectRefs.Add($line.Trim()) }
  }
  Assert-True ($LASTEXITCODE -eq 0 -and $objectRefs.Count -eq 4) "Expected four exact object references"

  Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $headers | Out-Null
  $taskIdsSql = ($taskIds | ForEach-Object { "'$_'" }) -join ','
  $dbSql = "SELECT (SELECT count(*) FROM knowledge_bases WHERE tenant_id='$TenantId' AND kb_id='$kbId') + (SELECT count(*) FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId') + (SELECT count(*) FROM chunks WHERE tenant_id='$TenantId' AND kb_id='$kbId') + (SELECT count(*) FROM tasks WHERE tenant_id='$TenantId' AND kb_id='$kbId') + (SELECT count(*) FROM task_events WHERE tenant_id='$TenantId' AND kb_id='$kbId') + (SELECT count(*) FROM task_dispatch_outbox WHERE task_id IN ($taskIdsSql))"
  $dbRemaining = (& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $dbSql).Trim()
  Assert-True ($LASTEXITCODE -eq 0 -and [int]$dbRemaining -eq 0) "PostgreSQL cleanup left $dbRemaining rows"

  $normalizedKb = $kbId.Replace('-', '_')
  $collections = (Invoke-Json GET "http://127.0.0.1:6333/collections").result.collections
  Assert-True (@($collections | Where-Object { [string]$_.name -like "*$normalizedKb*" }).Count -eq 0) "Qdrant collection remains"

  & docker exec agentpowflow-minio sh -c 'mc alias set root https://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" --insecure >/dev/null'
  Assert-True ($LASTEXITCODE -eq 0) "Unable to configure MinIO verifier"
  foreach ($reference in $objectRefs) {
    $parts = $reference.Split('|', 2)
    Assert-True ($parts.Count -eq 2 -and $parts[0] -match '^[A-Za-z0-9._-]+$' -and $parts[1] -match '^v1/tenants/[0-9a-f]+/kbs/[0-9a-f]+/documents/') "Unsafe object reference"
    $remaining = @(& docker exec agentpowflow-minio mc ls --insecure --versions "root/$($parts[0])/$($parts[1])")
    Assert-True ($LASTEXITCODE -eq 0 -and $remaining.Count -eq 0) "MinIO versions remain for $($parts[1])"
  }

  $succeeded = $true
  [pscustomobject]@{
    status = "ok"; modes = 2; control_tasks = $taskIds.Count; completion_events = 2
    persistent_facts_completed = 2; execution_documents_verified = 2; zero_chunks = $true
    postgres_zero_residual = $true; qdrant_zero_residual = $true; minio_all_versions_zero = $true
  } | ConvertTo-Json -Compress
}
finally {
  if (-not $succeeded -and $null -ne $headers -and $kbId -match '^[0-9a-f-]{36}$') {
    try { Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $headers | Out-Null } catch {}
  }
  foreach ($path in @($rawFile, $cleanFile, $rawResponse, $cleanResponse)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
  }
  if (Test-Path -LiteralPath $tempRoot -PathType Container) { Remove-Item -LiteralPath $tempRoot -Force }
}
