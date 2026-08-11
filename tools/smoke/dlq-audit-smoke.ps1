param(
  [string]$ComposeFile = "infra/compose/docker-compose.stack.yml",
  [string]$EnvFile = "config/service-ports.env"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Invoke-Json([string]$Method,[string]$Uri,[hashtable]$Headers=@{},[object]$Body=$null) {
  $script:LastRequest="$Method $Uri"; $p=@{Method=$Method;Uri=$Uri;Headers=$Headers;TimeoutSec=15}
  if($null-ne $Body){$p.ContentType="application/json";$p.Body=$Body|ConvertTo-Json -Depth 30 -Compress}
  Invoke-RestMethod @p
}
$composePath=(Resolve-Path -LiteralPath $ComposeFile).Path
$envPath=(Resolve-Path -LiteralPath $EnvFile).Path
$compose=(docker compose --env-file $envPath -f $composePath config --format json)|ConvertFrom-Json
$env=$compose.services.'control-plane'.environment
$base="http://127.0.0.1:8099";$suffix=[guid]::NewGuid().ToString("N").Substring(0,10)
$workflowId="";$conversationId="";$runId="";$failedTaskId="dlq-smoke-$suffix";$deadLetterId="";$headers=$null;$succeeded=$false
try {
  $login=Invoke-Json POST "$base/api/v1/auth/login" @{} @{tenantCode=[string]$env.CONTROL_PLANE_ADMIN_TENANT_ID;username=[string]$env.CONTROL_PLANE_ADMIN_USERNAME;password=[string]$env.CONTROL_PLANE_ADMIN_PASSWORD}
  $headers=@{Authorization="Bearer $($login.accessToken)"}
  $dsl=@{entry_node_id="start-1";nodes=@(@{id="start-1";type="start";config=@{}},@{id="llm-1";type="llm.generate";config=@{mock_delay_ms=5000}},@{id="end-1";type="end";config=@{}});edges=@(@{id="edge-1";source_node_id="start-1";target_node_id="llm-1";condition_label=""},@{id="edge-2";source_node_id="llm-1";target_node_id="end-1";condition_label=""})}
  $wf=Invoke-Json POST "$base/api/v1/admin/workflows" $headers @{name="dlq-smoke-$suffix";description="DLQ audit replay smoke";dsl=$dsl;layout=@{}}
  $workflowId=[string]$wf.item.workflow_id
  $version=Invoke-Json POST "$base/api/v1/admin/workflows/$workflowId/publish" $headers @{expected_revision=[long]$wf.item.revision}
  $conversation=Invoke-Json POST "$base/api/v1/conversations" $headers @{title="dlq-smoke-$suffix"};$conversationId=[string]$conversation.item.conversation_id
  $created=Invoke-Json POST "$base/api/v1/conversations/$conversationId/messages" $headers @{content=@{text="dlq smoke"};workflow_id=$workflowId;workflow_version_no=[int]$version.item.version_no};$runId=[string]$created.run.run_id
  $dlq=@{tenant_id=[string]$env.CONTROL_PLANE_ADMIN_TENANT_ID;run_id=$runId;failed_task_id=$failedTaskId;error="simulated retry exhaustion";source_queue="control_run_queue";payload=@{run_id=$runId;tenant_id=[string]$env.CONTROL_PLANE_ADMIN_TENANT_ID}}|ConvertTo-Json -Depth 10 -Compress
  $dlqBase64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dlq))
  docker exec -e "APF_DLQ_SMOKE_PAYLOAD=$dlqBase64" agentpowflow-execution-api python -c "import base64,json,os;from workers.app.celery_app import celery_app;p=json.loads(base64.b64decode(os.environ['APF_DLQ_SMOKE_PAYLOAD']));celery_app.send_task('agentpowflow.control_run.dead_letter',args=[p],queue='control_run_dead_letter',exchange='control_run_dead_letter',routing_key='control_run.dead',ignore_result=True)" | Out-Null
  if($LASTEXITCODE-ne 0){throw "unable to publish DLQ message"}
  $dead=$null
  for($i=0;$i-lt 40;$i++){ $list=Invoke-Json GET "$base/api/v1/admin/run-dead-letters?status=OPEN" $headers;$dead=@($list.items|Where-Object{$_.failed_task_id-eq $failedTaskId})|Select-Object -First 1;if($null-ne $dead){break};Start-Sleep -Milliseconds 250 }
  Assert-True ($null-ne $dead) "RabbitMQ DLQ message was not persisted";$deadLetterId=[string]$dead.dead_letter_id
  $replay=Invoke-Json POST "$base/api/v1/admin/run-dead-letters/$deadLetterId/replay" $headers @{}
  Assert-True ($replay.item.status-eq "REPLAYING") "DLQ replay did not enter REPLAYING"
  $run=$null
  for($i=0;$i-lt 80;$i++){ $run=Invoke-Json GET "$base/api/v1/runs/$runId" $headers;if($run.item.status-in @("SUCCEEDED","FAILED","CANCELLED")){break};Start-Sleep -Milliseconds 250 }
  Assert-True ($run.item.status-eq "SUCCEEDED") "replayed run did not succeed"
  $detail=Invoke-Json GET "$base/api/v1/admin/run-dead-letters/$deadLetterId" $headers
  Assert-True ($detail.item.status-eq "REPLAYED") "terminal callback did not resolve DLQ replay"
  $audit=Invoke-Json GET "$base/api/v1/admin/run-dead-letters/$deadLetterId/audit" $headers
  $actions=@($audit.items|ForEach-Object{$_.action});Assert-True ($actions.Count-eq 3) "expected three audit actions";Assert-True ($actions[0]-eq "INGESTED" -and $actions[1]-eq "REPLAY_REQUESTED" -and $actions[2]-eq "REPLAY_COMPLETED") "unexpected audit order"
  $succeeded=$true
  Write-Output "DLQ_AUDIT_SMOKE_OK rabbit_ingest=1 admin_query=1 replay=1 audit=3 cleanup=1"
} catch { throw "DLQ audit smoke failed at ${script:LastRequest}: $($_.Exception.Message)" }
finally {
  if($runId-and $headers){try{$current=Invoke-Json GET "$base/api/v1/runs/$runId" $headers;if($current.item.status-notin @("SUCCEEDED","FAILED","CANCELLED")){Invoke-Json POST "$base/api/v1/runs/$runId/cancel" $headers @{}|Out-Null}}catch{}}
  if($conversationId-and $headers){try{Invoke-Json DELETE "$base/api/v1/conversations/$conversationId" $headers|Out-Null}catch{if($succeeded){throw}}}
  if($workflowId-and $headers){try{$current=Invoke-Json GET "$base/api/v1/admin/workflows/$workflowId" $headers;Invoke-Json POST "$base/api/v1/admin/workflows/$workflowId/archive" $headers @{expected_revision=[long]$current.item.revision}|Out-Null;Invoke-Json DELETE "$base/api/v1/admin/workflows/$workflowId" $headers|Out-Null}catch{if($succeeded){throw}}}
  if($deadLetterId-match '^[0-9a-f-]{36}$'){docker exec agentpowflow-postgres psql -U apf -d agentpowflow -v ON_ERROR_STOP=1 -c "DELETE FROM run_dead_letters WHERE dead_letter_id='$deadLetterId';"|Out-Null;if($LASTEXITCODE-ne 0 -and $succeeded){throw "DLQ smoke cleanup failed"}}
}
