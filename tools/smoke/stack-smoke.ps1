param(
  [string]$ComposeFile = "infra/compose/docker-compose.stack.yml",
  [string]$EnvFile = "config/service-ports.env",
  [switch]$AllowExternalEmbeddingProvider
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Invoke-Json {
  param(
    [ValidateSet("GET", "POST", "PUT", "PATCH", "DELETE")][string]$Method,
    [string]$Uri,
    [hashtable]$Headers = @{},
    [object]$Body = $null
  )
  $params = @{
    Method = $Method
    Uri = $Uri
    Headers = $Headers
    TimeoutSec = 15
  }
  $script:LastSmokeRequest = "$Method $Uri"
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = $Body | ConvertTo-Json -Depth 20 -Compress
  }
  Invoke-RestMethod @params
}

function Assert-HttpStatus {
  param(
    [ValidateSet("GET", "POST", "PUT", "PATCH", "DELETE")][string]$Method,
    [string]$Uri,
    [hashtable]$Headers,
    [object]$Body,
    [int]$ExpectedStatus
  )
  try {
    Invoke-Json -Method $Method -Uri $Uri -Headers $Headers -Body $Body | Out-Null
  } catch {
    $response = $_.Exception.Response
    $actualStatus = if ($null -ne $response) { [int]$response.StatusCode } else { 0 }
    if ($actualStatus -eq $ExpectedStatus) { return }
    throw "Expected HTTP $ExpectedStatus from $Method $Uri, received $actualStatus. $($_.Exception.Message)"
  }
  throw "Expected HTTP $ExpectedStatus from $Method $Uri, but the request succeeded."
}

function Get-EntityId {
  param([object]$Item)
  if ($null -eq $Item) { return "" }
  if (-not [string]::IsNullOrWhiteSpace([string]$Item.kb_id)) { return [string]$Item.kb_id }
  if (-not [string]::IsNullOrWhiteSpace([string]$Item.id)) { return [string]$Item.id }
  return ""
}

function New-SmokeWorkflowDsl {
  param([hashtable]$LlmConfig = @{})
  return @{
    entry_node_id = "start-1"
    nodes = @(
      @{ id = "start-1"; type = "start"; config = @{} },
      @{ id = "condition-1"; type = "condition.if"; config = @{ field = "text"; operator = "truthy" } },
      @{ id = "llm-true"; type = "llm.generate"; config = $LlmConfig },
      @{ id = "llm-false"; type = "llm.generate"; config = $LlmConfig },
      @{ id = "end-true"; type = "end"; config = @{} },
      @{ id = "end-false"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "edge-1"; source_node_id = "start-1"; target_node_id = "condition-1"; condition_label = "" },
      @{ id = "edge-true"; source_node_id = "condition-1"; target_node_id = "llm-true"; condition_label = "true" },
      @{ id = "edge-false"; source_node_id = "condition-1"; target_node_id = "llm-false"; condition_label = "false" },
      @{ id = "edge-true-end"; source_node_id = "llm-true"; target_node_id = "end-true"; condition_label = "" },
      @{ id = "edge-false-end"; source_node_id = "llm-false"; target_node_id = "end-false"; condition_label = "" }
    )
  }
}

function New-RetrievalWorkflowDsl {
  param([string]$KbId)
  return @{
    entry_node_id = "start-retrieval"
    nodes = @(
      @{ id = "start-retrieval"; type = "start"; config = @{} },
      @{ id = "search-1"; type = "retrieval.search"; config = @{ kb_id = $KbId; top_k = 3 } },
      @{ id = "answer-1"; type = "llm.generate"; config = @{} },
      @{ id = "end-retrieval"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "retrieval-edge-1"; source_node_id = "start-retrieval"; target_node_id = "search-1"; condition_label = "" },
      @{ id = "retrieval-edge-2"; source_node_id = "search-1"; target_node_id = "answer-1"; condition_label = "" },
      @{ id = "retrieval-edge-3"; source_node_id = "answer-1"; target_node_id = "end-retrieval"; condition_label = "" }
    )
  }
}

function New-ParallelWorkflowDsl {
  return @{
    entry_node_id = "start-parallel"
    nodes = @(
      @{ id = "start-parallel"; type = "start"; config = @{} },
      @{ id = "fork-parallel"; type = "parallel.fork"; config = @{} },
      @{ id = "draft-parallel"; type = "llm.generate"; config = @{ mock_delay_ms = 150 } },
      @{ id = "review-parallel"; type = "llm.generate"; config = @{ mock_delay_ms = 150 } },
      @{ id = "join-parallel"; type = "parallel.join"; config = @{ answer_branch = "draft" } },
      @{ id = "end-parallel"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "parallel-edge-1"; source_node_id = "start-parallel"; target_node_id = "fork-parallel"; condition_label = "" },
      @{ id = "parallel-edge-draft"; source_node_id = "fork-parallel"; target_node_id = "draft-parallel"; condition_label = "draft" },
      @{ id = "parallel-edge-review"; source_node_id = "fork-parallel"; target_node_id = "review-parallel"; condition_label = "review" },
      @{ id = "parallel-edge-draft-join"; source_node_id = "draft-parallel"; target_node_id = "join-parallel"; condition_label = "" },
      @{ id = "parallel-edge-review-join"; source_node_id = "review-parallel"; target_node_id = "join-parallel"; condition_label = "" },
      @{ id = "parallel-edge-end"; source_node_id = "join-parallel"; target_node_id = "end-parallel"; condition_label = "" }
    )
  }
}

function New-HumanApprovalWorkflowDsl {
  return @{
    entry_node_id = "start-approval"
    nodes = @(
      @{ id = "start-approval"; type = "start"; config = @{} },
      @{ id = "draft-approval"; type = "llm.generate"; config = @{} },
      @{ id = "human-review"; type = "human.approval"; config = @{
        title = "Stack smoke approval"
        instructions = "Approve the generated draft to continue."
        assignee_roles = @("PLATFORM_ADMIN")
        timeout_seconds = 300
        timeout_action = "reject"
      } },
      @{ id = "approved-answer"; type = "llm.generate"; config = @{} },
      @{ id = "rejected-answer"; type = "llm.generate"; config = @{} },
      @{ id = "approved-end"; type = "end"; config = @{} },
      @{ id = "rejected-end"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "approval-edge-1"; source_node_id = "start-approval"; target_node_id = "draft-approval"; condition_label = "" },
      @{ id = "approval-edge-2"; source_node_id = "draft-approval"; target_node_id = "human-review"; condition_label = "" },
      @{ id = "approval-edge-approve"; source_node_id = "human-review"; target_node_id = "approved-answer"; condition_label = "approve" },
      @{ id = "approval-edge-reject"; source_node_id = "human-review"; target_node_id = "rejected-answer"; condition_label = "reject" },
      @{ id = "approval-edge-approved-end"; source_node_id = "approved-answer"; target_node_id = "approved-end"; condition_label = "" },
      @{ id = "approval-edge-rejected-end"; source_node_id = "rejected-answer"; target_node_id = "rejected-end"; condition_label = "" }
    )
  }
}

