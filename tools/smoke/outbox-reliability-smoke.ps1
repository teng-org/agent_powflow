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
    [object]$Body = $null
  )
  $script:LastRequest = "$Method $Uri"
  $params = @{ Method = $Method; Uri = $Uri; Headers = $Headers; TimeoutSec = 15 }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = $Body | ConvertTo-Json -Depth 20 -Compress
  }
  Invoke-RestMethod @params
}

$composePath = (Resolve-Path -LiteralPath $ComposeFile).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$composeJson = docker compose --env-file $envPath -f $composePath config --format json
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Compose configuration." }
$compose = $composeJson | ConvertFrom-Json
$controlEnv = $compose.services.'control-plane'.environment
$controlUser = [string]$controlEnv.CONTROL_PLANE_ADMIN_USERNAME
$controlPassword = [string]$controlEnv.CONTROL_PLANE_ADMIN_PASSWORD
$controlTenant = [string]$controlEnv.CONTROL_PLANE_ADMIN_TENANT_ID
$controlBase = "http://127.0.0.1:8099"
$executionHealth = "http://127.0.0.1:8091/api/v1/system/health"
$suffix = [guid]::NewGuid().ToString("N").Substring(0, 10)
$workflowId = ""
$conversationId = ""
$runId = ""
$executionStopped = $false
$succeeded = $false

