param(
  [string]$PrometheusUrl = "http://127.0.0.1:9090",
  [string]$AlertmanagerUrl = "http://127.0.0.1:9093"
)

$ErrorActionPreference = "Stop"

function Assert-SafeBaseUrl {
  param([Parameter(Mandatory = $true)][string]$Value, [Parameter(Mandatory = $true)][string]$Name)
  $uri = [Uri]$Value
  if ($uri.Scheme -notin @("http", "https") -or -not $uri.Host -or -not [string]::IsNullOrWhiteSpace($uri.UserInfo)) {
    throw "$Name must be an http(s) URL without embedded credentials"
  }
  return $Value.TrimEnd('/')
}

$prometheus = Assert-SafeBaseUrl -Value $PrometheusUrl -Name "PrometheusUrl"
$alertmanager = Assert-SafeBaseUrl -Value $AlertmanagerUrl -Name "AlertmanagerUrl"
$smokeId = "minio-observability-$([Guid]::NewGuid().ToString('N'))"
$alertApi = "$alertmanager/api/v2/alerts"
$posted = $false
$resolved = $false

function Find-SmokeAlert {
  $alerts = @(Invoke-RestMethod -Method Get -Uri $alertApi)
  return @($alerts | Where-Object { $_.labels.smoke_id -eq $smokeId })
}

function Get-WebhookNotificationCounters {
  $metrics = (Invoke-WebRequest -UseBasicParsing -Uri "$alertmanager/metrics").Content
  $totalMatch = [regex]::Match($metrics, '(?m)^alertmanager_notifications_total\{integration="webhook"\}\s+([0-9.eE+-]+)\s*$')
  if (-not $totalMatch.Success) { throw "Alertmanager webhook notification counter is missing" }
  $failedMatches = [regex]::Matches($metrics, '(?m)^alertmanager_notifications_failed_total\{integration="webhook",reason="[^"]+"\}\s+([0-9.eE+-]+)\s*$')
  $failed = 0.0
  foreach ($match in $failedMatches) { $failed += [double]$match.Groups[1].Value }
  return @{ total = [double]$totalMatch.Groups[1].Value; failed = $failed }
}

function Wait-WebhookNotification {
  param([Parameter(Mandatory = $true)][double]$AfterTotal, [int]$TimeoutSeconds = 45)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $counters = Get-WebhookNotificationCounters
    if ($counters.failed -gt $script:baselineFailed) {
      throw "Alertmanager webhook delivery failed"
    }
    if ($counters.total -gt $AfterTotal) { return $counters }
    Start-Sleep -Seconds 1
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "Alertmanager did not deliver the webhook within ${TimeoutSeconds}s"
}