function New-DeniedHttpWorkflowDsl {
  return @{
    entry_node_id = "start-http"
    nodes = @(
      @{ id = "start-http"; type = "start"; config = @{} },
      @{ id = "http-denied"; type = "tool.http"; config = @{ url = "https://smoke-denied.invalid/v1"; method = "POST"; body = @{ text = '${text}' } } },
      @{ id = "answer-http"; type = "llm.generate"; config = @{} },
      @{ id = "end-http"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "http-edge-1"; source_node_id = "start-http"; target_node_id = "http-denied"; condition_label = "" },
      @{ id = "http-edge-2"; source_node_id = "http-denied"; target_node_id = "answer-http"; condition_label = "" },
      @{ id = "http-edge-3"; source_node_id = "answer-http"; target_node_id = "end-http"; condition_label = "" }
    )
  }
}

function New-SecretHttpWorkflowDsl {
  param([string]$SecretId)
  return @{
    entry_node_id = "start-secret"
    nodes = @(
      @{ id = "start-secret"; type = "start"; config = @{} },
      @{ id = "http-secret"; type = "tool.http"; config = @{ url = "http://control-plane:8099/api/v1/auth/me"; method = "GET"; auth = @{ secret_ref = $SecretId; header = "Authorization"; prefix = "Bearer " } } },
      @{ id = "answer-secret"; type = "llm.generate"; config = @{} },
      @{ id = "end-secret"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "secret-edge-1"; source_node_id = "start-secret"; target_node_id = "http-secret"; condition_label = "" },
      @{ id = "secret-edge-2"; source_node_id = "http-secret"; target_node_id = "answer-secret"; condition_label = "" },
      @{ id = "secret-edge-3"; source_node_id = "answer-secret"; target_node_id = "end-secret"; condition_label = "" }
    )
  }
}

$composePath = (Resolve-Path -LiteralPath $ComposeFile).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$composeJson = docker compose --env-file $envPath -f $composePath config --format json
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Compose configuration." }
$compose = $composeJson | ConvertFrom-Json

$controlEnv = $compose.services.'control-plane'.environment
$executionEnv = $compose.services.'execution-api'.environment
$controlUser = [string]$controlEnv.CONTROL_PLANE_ADMIN_USERNAME
$controlPassword = [string]$controlEnv.CONTROL_PLANE_ADMIN_PASSWORD
$controlTenant = [string]$controlEnv.CONTROL_PLANE_ADMIN_TENANT_ID
$executionUser = [string]$executionEnv.EXECUTION_ADMIN_USERNAME
$executionPassword = [string]$executionEnv.EXECUTION_ADMIN_PASSWORD
$internalToken = [string]$controlEnv.CONTROL_PLANE_INTERNAL_TOKEN

foreach ($required in @($controlUser, $controlPassword, $controlTenant, $executionUser, $executionPassword, $internalToken)) {
  Assert-True (-not [string]::IsNullOrWhiteSpace($required)) "Resolved Compose auth configuration is incomplete."
}

$controlBase = "http://127.0.0.1:8099"
$executionBase = "http://127.0.0.1:8091"
$chatBase = "http://127.0.0.1:8092"
$runSuffix = [guid]::NewGuid().ToString("N").Substring(0, 10)
$createdKbId = ""
$createdDocId = ""
$createdSessionId = ""
$createdWorkflowId = ""
$createdControlConversationId = ""
$createdRunId = ""
$createdSecretId = ""
$secretScenarioVerified = $false
$smokeSucceeded = $false