try {
  $login = Invoke-Json -Method POST -Uri "$controlBase/api/v1/auth/login" -Body @{
    tenantCode = $controlTenant
    username = $controlUser
    password = $controlPassword
  }
  $headers = @{ Authorization = "Bearer $($login.accessToken)" }
  $dsl = @{
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
  $workflow = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows" -Headers $headers -Body @{
    name = "outbox-smoke-$suffix"
    description = "dispatch dead-letter and replay smoke"
    dsl = $dsl
    layout = @{}
  }
  $workflowId = [string]$workflow.item.workflow_id
  $version = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$workflowId/publish" -Headers $headers -Body @{
    expected_revision = [long]$workflow.item.revision
  }
  $conversation = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations" -Headers $headers -Body @{
    title = "outbox-smoke-$suffix"
  }
  $conversationId = [string]$conversation.item.conversation_id

  docker compose --env-file $envPath -f $composePath stop execution-api | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to stop execution-api for reliability smoke." }
  $executionStopped = $true

  $messageRun = Invoke-Json -Method POST -Uri "$controlBase/api/v1/conversations/$conversationId/messages" -Headers $headers -Body @{
    content = @{ text = "verify outbox dead letter replay" }
    workflow_id = $workflowId
    workflow_version_no = [int]$version.item.version_no
  }
  $runId = [string]$messageRun.run.run_id
  Assert-True (-not [string]::IsNullOrWhiteSpace($runId)) "Run id missing."

  # The dispatcher can claim immediately after the transaction commits. Wait
  # for that in-flight HTTP attempt to finish before preparing the final retry,
  # otherwise its ownership check correctly refuses to overwrite our setup.
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    $currentStatus = docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc "select status||'|'||attempt_count from run_dispatch_outbox where run_id='$runId';"
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect the exact smoke outbox item." }
    $snapshot = (($currentStatus -join "").Trim())
    if ($snapshot -match '^(RETRY|DEAD_LETTER)\|[1-9][0-9]*$') { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($snapshot -match '^(RETRY|DEAD_LETTER)\|[1-9][0-9]*$') "Initial dispatch attempt did not finish."

  $updated = docker exec agentpowflow-postgres psql -U apf -d agentpowflow -Atc "update run_dispatch_outbox set attempt_count=max_attempts-1, available_at=now(), status='RETRY', locked_by='', locked_until=null where run_id='$runId' returning outbox_id;"
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($updated -join ""))) {
    throw "Unable to prepare the exact smoke outbox item for its final retry."
  }

  $dead = $null
  for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $items = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/run-dispatches?status=DEAD_LETTER" -Headers $headers
    $dead = @($items.items | Where-Object { $_.run_id -eq $runId }) | Select-Object -First 1
    if ($null -ne $dead) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($null -ne $dead) "Dispatch did not enter DEAD_LETTER after retry exhaustion."
  Assert-True ([int]$dead.attempt_count -eq [int]$dead.max_attempts) "Dead-letter attempt count is inconsistent."
  $deadRun = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$runId" -Headers $headers
  Assert-True ($deadRun.item.status -eq "PENDING") "Dispatch dead letter must not violate Run terminal-state invariants."
  Assert-True ($deadRun.item.stage -eq "DISPATCH_DEAD_LETTER") "Run did not expose its dispatch dead-letter stage."
  Assert-True ($deadRun.item.error_code -eq "DISPATCH_RETRY_EXHAUSTED") "Run dead-letter error code missing."

  docker compose --env-file $envPath -f $composePath start execution-api | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Unable to restart execution-api." }
  $executionStopped = $false
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    try {
      $health = Invoke-Json -Method GET -Uri $executionHealth
      if ($health.status -eq "ok") { break }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($health.status -eq "ok") "execution-api did not recover."

  $replayed = Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/run-dispatches/$($dead.outbox_id)/replay" -Headers $headers -Body @{}
  Assert-True ($replayed.item.status -eq "PENDING" -and [int]$replayed.item.attempt_count -eq 0) "Dead-letter replay did not reset the outbox item."

  $run = $null
  for ($attempt = 0; $attempt -lt 100; $attempt++) {
    $run = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$runId" -Headers $headers
    if ($run.item.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Milliseconds 250
  }
  Assert-True ($run.item.status -eq "SUCCEEDED") "Replayed dispatch did not complete successfully."
  $published = $null
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $allOutbox = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/run-dispatches" -Headers $headers
    $published = @($allOutbox.items | Where-Object { $_.run_id -eq $runId }) | Select-Object -First 1
    if ($null -ne $published -and $published.status -eq "PUBLISHED") { break }
    Start-Sleep -Milliseconds 100
  }
  Assert-True ($published.status -eq "PUBLISHED" -and [int]$published.attempt_count -ge 1) "Replayed outbox item was not published after recovery."

  $succeeded = $true
  Write-Output "OUTBOX_RELIABILITY_SMOKE_OK retry_exhaustion=1 dead_letter=1 admin_replay=1 recovered_run=1 cleanup=1"
} catch {
  throw "Reliability smoke failed at ${script:LastRequest}: $($_.Exception.Message)"
} finally {
  if ($executionStopped) {
    try { docker compose --env-file $envPath -f $composePath start execution-api | Out-Null } catch {}
  }
  if (-not [string]::IsNullOrWhiteSpace($runId) -and $null -ne $headers) {
    try {
      $current = Invoke-Json -Method GET -Uri "$controlBase/api/v1/runs/$runId" -Headers $headers
      if ($current.item.status -notin @("SUCCEEDED", "FAILED", "CANCELLED")) {
        Invoke-Json -Method POST -Uri "$controlBase/api/v1/runs/$runId/cancel" -Headers $headers -Body @{} | Out-Null
      }
    } catch {}
  }
  if (-not [string]::IsNullOrWhiteSpace($conversationId) -and $null -ne $headers) {
    try { Invoke-Json -Method DELETE -Uri "$controlBase/api/v1/conversations/$conversationId" -Headers $headers | Out-Null } catch {
      if ($succeeded) { throw "Reliability smoke conversation cleanup failed: $($_.Exception.Message)" }
    }
  }
  if (-not [string]::IsNullOrWhiteSpace($workflowId) -and $null -ne $headers) {
    try {
      $currentWorkflow = Invoke-Json -Method GET -Uri "$controlBase/api/v1/admin/workflows/$workflowId" -Headers $headers
      Invoke-Json -Method POST -Uri "$controlBase/api/v1/admin/workflows/$workflowId/archive" -Headers $headers -Body @{
        expected_revision = [long]$currentWorkflow.item.revision
      } | Out-Null
      Invoke-Json -Method DELETE -Uri "$controlBase/api/v1/admin/workflows/$workflowId" -Headers $headers | Out-Null
    } catch {
      if ($succeeded) { throw "Reliability smoke workflow cleanup failed: $($_.Exception.Message)" }
    }
  }
}