try {
  $baseline = Get-WebhookNotificationCounters
  $script:baselineFailed = $baseline.failed

  $targets = Invoke-RestMethod -Method Get -Uri "$prometheus/api/v1/targets"
  $minioTargets = @($targets.data.activeTargets | Where-Object { $_.labels.job -eq "minio-cluster" })
  if ($minioTargets.Count -ne 1 -or $minioTargets[0].health -ne "up") {
    throw "MinIO Prometheus target is not uniquely healthy"
  }

  $capacity = Invoke-RestMethod -Method Get -Uri "$prometheus/api/v1/query?query=minio_cluster_capacity_usable_total_bytes"
  $capacityRows = @($capacity.data.result)
  if ($capacity.status -ne "success" -or $capacityRows.Count -lt 1 -or [double]$capacityRows[0].value[1] -le 0) {
    throw "MinIO usable capacity metric is missing or invalid"
  }

  $rules = Invoke-RestMethod -Method Get -Uri "$prometheus/api/v1/rules"
  $requiredRules = @(
    "AgentPowflowMinioMetricsAbsent",
    "AgentPowflowMinioDriveOffline",
    "AgentPowflowMinioNodeOffline",
    "AgentPowflowMinioUsableCapacityLow",
    "AgentPowflowMinioUsableCapacityCritical"
  )
  $loadedRuleNames = @($rules.data.groups.rules | ForEach-Object { [string]$_.name })
  foreach ($rule in $requiredRules) {
    if ($loadedRuleNames -notcontains $rule) { throw "Required alert rule is not loaded: $rule" }
  }

  $alertmanagers = Invoke-RestMethod -Method Get -Uri "$prometheus/api/v1/alertmanagers"
  if (@($alertmanagers.data.activeAlertmanagers).Count -lt 1) {
    throw "Prometheus has no active Alertmanager target"
  }

  $now = [DateTimeOffset]::UtcNow
  $alertObject = @{
      labels = @{
        alertname = "AgentPowflowMinioObservabilitySmoke"
        component = "minio"
        severity = "warning"
        smoke_id = $smokeId
      }
      annotations = @{
        summary = "Agent Powflow MinIO observability smoke"
        description = "Synthetic alert used only to verify Alertmanager ingestion and cleanup."
      }
      startsAt = $now.ToString("o")
      endsAt = $now.AddMinutes(5).ToString("o")
      generatorURL = "$prometheus/graph"
  }
  $payload = "[" + ($alertObject | ConvertTo-Json -Depth 8) + "]"
  Invoke-RestMethod -Method Post -Uri $alertApi -ContentType "application/json" -Body $payload | Out-Null
  $posted = $true

  $found = @()
  for ($attempt = 0; $attempt -lt 10; $attempt++) {
    $found = @(Find-SmokeAlert)
    if ($found.Count -eq 1) { break }
    Start-Sleep -Milliseconds 500
  }
  if ($found.Count -ne 1) { throw "Synthetic MinIO alert did not appear in Alertmanager" }

  $firingDelivery = Wait-WebhookNotification -AfterTotal $baseline.total

  $resolvedAt = [DateTimeOffset]::UtcNow.AddSeconds(-1)
  $resolveObject = @{
      labels = @{
        alertname = "AgentPowflowMinioObservabilitySmoke"
        component = "minio"
        severity = "warning"
        smoke_id = $smokeId
      }
      annotations = @{
        summary = "Agent Powflow MinIO observability smoke"
        description = "Synthetic alert resolved by smoke cleanup."
      }
      startsAt = $resolvedAt.AddMinutes(-1).ToString("o")
      endsAt = $resolvedAt.ToString("o")
      generatorURL = "$prometheus/graph"
  }
  $resolvePayload = "[" + ($resolveObject | ConvertTo-Json -Depth 8) + "]"
  Invoke-RestMethod -Method Post -Uri $alertApi -ContentType "application/json" -Body $resolvePayload | Out-Null
  Wait-WebhookNotification -AfterTotal $firingDelivery.total -TimeoutSeconds 60 | Out-Null
  $resolved = $true

  Write-Output "MINIO_OBSERVABILITY_SMOKE_OK target_up=1 capacity_metric=1 rules=5 alertmanager_target=1 synthetic_alert=1 firing_webhook=1 resolved_webhook=1 smoke_id=$smokeId"
} finally {
  if ($posted -and -not $resolved) {
    $resolvedAt = [DateTimeOffset]::UtcNow.AddSeconds(-1)
    $resolveObject = @{
        labels = @{
          alertname = "AgentPowflowMinioObservabilitySmoke"
          component = "minio"
          severity = "warning"
          smoke_id = $smokeId
        }
        annotations = @{
          summary = "Agent Powflow MinIO observability smoke"
          description = "Synthetic alert resolved by smoke cleanup."
        }
        startsAt = $resolvedAt.AddMinutes(-1).ToString("o")
        endsAt = $resolvedAt.ToString("o")
        generatorURL = "$prometheus/graph"
    }
    $resolvePayload = "[" + ($resolveObject | ConvertTo-Json -Depth 8) + "]"
    try {
      Invoke-RestMethod -Method Post -Uri $alertApi -ContentType "application/json" -Body $resolvePayload | Out-Null
    } catch {
      Write-Warning "Synthetic alert cleanup request failed"
    }
  }
}