try {
  foreach ($health in @(
    "$controlBase/api/v1/system/health",
    "$executionBase/api/v1/system/health",
    "$chatBase/api/v1/system/health"
  )) {
    $result = Invoke-Json -Method GET -Uri $health
    Assert-True ($result.status -eq "ok") "Health check failed: $health"
  }
  $executionHealth = Invoke-Json -Method GET -Uri "$executionBase/api/v1/system/health"
  $embeddingMode = ([string]$executionHealth.embedding_mode).Trim().ToLowerInvariant()
  if ($embeddingMode -ne "local_only" -and -not $AllowExternalEmbeddingProvider) {
    throw "Stack smoke refuses external embedding mode '$embeddingMode'. Restart Execution API/Worker with EMBEDDING_MODE=local_only, or explicitly pass -AllowExternalEmbeddingProvider after approving provider usage and cost."
  }

  $controlLogin = Invoke-Json -Method POST -Uri "$controlBase/api/v1/auth/login" -Body @{
    tenantCode = $controlTenant
    username = $controlUser
    password = $controlPassword
  }
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$controlLogin.accessToken)) "Control Plane access token missing."
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$controlLogin.refreshToken)) "Control Plane refresh token missing."
  $controlHeaders = @{ Authorization = "Bearer $($controlLogin.accessToken)" }

  $me = Invoke-Json -Method GET -Uri "$controlBase/api/v1/auth/me" -Headers $controlHeaders
  Assert-True ($me.item.tenant_id -eq $controlTenant) "Control Plane tenant mismatch."

  $canvas = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/agent-canvas/validate" -Headers $controlHeaders -Body @{
    entry_node_id = "start-1"
    nodes = @(
      @{ id = "start-1"; type = "start"; config = @{} },
      @{ id = "llm-1"; type = "llm.generate"; config = @{} },
      @{ id = "end-1"; type = "end"; config = @{} }
    )
    edges = @(
      @{ id = "edge-1"; source_node_id = "start-1"; target_node_id = "llm-1"; condition_label = "" },
      @{ id = "edge-2"; source_node_id = "llm-1"; target_node_id = "end-1"; condition_label = "" }
    )
  }
  Assert-True ([bool]$canvas.valid) "Valid Canvas DSL was rejected."

  $workflowDsl = New-SmokeWorkflowDsl
  $workflow = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke workflow"
    dsl = $workflowDsl
    layout = @{}
  }
  $createdWorkflowId = [string]$workflow.item.workflow_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdWorkflowId)) "Created workflow id missing."
  Assert-True ([bool]$workflow.validation.valid) "Created workflow draft should be valid."
  $published = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$workflow.item.revision
  }
  Assert-True ($published.item.version_no -eq 1) "Workflow first published version should be 1."
  Assert-True ([string]$published.item.checksum -match '^[0-9a-f]{64}$') "Workflow version checksum missing or invalid."
  $versions = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/versions" -Headers $controlHeaders
  Assert-True (@($versions.items).Count -eq 1) "Published workflow version not visible."

  $conversation = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations" -Headers $controlHeaders -Body @{
    title = "smoke-run-$runSuffix"
  }
  $createdControlConversationId = [string]$conversation.item.conversation_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdControlConversationId)) "Created Control Plane conversation id missing."

  $messageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "stack smoke run" }
    workflow_id = $createdWorkflowId
    workflow_version_no = 1
  }
  $createdRunId = [string]$messageRun.run.run_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdRunId)) "Created run id missing."
  Assert-True ($messageRun.run.status -eq "PENDING") "New run should be PENDING."
  Assert-True ($messageRun.run.workflow_checksum -eq [string]$published.item.checksum) "Run did not snapshot the published workflow checksum."

  Assert-HttpStatus -Method POST -Uri "$executionBase/api/v1/internal/control-runs/dispatch" -Headers @{
    "X-Internal-Token" = "invalid-$runSuffix"
  } -Body @{
    run_id = $createdRunId
    tenant_id = $controlTenant
    actor_id = [string]$controlLogin.actorId
    conversation_id = $createdControlConversationId
    status = "PENDING"
    workflow_id = $createdWorkflowId
    workflow_version_id = [string]$published.item.version_id
    workflow_version_no = 1
    workflow_checksum = [string]$published.item.checksum
    workflow_dsl = $workflowDsl
    workflow_layout = @{}
    input_payload = @{ text = "stack smoke run" }
  } -ExpectedStatus 401

  $invalidInternalEvent = @{
    event_id = "smoke-$runSuffix-invalid"
    tenant_id = $controlTenant
    source_service = "stack-smoke"
    event_type = "RUN_PROGRESS"
    status = "RUNNING"
    stage = "llm.generate"
    progress = 50
    payload = @{}
  }
  Assert-HttpStatus -Method POST -Uri "$controlBase/api/v1/internal/runs/$createdRunId/events" -Headers @{
    "X-Internal-Token" = "invalid-$runSuffix"
  } -Body $invalidInternalEvent -ExpectedStatus 401

  $internalHeaders = @{ "X-Internal-Token" = $internalToken }
  $run = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $run = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$createdRunId" -Headers $controlHeaders
    if ($run.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($run.item.status -eq "SUCCEEDED") "Automatically dispatched mock run did not reach SUCCEEDED."
  Assert-True ($run.item.progress -eq 100) "Successful run progress should be 100."
  Assert-True ($run.item.output_payload.provider -eq "local-mock") "Run was not executed by the local mock provider."
  Assert-True ([string]$run.item.output_payload.answer -match 'stack smoke run') "Mock run output was not persisted."
  Assert-True ([double]$run.item.metrics.cost -eq 0) "Mock run must have zero provider cost."
  Assert-True ((@($run.item.metrics.executed_node_ids) -join ',') -eq 'start-1,condition-1,llm-true,end-true') "Workflow did not follow the selected true branch."
  Assert-True ([int]$run.item.metrics.provider_call_count -eq 1) "Selected graph path should execute exactly one provider node."
  Assert-True ($null -ne $run.item.started_at -and $null -ne $run.item.finished_at) "Run lifecycle timestamps are incomplete."
  $dispatches = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/run-dispatches" -Headers $controlHeaders
  $successDispatch = @($dispatches.items | Where-Object { $_.run_id -eq $createdRunId }) | Select-Object -First 1
  Assert-True ($null -ne $successDispatch -and $successDispatch.status -eq "PUBLISHED") "Successful Run outbox was not published."
  Assert-True ([int]$successDispatch.attempt_count -ge 1) "Published Run outbox did not record a dispatch attempt."
  $messages = Invoke-Json -Method GET -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders
  Assert-True (@($messages.items).Count -eq 2) "Successful run should create one assistant message."
  Assert-True ($messages.items[0].role -eq "USER" -and $messages.items[1].role -eq "ASSISTANT") "Conversation message roles are out of order."
  $history = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$createdRunId/events/history" -Headers $controlHeaders
  Assert-True (@($history.items).Count -eq 11) "Run history should contain lifecycle and four node start/completion pairs."
  Assert-True ((($history.items | ForEach-Object { $_.sequence_no }) -join ',') -eq ((1..11) -join ',')) "Run event sequence is not contiguous."
  Assert-True ($history.items[0].event_type -eq 'RUN_CREATED' -and $history.items[1].event_type -eq 'RUN_STARTED' -and $history.items[-1].event_type -eq 'RUN_SUCCEEDED') "Run lifecycle event boundaries are incomplete."
  Assert-True (@($history.items | Where-Object { $_.event_type -eq 'NODE_STARTED' }).Count -eq 4) "Per-node start events are incomplete."
  Assert-True (@($history.items | Where-Object { $_.event_type -eq 'NODE_COMPLETED' }).Count -eq 4) "Per-node completion events are incomplete."
  $conditionEvent = @($history.items | Where-Object { $_.event_type -eq 'NODE_COMPLETED' -and $_.payload.node_id -eq 'condition-1' }) | Select-Object -First 1
  Assert-True ($null -ne $conditionEvent -and $conditionEvent.payload.details.selected_label -eq 'true') "Condition branch audit detail is missing."
  $adminRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$createdRunId" -Headers $controlHeaders
  $adminEvents = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$createdRunId/events" -Headers $controlHeaders
  $adminNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$createdRunId/nodes" -Headers $controlHeaders
  $adminRuns = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs?status=SUCCEEDED" -Headers $controlHeaders
  Assert-True ($adminRun.item.run_id -eq $createdRunId -and @($adminEvents.items).Count -eq 11 -and @($adminNodes.items).Count -eq 4 -and @($adminRuns.items | Where-Object { $_.run_id -eq $createdRunId }).Count -eq 1) "Admin run trace APIs are incomplete."
  Assert-True (@($adminNodes.items | Where-Object { $_.status -ne 'SUCCEEDED' }).Count -eq 0) "Successful node execution records are incomplete."
  $startedDuplicate = Invoke-Json -Method POST -Uri "$controlBase/api/v1/internal/runs/$createdRunId/events" -Headers $internalHeaders -Body @{
    event_id = "execution:${createdRunId}:attempt:1:started"
    tenant_id = $controlTenant
    source_service = "stack-smoke"
    event_type = "RUN_STARTED"
    status = "RUNNING"
    stage = "workflow.start"
    progress = 10
    payload = @{}
  }
  Assert-True ([bool]$startedDuplicate.duplicate) "Execution start event should remain idempotent after completion."
  $sseOutput = & curl.exe --silent --max-time 2 -H "Authorization: Bearer $($controlLogin.accessToken)" "$controlBase/api/v1/runs/$createdRunId/events" 2>$null
  Assert-True (($sseOutput -join "`n") -match 'event:RUN_CREATED') "SSE replay did not include RUN_CREATED."
  Assert-True (($sseOutput -join "`n") -match 'event:RUN_SUCCEEDED') "SSE replay did not include RUN_SUCCEEDED."

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $cancelDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke cancellation workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = New-SmokeWorkflowDsl -LlmConfig @{ mock_delay_ms = 4000 }
    layout = @{}
  }
  $cancelVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$cancelDraft.item.revision
  }
  Assert-True ($cancelVersion.item.version_no -eq 2) "Cancellation workflow version should be 2."
  $cancelMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "cancel this mock run" }
    workflow_id = $createdWorkflowId
    workflow_version_no = 2
  }
  $cancelRunId = [string]$cancelMessageRun.run.run_id
  $cancelRun = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $cancelRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$cancelRunId" -Headers $controlHeaders
    if ($cancelRun.item.status -eq "RUNNING") { break }
    if ($cancelRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 100
  }
  Assert-True ($cancelRun.item.status -eq "RUNNING") "Delayed mock run was not claimed before cancellation."
  $competingLease = Invoke-Json -Method POST -Uri "$controlBase/api/v1/internal/runs/$cancelRunId/lease/claim" -Headers $internalHeaders -Body @{
    tenant_id = $controlTenant
    worker_id = "stack-smoke-competing-worker"
    lease_token = [guid]::NewGuid().ToString()
  }
  Assert-True (-not [bool]$competingLease.item.acquired -and $competingLease.item.reason -eq "LEASE_HELD") "Concurrent worker unexpectedly stole a live execution lease."
  Invoke-Json -Method POST -Uri "$controlBase/api/v1/runs/$cancelRunId/cancel" -Headers $controlHeaders -Body @{} | Out-Null
  Start-Sleep -Milliseconds 4200
  $cancelledRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$cancelRunId" -Headers $controlHeaders
  Assert-True ($cancelledRun.item.status -eq "CANCELLED") "Cancelled run accepted a late worker result."
  $cancelHistory = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$cancelRunId/events/history" -Headers $controlHeaders
  Assert-True (@($cancelHistory.items | Where-Object { $_.event_type -eq "RUN_CANCELLED" }).Count -eq 1) "Cancellation event was not persisted."
  Assert-True (@($cancelHistory.items | Where-Object { $_.event_type -eq "RUN_SUCCEEDED" }).Count -eq 0) "Cancelled run must not receive a success event."
  $terminalLease = Invoke-Json -Method POST -Uri "$controlBase/api/v1/internal/runs/$cancelRunId/lease/claim" -Headers $internalHeaders -Body @{
    tenant_id = $controlTenant
    worker_id = "stack-smoke-terminal-worker"
    lease_token = [guid]::NewGuid().ToString()
  }
  Assert-True (-not [bool]$terminalLease.item.acquired -and $terminalLease.item.reason -eq "RUN_TERMINAL") "Terminal Run unexpectedly granted an execution lease."

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $failureDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke failure workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = New-SmokeWorkflowDsl -LlmConfig @{
      mock_fail = $true
      retry = @{ max_attempts = 2; initial_delay_ms = 0; max_delay_ms = 0; retry_on = "any" }
    }
    layout = @{}
  }
  $failureVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$failureDraft.item.revision
  }
  Assert-True ($failureVersion.item.version_no -eq 3) "Failure workflow version should be 3."
  $failureMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "fail this mock run" }
    workflow_id = $createdWorkflowId
    workflow_version_no = 3
  }
  $failureRunId = [string]$failureMessageRun.run.run_id
  $failedRun = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $failedRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$failureRunId" -Headers $controlHeaders
    if ($failedRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($failedRun.item.status -eq "FAILED") "Mock workflow failure did not reach FAILED."
  Assert-True ($failedRun.item.error_code -eq "WORKFLOW_EXECUTION_FAILED") "Failed run error code was not persisted."
  Assert-True ([string]$failedRun.item.error_message -match 'mock workflow failure requested') "Failed run error detail was not persisted."
  $failedNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$failureRunId/nodes" -Headers $controlHeaders
  $failedNode = @($failedNodes.items | Where-Object { $_.status -eq 'FAILED' -and $_.node_id -eq 'llm-true' }) | Select-Object -First 1
  Assert-True ($null -ne $failedNode) "Failed node execution record was not persisted."
  Assert-True (@($failedNode.attempts).Count -eq 2) "Failed node retry attempts were not persisted."
  Assert-True ($failedNode.attempts[0].status -eq 'FAILED' -and [bool]$failedNode.attempts[0].retryable) "First failed node attempt should be retryable."
  Assert-True ($failedNode.attempts[1].status -eq 'FAILED' -and -not [bool]$failedNode.attempts[1].retryable) "Final failed node attempt should be terminal."
  $finalDispatches = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/run-dispatches" -Headers $controlHeaders
  $smokeDispatches = @($finalDispatches.items | Where-Object { $_.run_id -in @($createdRunId, $cancelRunId, $failureRunId) })
  Assert-True ($smokeDispatches.Count -eq 3) "Not every smoke Run created a durable outbox item."
  Assert-True (@($smokeDispatches | Where-Object { $_.status -ne "PUBLISHED" }).Count -eq 0) "A smoke Run outbox was not published."
  $negativeMessages = Invoke-Json -Method GET -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders
  Assert-True (@($negativeMessages.items).Count -eq 4) "Cancelled/failed runs must not create assistant messages."

  $executionLogin = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/auth/login" -Body @{
    username = $executionUser
    password = $executionPassword
  }
  Assert-True (-not [string]::IsNullOrWhiteSpace([string]$executionLogin.access_token)) "Execution Plane access token missing."
  $executionHeaders = @{
    Authorization = "Bearer $($executionLogin.access_token)"
    "X-Tenant-Id" = $controlTenant
    "X-Actor-Id" = $controlUser
    "X-Trace-Id" = "smoke-$runSuffix"
    "X-Source-Service" = "stack-smoke"
  }

  $kb = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/kbs" -Headers $executionHeaders -Body @{
    name = "smoke-$runSuffix"
  }
  $createdKbId = Get-EntityId $kb.item
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdKbId)) "Created KB id missing."
  $kbList = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/kbs" -Headers $executionHeaders
  Assert-True (@($kbList.items | Where-Object { (Get-EntityId $_) -eq $createdKbId }).Count -eq 1) "Created KB not visible in list."

  $evidenceMarker = "smoke-evidence-$runSuffix"
  $ingest = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/kbs/$createdKbId/documents/ingest" -Headers $executionHeaders -Body @{
    filename = "smoke-evidence.txt"
    content = "Agent Powflow verification marker $evidenceMarker proves local retrieval works without external providers."
    chunk_size = 100
    chunk_overlap = 0
    auto_summary = $false
  }
  $createdDocId = [string]$ingest.doc_id
  $ingestTaskId = [string]$ingest.task_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdDocId) -and -not [string]::IsNullOrWhiteSpace($ingestTaskId)) "Document ingest ids are incomplete."
  $ingestRecord = $null
  for ($attempt = 0; $attempt -lt 120; $attempt++) {
    $taskList = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/tasks?kb_id=$createdKbId&limit=100" -Headers $executionHeaders
    $ingestRecord = @($taskList.items | Where-Object { $_.task_id -eq $ingestTaskId }) | Select-Object -First 1
    if ($null -ne $ingestRecord -and $ingestRecord.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($null -ne $ingestRecord -and $ingestRecord.status -eq "SUCCEEDED") "Local embedding document ingest did not succeed."
  $otherTenantHeaders = @{}
  foreach ($key in $executionHeaders.Keys) { $otherTenantHeaders[$key] = $executionHeaders[$key] }
  $otherTenantHeaders["X-Tenant-Id"] = "smoke-other-$runSuffix"
  $hiddenTask = Invoke-Json -Method GET -Uri "$executionBase/api/v1/tasks/$ingestTaskId" -Headers $otherTenantHeaders
  Assert-True ($hiddenTask.state -eq "UNKNOWN" -and $null -eq $hiddenTask.record) "Task status leaked across tenants."
  Assert-HttpStatus -Method GET -Uri "$executionBase/api/v1/admin/tasks/$ingestTaskId/events" -Headers $otherTenantHeaders -ExpectedStatus 404
  Assert-HttpStatus -Method POST -Uri "$executionBase/api/v1/admin/tasks/$ingestTaskId/cancel" -Headers $otherTenantHeaders -Body @{} -ExpectedStatus 404
  $retrieval = Invoke-Json -Method POST -Uri "$executionBase/api/v1/retrieval/search" -Headers $executionHeaders -Body @{
    kb_id = $createdKbId
    query = $evidenceMarker
    top_k = 3
  }
  Assert-True (@($retrieval.evidence).Count -gt 0) "Local retrieval returned no evidence."
  Assert-True ((@($retrieval.evidence | ForEach-Object { [string]$_.text }) -join "`n") -match [regex]::Escape($evidenceMarker)) "Retrieved evidence does not contain the smoke marker."

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $retrievalDsl = New-RetrievalWorkflowDsl -KbId $createdKbId
  $retrievalDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke retrieval workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = $retrievalDsl
    layout = @{}
  }
  $retrievalVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$retrievalDraft.item.revision
  }
  Assert-True ($retrievalVersion.item.version_no -eq 4) "Retrieval workflow version should be 4."
  $retrievalMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = $evidenceMarker }
    workflow_id = $createdWorkflowId
    workflow_version_no = 4
  }
  $retrievalRunId = [string]$retrievalMessageRun.run.run_id
  $retrievalWorkflowRun = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $retrievalWorkflowRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$retrievalRunId" -Headers $controlHeaders
    if ($retrievalWorkflowRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($retrievalWorkflowRun.item.status -eq "SUCCEEDED") "retrieval.search workflow did not succeed."
  Assert-True ([int]$retrievalWorkflowRun.item.metrics.retrieval_citation_count -gt 0) "retrieval.search workflow did not persist citations."
  Assert-True (@($retrievalWorkflowRun.item.citations | Where-Object { $_.chunk_id -and $_.kb_id -eq $createdKbId }).Count -gt 0) "retrieval.search citations are missing identifiers."
  $retrievalHistory = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$retrievalRunId/events/history" -Headers $controlHeaders
  $retrievalNodeEvent = @($retrievalHistory.items | Where-Object { $_.event_type -eq 'NODE_COMPLETED' -and $_.payload.node_id -eq 'search-1' }) | Select-Object -First 1
  Assert-True ($null -ne $retrievalNodeEvent -and [int]$retrievalNodeEvent.payload.details.result_count -gt 0) "retrieval.search node trace is incomplete."
  $retrievalNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$retrievalRunId/nodes" -Headers $controlHeaders
  Assert-True (@($retrievalNodes.items).Count -eq 4 -and @($retrievalNodes.items | Where-Object { $_.node_id -eq 'search-1' -and [int]$_.details.result_count -gt 0 }).Count -eq 1) "retrieval.search node execution record is incomplete."

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $httpDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke denied HTTP workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = New-DeniedHttpWorkflowDsl
    layout = @{}
  }
  $httpVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$httpDraft.item.revision
  }
  Assert-True ($httpVersion.item.version_no -eq 5) "HTTP tool workflow version should be 5."
  $httpMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "deny this request" }
    workflow_id = $createdWorkflowId
    workflow_version_no = 5
  }
  $httpRunId = [string]$httpMessageRun.run.run_id
  $httpRun = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $httpRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$httpRunId" -Headers $controlHeaders
    if ($httpRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($httpRun.item.status -eq "FAILED") "Default-deny tool.http workflow did not fail closed."
  Assert-True ([string]$httpRun.item.error_message -match 'not allowlisted') "tool.http failure did not report its allowlist boundary."

  $secretList = Invoke-Json -Method GET -Uri "$executionBase/api/v1/admin/workflow-secrets" -Headers $executionHeaders
  if ([bool]$secretList.vault_available) {
    $secret = Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/workflow-secrets" -Headers $executionHeaders -Body @{
      name = "smoke-secret-$runSuffix"
      value = [string]$controlLogin.accessToken
      description = "stack smoke control token"
    }
    $createdSecretId = [string]$secret.item.secret_id
    Assert-True (-not [string]::IsNullOrWhiteSpace($createdSecretId)) "Created Workflow Secret id missing."
    Assert-True ($null -eq $secret.item.value -and $null -eq $secret.item.ciphertext) "Workflow Secret API exposed plaintext or ciphertext."
    $rotated = Invoke-Json -Method PUT -Uri "$executionBase/api/v1/admin/workflow-secrets/$createdSecretId" -Headers $executionHeaders -Body @{
      value = [string]$controlLogin.accessToken
      description = "stack smoke rotated token"
    }
    Assert-True ([int]$rotated.item.version -eq 2 -and $null -eq $rotated.item.value) "Workflow Secret rotation metadata is incomplete."

    $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
    $secretDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
      name = "smoke-$runSuffix"
      description = "stack smoke authenticated HTTP workflow"
      expected_revision = [long]$workflowCurrent.item.revision
      dsl = New-SecretHttpWorkflowDsl -SecretId $createdSecretId
      layout = @{}
    }
    $secretVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
      expected_revision = [long]$secretDraft.item.revision
    }
    Assert-True ($secretVersion.item.version_no -eq 6) "Authenticated HTTP workflow version should be 6."
    $secretMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
      content = @{ text = "authenticated HTTP smoke" }
      workflow_id = $createdWorkflowId
      workflow_version_no = 6
    }
    $secretRunId = [string]$secretMessageRun.run.run_id
    $secretRun = $null
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
      $secretRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$secretRunId" -Headers $controlHeaders
      if ($secretRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
      Start-Sleep -Milliseconds 250
    }
    Assert-True ($secretRun.item.status -eq "SUCCEEDED" -and [int]$secretRun.item.metrics.tool_call_count -eq 1) "Secret-authenticated tool.http workflow did not succeed."
    $secretNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$secretRunId/nodes" -Headers $controlHeaders
    Assert-True (@($secretNodes.items | Where-Object { $_.node_id -eq 'http-secret' -and $_.status -eq 'SUCCEEDED' }).Count -eq 1) "Authenticated HTTP node execution was not persisted."

    Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/workflow-secrets/$createdSecretId/disable" -Headers $executionHeaders -Body @{} | Out-Null
    $disabledRunResponse = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
      content = @{ text = "disabled secret smoke" }
      workflow_id = $createdWorkflowId
      workflow_version_no = 6
    }
    $disabledRunId = [string]$disabledRunResponse.run.run_id
    $disabledRun = $null
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
      $disabledRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$disabledRunId" -Headers $controlHeaders
      if ($disabledRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
      Start-Sleep -Milliseconds 250
    }
    Assert-True ($disabledRun.item.status -eq "FAILED" -and [string]$disabledRun.item.error_message -match 'disabled') "Disabled Workflow Secret did not fail closed."
    Invoke-Json -Method DELETE -Uri "$executionBase/api/v1/admin/workflow-secrets/$createdSecretId" -Headers $executionHeaders | Out-Null
    $createdSecretId = ""
    $secretScenarioVerified = $true
  }

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $parallelDsl = New-ParallelWorkflowDsl
  $parallelDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "smoke-$runSuffix"
    description = "stack smoke parallel fan-out/join workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = $parallelDsl
    layout = @{}
  }
  Assert-True ([bool]$parallelDraft.validation.valid) "Structured parallel workflow draft was rejected."
  $parallelVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$parallelDraft.item.revision
  }
  $parallelVersionNo = [int]$parallelVersion.item.version_no
  Assert-True ($parallelVersionNo -eq ([int]$workflowCurrent.item.latest_version_no + 1)) "Parallel workflow version number is not monotonic."
  $parallelMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "parallel stack smoke" }
    workflow_id = $createdWorkflowId
    workflow_version_no = $parallelVersionNo
  }
  $parallelRunId = [string]$parallelMessageRun.run.run_id
  $parallelRun = $null
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $parallelRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$parallelRunId" -Headers $controlHeaders
    if ($parallelRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($parallelRun.item.status -eq "SUCCEEDED") "Parallel fan-out/join workflow did not succeed."
  Assert-True ([int]$parallelRun.item.metrics.parallel_fork_count -eq 1 -and [int]$parallelRun.item.metrics.parallel_branch_count -eq 2) "Parallel metrics are incomplete."
  Assert-True ([int]$parallelRun.item.metrics.provider_call_count -eq 2) "Parallel workflow did not execute both provider branches."
  Assert-True ((@($parallelRun.item.metrics.executed_node_ids) -join ',') -eq 'start-parallel,fork-parallel,draft-parallel,review-parallel,join-parallel,end-parallel') "Parallel executed-node audit order is not deterministic."
  Assert-True ([string]$parallelRun.item.output_payload.answer -match '\[mock:draft-parallel\]') "parallel.join did not select the declared answer branch."
  Assert-True ([string]$parallelRun.item.output_payload.branches.review.answer -match '\[mock:review-parallel\]') "Parallel branch output namespace is incomplete."
  $parallelNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$parallelRunId/nodes" -Headers $controlHeaders
  Assert-True (@($parallelNodes.items).Count -eq 6 -and @($parallelNodes.items | Where-Object { $_.status -ne 'SUCCEEDED' }).Count -eq 0) "Parallel node execution records are incomplete."
  $parallelJoin = @($parallelNodes.items | Where-Object { $_.node_id -eq 'join-parallel' }) | Select-Object -First 1
  Assert-True ($parallelJoin.details.answer_branch -eq 'draft' -and @($parallelJoin.details.completed_branches).Count -eq 2) "Parallel join audit details are incomplete."

  $workflowCurrent = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
  $approvalDsl = New-HumanApprovalWorkflowDsl
  $approvalDraft = Invoke-Json -Method PUT -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/draft" -Headers $controlHeaders -Body @{
    name = "Stack Smoke Workflow"
    description = "stack smoke durable human approval workflow"
    expected_revision = [long]$workflowCurrent.item.revision
    dsl = $approvalDsl
    layout = @{}
  }
  Assert-True ([bool]$approvalDraft.validation.valid) "Durable human approval workflow draft was rejected."
  $approvalVersion = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/publish" -Headers $controlHeaders -Body @{
    expected_revision = [long]$approvalDraft.item.revision
  }
  $approvalVersionNo = [int]$approvalVersion.item.version_no
  $approvalMessageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$createdControlConversationId/messages" -Headers $controlHeaders -Body @{
    content = @{ text = "durable approval stack smoke" }
    workflow_id = $createdWorkflowId
    workflow_version_no = $approvalVersionNo
  }
  $approvalRunId = [string]$approvalMessageRun.run.run_id
  $waitingRun = $null
  for ($i = 0; $i -lt 80; $i++) {
    $waitingRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$approvalRunId" -Headers $controlHeaders
    if ($waitingRun.item.status -in @("WAITING_APPROVAL", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($waitingRun.item.status -eq "WAITING_APPROVAL") "Human approval workflow did not persist a waiting state."
  $pendingApprovals = Invoke-Json -Method GET -Uri "$controlBase/api/v1/approvals?status=PENDING" -Headers $controlHeaders
  $approvalItem = @($pendingApprovals.items | Where-Object { $_.run_id -eq $approvalRunId }) | Select-Object -First 1
  Assert-True ($null -ne $approvalItem -and $approvalItem.node_id -eq "human-review") "Pending approval was not visible in the tenant inbox."
  Assert-True (@($approvalItem.assignee_roles) -contains "PLATFORM_ADMIN") "Approval assignee roles were not persisted."
  $approvalStats = Invoke-Json -Method GET -Uri "$controlBase/api/v1/approvals/stats?due_within_seconds=900" -Headers $controlHeaders
  Assert-True ([int]$approvalStats.item.pending_count -ge 1) "Approval SLA statistics did not include the pending approval."
  Assert-True ([int]$approvalStats.item.due_soon_count -ge 1 -and [int]$approvalStats.item.overdue_count -eq 0) "Approval due-soon statistics are incorrect."
  $approvalAlertConfig = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/approval-alerts/config" -Headers $controlHeaders
  Assert-True (-not [bool]$approvalAlertConfig.item.destination_configured) "Approval alert smoke expects the default disabled webhook destination."
  Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/approval-alerts/evaluate" -Headers $controlHeaders -Body @{} | Out-Null
  $openApprovalAlerts = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/approval-alerts?status=OPEN" -Headers $controlHeaders
  $approvalAlert = @($openApprovalAlerts.items | Where-Object { $_.approval_id -eq $approvalItem.approval_id }) | Select-Object -First 1
  Assert-True ($null -ne $approvalAlert -and $approvalAlert.alert_type -eq "DUE_SOON") "Due-soon approval alert was not created idempotently."
  Assert-True ($approvalAlert.delivery.status -eq "DISABLED" -and [int]$approvalAlert.delivery.attempt_count -eq 0) "Unconfigured approval webhook did not remain safely disabled."
  $acknowledgedApprovalAlert = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/approval-alerts/$($approvalAlert.alert_id)/acknowledge" -Headers $controlHeaders -Body @{}
  Assert-True ($acknowledgedApprovalAlert.item.status -eq "ACKNOWLEDGED") "Approval alert acknowledgement was not persisted."
  $waitingNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$approvalRunId/nodes" -Headers $controlHeaders
  $waitingNode = @($waitingNodes.items | Where-Object { $_.node_id -eq "human-review" }) | Select-Object -First 1
  Assert-True ($waitingNode.status -eq "WAITING_APPROVAL") "Human node did not expose its waiting execution status."

  $decision = Invoke-Json -Method POST -Uri "$controlBase/api/v1/approvals/$($approvalItem.approval_id)/decision" -Headers $controlHeaders -Body @{
    decision = "approve"
    reason = "stack smoke approved"
    revision = [long]$approvalItem.revision
  }
  Assert-True ($decision.item.decision -eq "approve" -and -not [bool]$decision.duplicate) "Approval decision was not accepted."
  $duplicateDecision = Invoke-Json -Method POST -Uri "$controlBase/api/v1/approvals/$($approvalItem.approval_id)/decision" -Headers $controlHeaders -Body @{
    decision = "approve"
    reason = "stack smoke approved"
    revision = [long]$approvalItem.revision
  }
  Assert-True ([bool]$duplicateDecision.duplicate) "Repeated approval decision was not idempotent."

  $approvalRun = $null
  for ($i = 0; $i -lt 80; $i++) {
    $approvalRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$approvalRunId" -Headers $controlHeaders
    if ($approvalRun.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($approvalRun.item.status -eq "SUCCEEDED") "Approved workflow did not resume to success."
  Assert-True ((@($approvalRun.item.metrics.executed_node_ids) -join ',') -eq 'start-approval,draft-approval,human-review,approved-answer,approved-end') "Approval resume did not preserve deterministic executed-node history."
  Assert-True ([int]$approvalRun.item.metrics.provider_call_count -eq 2) "Approval resume did not preserve pre-suspension provider metrics."
  $approvalEvents = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$approvalRunId/events" -Headers $controlHeaders
  Assert-True (@($approvalEvents.items | Where-Object { $_.event_type -eq "APPROVAL_REQUESTED" }).Count -eq 1) "Approval request audit event is missing."
  Assert-True (@($approvalEvents.items | Where-Object { $_.event_type -eq "APPROVAL_DECIDED" }).Count -eq 1) "Approval decision audit event is missing."
  $approvalNodes = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/runs/$approvalRunId/nodes" -Headers $controlHeaders
  $completedApprovalNode = @($approvalNodes.items | Where-Object { $_.node_id -eq "human-review" }) | Select-Object -First 1
  Assert-True ($completedApprovalNode.status -eq "SUCCEEDED" -and $completedApprovalNode.details.approval_decision -eq "approve") "Approval node did not complete with decision audit details."
  $approvalStatsAfterDecision = Invoke-Json -Method GET -Uri "$controlBase/api/v1/approvals/stats?due_within_seconds=900" -Headers $controlHeaders
  Assert-True ([int]$approvalStatsAfterDecision.item.decided_count_24h -ge 1) "Approval decision was not reflected in 24-hour SLA statistics."
  $approvalTrend = Invoke-Json -Method GET -Uri "$controlBase/api/v1/approvals/trend?days=7" -Headers $controlHeaders
  $approvalTrendTotals = @($approvalTrend.item.items | Measure-Object -Property decided_count -Sum).Sum
  Assert-True ($approvalTrend.item.timezone -eq "UTC" -and @($approvalTrend.item.items).Count -eq 7) "Approval trend did not return a complete UTC reporting window."
  Assert-True ([int]$approvalTrendTotals -ge 1) "Approval audit fact was not reflected in the retained trend."
  $resolvedApprovalAlerts = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/approval-alerts?status=RESOLVED" -Headers $controlHeaders
  $resolvedApprovalAlert = @($resolvedApprovalAlerts.items | Where-Object { $_.alert_id -eq $approvalAlert.alert_id }) | Select-Object -First 1
  Assert-True ($null -ne $resolvedApprovalAlert -and $null -ne $resolvedApprovalAlert.resolved_at) "Terminal approval did not resolve its queue-risk alert."

  $chatHeaders = @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Tenant-Id" = $controlTenant
    "X-Actor-Id" = [string]$controlLogin.actorId
    "X-Trace-Id" = "smoke-chat-$runSuffix"
    "X-Source-Service" = "stack-smoke"
  }
  $authContext = Invoke-Json -Method GET -Uri "$chatBase/api/v1/chat/auth/context" -Headers $chatHeaders
  Assert-True ($authContext.item.tenant_id -eq $controlTenant) "Chat auth tenant mismatch."

  Assert-HttpStatus -Method GET -Uri "$chatBase/api/v1/chat/config" -Headers @{
    Authorization = "Bearer $($controlLogin.refreshToken)"
    "X-Tenant-Id" = $controlTenant
  } -Body $null -ExpectedStatus 401

  Assert-HttpStatus -Method GET -Uri "$chatBase/api/v1/chat/config" -Headers @{
    Authorization = "Bearer $($controlLogin.accessToken)"
    "X-Tenant-Id" = "different-tenant"
  } -Body $null -ExpectedStatus 403

  $session = Invoke-Json -Method POST -Uri "$chatBase/api/v1/chat/sessions" -Headers $chatHeaders -Body @{
    title = "smoke-$runSuffix"
    kb_id = $createdKbId
  }
  $createdSessionId = [string]$session.item.session_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($createdSessionId)) "Created Chat session id missing."
  $sessions = Invoke-Json -Method GET -Uri "$chatBase/api/v1/chat/sessions?limit=20" -Headers $chatHeaders
  Assert-True (@($sessions.items | Where-Object { $_.session_id -eq $createdSessionId }).Count -eq 1) "Created Chat session not visible in list."

  $chatBody = @{
    message = "Explain the knowledge-base marker $evidenceMarker"
    use_kb = $true
    kb_id = $createdKbId
    top_k = 3
  } | ConvertTo-Json -Depth 10 -Compress
  $chatResponse = Invoke-WebRequest -UseBasicParsing -Method POST -Uri "$chatBase/api/v1/chat/sessions/$createdSessionId/messages/stream" -Headers $chatHeaders -ContentType "application/json" -Body $chatBody -TimeoutSec 45
  $chatStream = [string]$chatResponse.Content
  Assert-True ($chatStream -match 'event: done') "Chat SSE stream did not complete."
  Assert-True ($chatStream -notmatch 'event: error') "Chat SSE stream returned an error event."
  Assert-True ($chatStream -match [regex]::Escape($evidenceMarker)) "Chat mock response was not grounded in the retrieved smoke evidence."
  $chatMessages = Invoke-Json -Method GET -Uri "$chatBase/api/v1/chat/sessions/$createdSessionId/messages?limit=20" -Headers $chatHeaders
  Assert-True (@($chatMessages.items).Count -eq 2) "Chat turn should persist one user and one assistant message."

  $smokeSucceeded = $true
  $secretMetric = if ($secretScenarioVerified) { 1 } else { 0 }
  Write-Output "STACK_SMOKE_OK health=3 auth=2 tenant_guards=2 execution_task_tenant_guards=3 canvas=1 workflow_lifecycle=1 graph_branch=1 parallel_fanout_join=1 durable_human_approval=1 approval_idempotency=1 approval_sla_metrics=1 approval_retained_trend=1 approval_alert_outbox=1 node_events=8 admin_run_trace=4 node_execution_records=1 node_retry_attempts=2 run_success=1 run_cancel=1 run_failure=1 transactional_outbox=3 execution_lease=3 execution_dispatch=1 mock_provider=1 document_ingest=1 retrieval=1 workflow_retrieval=1 workflow_http_default_deny=1 workflow_secret=$secretMetric chat_stream=1 cleanup=1"
} catch {
  throw "Smoke request failed at ${script:LastSmokeRequest}: $($_.Exception.Message)"
} finally {
  $cleanupErrors = New-Object System.Collections.Generic.List[string]
  if (-not [string]::IsNullOrWhiteSpace($createdSecretId) -and $null -ne $executionHeaders) {
    try {
      try { Invoke-Json -Method POST -Uri "$executionBase/api/v1/admin/workflow-secrets/$createdSecretId/disable" -Headers $executionHeaders -Body @{} | Out-Null } catch {}
      Invoke-Json -Method DELETE -Uri "$executionBase/api/v1/admin/workflow-secrets/$createdSecretId" -Headers $executionHeaders | Out-Null
    } catch { $cleanupErrors.Add("Workflow Secret ${createdSecretId}: $($_.Exception.Message)") }
  }
  if (-not [string]::IsNullOrWhiteSpace($createdSessionId) -and $null -ne $chatHeaders) {
    try {
      $deletedSession = Invoke-Json -Method DELETE -Uri "$chatBase/api/v1/chat/sessions/$createdSessionId" -Headers $chatHeaders
      if (-not [bool]$deletedSession.deleted) { throw "Chat delete response was not successful." }
    } catch { $cleanupErrors.Add("Chat session ${createdSessionId}: $($_.Exception.Message)") }
  }
  if (-not [string]::IsNullOrWhiteSpace($createdKbId) -and $null -ne $executionHeaders) {
    try {
      $deletedKb = Invoke-Json -Method DELETE -Uri "$executionBase/api/v1/admin/kbs/$createdKbId" -Headers $executionHeaders
      if ($deletedKb.status -ne "ok") { throw "KB delete response was not successful." }
    } catch { $cleanupErrors.Add("KB ${createdKbId}: $($_.Exception.Message)") }
  }
  if (-not [string]::IsNullOrWhiteSpace($createdControlConversationId) -and $null -ne $controlHeaders) {
    try {
      $deletedConversation = Invoke-Json -Method DELETE -Uri "$controlBase/api/v1/conversations/$createdControlConversationId" -Headers $controlHeaders
      if ($deletedConversation.status -ne "ok") { throw "Control Plane conversation delete response was not successful." }
    } catch { $cleanupErrors.Add("Control Plane conversation ${createdControlConversationId}: $($_.Exception.Message)") }
  }
  if (-not [string]::IsNullOrWhiteSpace($createdWorkflowId) -and $null -ne $controlHeaders) {
    try {
      $currentWorkflow = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
      if ($currentWorkflow.item.status -ne "ARCHIVED") {
        Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId/archive" -Headers $controlHeaders -Body @{
          expected_revision = [long]$currentWorkflow.item.revision
        } | Out-Null
      }
      $deletedWorkflow = Invoke-Json -Method DELETE -Uri "$controlBase/api/v1/admin/workflows/$createdWorkflowId" -Headers $controlHeaders
      if ($deletedWorkflow.status -ne "ok") { throw "Workflow delete response was not successful." }
    } catch {
      $cleanupErrors.Add("Workflow ${createdWorkflowId}: $($_.Exception.Message)")
    }
  }
  if ($cleanupErrors.Count -gt 0) {
    $cleanupMessage = "Smoke cleanup failed: " + ($cleanupErrors -join "; ")
    if ($smokeSucceeded) { throw $cleanupMessage }
    Write-Warning $cleanupMessage
  }
}
