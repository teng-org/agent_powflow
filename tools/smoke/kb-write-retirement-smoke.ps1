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

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "agentpowflow-kb-write-retirement-$suffix"
$legacyBodyPath = Join-Path $tempRoot "legacy-request.json"
$legacyResponsePath = Join-Path $tempRoot "legacy-response.json"
$kbId = ""
$docId = ""
$taskId = ""
$controlHeaders = $null
$objectRefs = [System.Collections.Generic.List[string]]::new()
$succeeded = $false

try {
  [IO.Directory]::CreateDirectory($tempRoot) | Out-Null

  $executionLogin = Invoke-Json POST "$ExecutionBase/api/v1/admin/auth/login" -Body @{ username = $Username; password = $Password }
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $TenantId
    "X-Actor-Id" = $Username
    "X-Trace-Id" = "kb-write-retirement-smoke-$suffix"
    "X-Source-Service" = "kb-write-retirement-smoke"
  }
  [IO.File]::WriteAllText($legacyBodyPath, (@{ name = "retired-$suffix" } | ConvertTo-Json -Compress))
  $legacyCode = & curl.exe -sS -o $legacyResponsePath -w "%{http_code}" `
    -X POST "$ExecutionBase/api/v1/admin/kbs" `
    -H "Authorization: $($executionHeaders.Authorization)" `
    -H "X-Tenant-Id: $TenantId" `
    -H "X-Actor-Id: $Username" `
    -H "X-Trace-Id: $($executionHeaders.'X-Trace-Id')" `
    -H "Content-Type: application/json" `
    --data-binary "@$legacyBodyPath"
  Assert-True ($LASTEXITCODE -eq 0 -and [int]$legacyCode -eq 410) "Legacy Execution write returned HTTP $legacyCode instead of 410"
  $legacyResponse = Get-Content -Raw -LiteralPath $legacyResponsePath | ConvertFrom-Json
  Assert-True ([string]$legacyResponse.detail.code -eq "LEGACY_KB_WRITE_RETIRED") "Legacy retirement error code mismatch"

  $controlLogin = Invoke-Json POST "$ControlBase/api/v1/auth/login" -Body @{
    tenantCode = $TenantId; username = $Username; password = $Password
  }
  $controlHeaders = @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Trace-Id" = "kb-write-retirement-smoke-$suffix"
  }
  $kbId = [string](Invoke-Json POST "$ControlBase/api/v1/admin/kbs" $controlHeaders @{ name = "write-retirement-$suffix" }).item.kb_id
  Assert-True ($kbId -match '^[0-9a-f-]{36}$') "Smoke KB id is invalid"

  $ingest = Invoke-Json POST "$ControlBase/api/v1/admin/kbs/$kbId/documents/ingest" $controlHeaders @{
    filename = "write-retirement-$suffix.txt"
    content = "Agent Powflow write retirement $suffix verifies Control-owned draft and freeze metadata mutations."
    chunk_method = "sentence"; chunk_size = 120; chunk_overlap = 0; auto_summary = $false
  }
  $docId = [string]$ingest.doc_id
  $taskId = [string]$ingest.task_id
  Assert-True ($docId -match '^[0-9a-f-]{36}$' -and $taskId -match '^[0-9a-f-]{36}$') "Ingest did not return durable identities"
  Wait-ControlTask $taskId $controlHeaders | Out-Null

  $documents = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents" $executionHeaders
  $document = @($documents.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  Assert-True ($null -ne $document) "Ingested document is not readable from Execution"
  $chunks = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks" $executionHeaders
  $chunk = @($chunks.items) | Select-Object -First 1
  $chunkId = [string]$chunk.chunk_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($chunkId)) "Ingested document has no chunk"
  $chunkPathId = [Uri]::EscapeDataString($chunkId)

  $draftText = "Control-owned revised draft $suffix"
  Invoke-Json PUT "$ControlBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks/$chunkPathId/draft" $controlHeaders @{ text = $draftText } | Out-Null
  $drafted = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks" $executionHeaders
  $draft = @($drafted.drafts | Where-Object { [string]$_.chunk_id -eq $chunkId }) | Select-Object -First 1
  Assert-True ([string]$draft.action -eq "edit" -and [string]$draft.text -eq $draftText) "Control draft proxy did not persist the draft"

  Invoke-Json PATCH "$ControlBase/api/v1/admin/kbs/$kbId/documents/$docId/freeze" $controlHeaders @{ frozen = $true } | Out-Null
  Invoke-Json PATCH "$ControlBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks/$chunkPathId/freeze" $controlHeaders @{ frozen = $true } | Out-Null
  $frozenDocuments = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents" $executionHeaders
  $frozenDocument = @($frozenDocuments.items | Where-Object { [string]$_.doc_id -eq $docId }) | Select-Object -First 1
  $frozenChunks = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks" $executionHeaders
  $frozenChunk = @($frozenChunks.items | Where-Object { [string]$_.chunk_id -eq $chunkId }) | Select-Object -First 1
  Assert-True ([bool]$frozenDocument.frozen -and [bool]$frozenChunk.frozen) "Control freeze proxies did not persist both states"

  Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks/$chunkPathId/draft" $controlHeaders | Out-Null
  $cleared = Invoke-Json GET "$ExecutionBase/api/v1/admin/kbs/$kbId/documents/$docId/chunks" $executionHeaders
  Assert-True (@($cleared.drafts | Where-Object { [string]$_.chunk_id -eq $chunkId }).Count -eq 0) "Cleared draft remains visible"

  Assert-True ($TenantId -match '^[A-Za-z0-9_-]+$') "Unsafe tenant id"
  $refsSql = "SELECT source_object_bucket||'|'||source_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' AND source_object_key<>'' UNION SELECT source_object_bucket||'|'||extracted_object_key FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' AND extracted_object_key<>''"
  foreach ($line in @(& docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc $refsSql)) {
    if (-not [string]::IsNullOrWhiteSpace($line)) { $objectRefs.Add($line.Trim()) }
  }
  Assert-True ($LASTEXITCODE -eq 0 -and $objectRefs.Count -eq 2) "Expected two exact MinIO object references"

  Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $controlHeaders | Out-Null
  $dbSql = "SELECT COALESCE(SUM(c),0) FROM (" +
    "SELECT count(*) c FROM knowledge_bases WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM documents WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM chunks WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM chunk_drafts WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM tasks WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM task_events WHERE tenant_id='$TenantId' AND kb_id='$kbId' UNION ALL " +
    "SELECT count(*) FROM task_dispatch_outbox WHERE task_id='$taskId') x"
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
    status = "ok"; legacy_write_http = 410; legacy_code = "LEGACY_KB_WRITE_RETIRED"
    control_draft_saved = $true; control_draft_cleared = $true
    document_freeze = $true; chunk_freeze = $true
    postgres_zero_residual = $true; qdrant_zero_residual = $true; minio_all_versions_zero = $true
  } | ConvertTo-Json -Compress
}
finally {
  if (-not $succeeded -and $null -ne $controlHeaders -and $kbId -match '^[0-9a-f-]{36}$') {
    try { Invoke-Json DELETE "$ControlBase/api/v1/admin/kbs/$kbId" $controlHeaders | Out-Null } catch {}
  }
  foreach ($path in @($legacyBodyPath, $legacyResponsePath)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
  }
  if (Test-Path -LiteralPath $tempRoot -PathType Container) { Remove-Item -LiteralPath $tempRoot -Force }
}
