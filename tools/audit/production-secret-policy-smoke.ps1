$ErrorActionPreference = "Stop"

$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$validator = Join-Path $workspaceRoot "infra/scripts/validate-production-secrets.ps1"
$tempFile = [System.IO.Path]::GetTempFileName()
$resolvedTemp = (Resolve-Path -LiteralPath $tempFile).Path
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
if (-not $resolvedTemp.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Generated test file escaped the operating-system temp directory."
}

function New-TestSecret {
  param([int]$Bytes = 32, [switch]$KeepPadding)
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
  $value = [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
  if (-not $KeepPadding) { $value = $value.TrimEnd('=') }
  return $value
}

function Invoke-ValidatorChild {
  $executable = (Get-Process -Id $PID).Path
  $previousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $output = & $executable -NoProfile -ExecutionPolicy Bypass -File $validator -EnvFile $resolvedTemp 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return [pscustomobject]@{ ExitCode = $exitCode; Output = (($output | ForEach-Object { [string]$_ }) -join "`n") }
}

try {
  $adminToken = New-TestSecret
  $workflowPrimary = New-TestSecret -KeepPadding
  $workflowPrevious = New-TestSecret -KeepPadding
  $jwtPrimary = New-TestSecret
  $jwtPrevious = New-TestSecret
  $controlJwtPrimary = New-TestSecret
  $controlJwtPrevious = New-TestSecret
  $jwtExpiry = [DateTimeOffset]::UtcNow.AddHours(1).ToString("o")
  $lines = @(
    "APP_ENV=production",
    "EXECUTION_ENV=production",
    "POSTGRES_USER=apfprod",
    "POSTGRES_PASSWORD=$(New-TestSecret)",
    "MONGO_ROOT_USERNAME=apfmongo",
    "MONGO_ROOT_PASSWORD=$(New-TestSecret)",
    "RABBITMQ_USERNAME=apfrabbit",
    "RABBITMQ_PASSWORD=$(New-TestSecret)",
    "MINIO_ROOT_ACCESS_KEY=$(New-TestSecret -Bytes 18)",
    "MINIO_ROOT_SECRET_KEY=$(New-TestSecret)",
    "MINIO_APP_ACCESS_KEY=$(New-TestSecret -Bytes 18)",
    "MINIO_APP_SECRET_KEY=$(New-TestSecret)",
    "MINIO_GOVERNANCE_ACCESS_KEY=$(New-TestSecret -Bytes 18)",
    "MINIO_GOVERNANCE_SECRET_KEY=$(New-TestSecret)",
    "ALERTMANAGER_WEBHOOK_TOKEN=$(New-TestSecret)",
    "MINIO_DOCUMENT_BUCKET=agent-powflow-documents",
    "MINIO_SECURE=true",
    "MINIO_CA_CERT_PATH=/etc/agent-powflow/minio-ca.crt",
    "MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS=90",
    "MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS=2",
    "OBJECT_STORAGE_TENANT_QUOTA_BYTES=10737418240",
    "MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=true",
    "QDRANT_API_KEY=$(New-TestSecret)",
    "EXECUTION_ADMIN_TOKEN=$adminToken",
    "EXECUTION_ADMIN_PASSWORD=$(New-TestSecret)",
    "EXECUTION_JWT_SECRET=$jwtPrimary",
    "EXECUTION_JWT_KEY_ID=jwt-current",
    "EXECUTION_JWT_PREVIOUS_KEYS=jwt-old:$jwtPrevious",
    "EXECUTION_JWT_PREVIOUS_KEYS_EXPIRES_AT=$jwtExpiry",
    "EXECUTION_JWT_LEGACY_ACCEPT_UNTIL=$jwtExpiry",
    "CONTROL_PLANE_JWT_SECRET=$controlJwtPrimary",
    "CONTROL_PLANE_JWT_KEY_ID=jwt-control-current",
    "CONTROL_PLANE_JWT_PREVIOUS_KEYS=jwt-control-old:$controlJwtPrevious",
    "CONTROL_PLANE_JWT_PREVIOUS_KEYS_EXPIRES_AT=$jwtExpiry",
    "CONTROL_PLANE_JWT_LEGACY_ACCEPT_UNTIL=$jwtExpiry",
    "CONTROL_PLANE_INTERNAL_TOKEN=$(New-TestSecret)",
    "WORKFLOW_SECRET_MASTER_KEY=$workflowPrimary",
    "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS=$workflowPrevious"
  )
  Set-Content -LiteralPath $resolvedTemp -Value $lines -Encoding UTF8
  $valid = Invoke-ValidatorChild
  if ($valid.ExitCode -ne 0 -or $valid.Output -notmatch "PRODUCTION_SECRET_VALIDATION_OK") {
    throw "Valid production Secret fixture was rejected: $($valid.Output)"
  }

  $previousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $composeArguments = @(
      "compose",
      "--env-file", (Join-Path $workspaceRoot "config/service-ports.env"),
      "--env-file", $resolvedTemp,
      "-f", (Join-Path $workspaceRoot "infra/compose/docker-compose.stack.yml"),
      "-f", (Join-Path $workspaceRoot "infra/compose/docker-compose.production.yml"),
      "-f", (Join-Path $workspaceRoot "infra/compose/docker-compose.observability.yml"),
      "-f", (Join-Path $workspaceRoot "infra/compose/docker-compose.minio-tls.yml"),
      "config", "--quiet"
    )
    $composeOutput = & docker @composeArguments 2>&1
    $composeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  if ($composeExitCode -ne 0) {
    throw "Production Compose override did not parse; output was redacted by the policy smoke."
  }

  $weak = $lines | ForEach-Object {
    if ($_ -like "EXECUTION_JWT_SECRET=*") { "EXECUTION_JWT_SECRET=change-me-in-production" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $weak -Encoding UTF8
  $weakResult = Invoke-ValidatorChild
  if ($weakResult.ExitCode -eq 0 -or $weakResult.Output -notmatch "WEAK_OR_PLACEHOLDER") {
    throw "Weak production Secret fixture was not rejected."
  }

  $reused = $lines | ForEach-Object {
    if ($_ -like "CONTROL_PLANE_INTERNAL_TOKEN=*") { "CONTROL_PLANE_INTERNAL_TOKEN=$adminToken" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $reused -Encoding UTF8
  $reusedResult = Invoke-ValidatorChild
  if ($reusedResult.ExitCode -eq 0 -or $reusedResult.Output -notmatch "REUSED_SECRET") {
    throw "Reused production Secret fixture was not rejected."
  }

  $reusedRotationKey = $lines | ForEach-Object {
    if ($_ -like "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS=*") { "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS=$workflowPrimary" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $reusedRotationKey -Encoding UTF8
  $reusedRotationResult = Invoke-ValidatorChild
  if ($reusedRotationResult.ExitCode -eq 0 -or $reusedRotationResult.Output -notmatch "REUSED_ROTATION_KEY") {
    throw "Reused Workflow rotation key fixture was not rejected."
  }

  $reusedJwt = $lines | ForEach-Object {
    if ($_ -like "EXECUTION_JWT_PREVIOUS_KEYS=*") { "EXECUTION_JWT_PREVIOUS_KEYS=jwt-old:$jwtPrimary" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $reusedJwt -Encoding UTF8
  $reusedJwtResult = Invoke-ValidatorChild
  if ($reusedJwtResult.ExitCode -eq 0 -or $reusedJwtResult.Output -notmatch "REUSED_JWT_SECRET") {
    throw "Reused JWT Secret fixture was not rejected."
  }

  $crossPlaneJwtReuse = $lines | ForEach-Object {
    if ($_ -like "CONTROL_PLANE_JWT_SECRET=*") { "CONTROL_PLANE_JWT_SECRET=$jwtPrimary" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $crossPlaneJwtReuse -Encoding UTF8
  $crossPlaneJwtReuseResult = Invoke-ValidatorChild
  if ($crossPlaneJwtReuseResult.ExitCode -eq 0 -or $crossPlaneJwtReuseResult.Output -notmatch "REUSED_SECRET") {
    throw "Cross-plane JWT Secret reuse was not rejected."
  }

  $staleLegacyDeadline = $lines | ForEach-Object {
    if ($_ -like "EXECUTION_JWT_LEGACY_ACCEPT_UNTIL=*") { "EXECUTION_JWT_LEGACY_ACCEPT_UNTIL=2020-01-01T00:00:00Z" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $staleLegacyDeadline -Encoding UTF8
  $staleLegacyResult = Invoke-ValidatorChild
  if ($staleLegacyResult.ExitCode -eq 0 -or $staleLegacyResult.Output -notmatch "INVALID_EXPIRY") {
    throw "Stale legacy JWT compatibility deadline was not rejected."
  }

  $invalidCapacity = $lines | ForEach-Object {
    if ($_ -like "OBJECT_STORAGE_TENANT_QUOTA_BYTES=*") { "OBJECT_STORAGE_TENANT_QUOTA_BYTES=0" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $invalidCapacity -Encoding UTF8
  $invalidCapacityResult = Invoke-ValidatorChild
  if ($invalidCapacityResult.ExitCode -eq 0 -or $invalidCapacityResult.Output -notmatch "INVALID_CAPACITY") {
    throw "Disabled production object storage quota was not rejected."
  }

  $disabledPhysicalAdmission = $lines | ForEach-Object {
    if ($_ -like "MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=*") { "MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES=false" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $disabledPhysicalAdmission -Encoding UTF8
  $disabledPhysicalAdmissionResult = Invoke-ValidatorChild
  if ($disabledPhysicalAdmissionResult.ExitCode -eq 0 -or $disabledPhysicalAdmissionResult.Output -notmatch "WRITE_ADMISSION_DISABLED") {
    throw "Disabled production physical capacity admission was not rejected."
  }

  $invalidRetention = $lines | ForEach-Object {
    if ($_ -like "MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS=*") { "MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS=0" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $invalidRetention -Encoding UTF8
  $invalidRetentionResult = Invoke-ValidatorChild
  if ($invalidRetentionResult.ExitCode -eq 0 -or $invalidRetentionResult.Output -notmatch "INVALID_GOVERNANCE_CONFIG") {
    throw "Unsafe production MinIO retention was not rejected."
  }

  $disabledTls = $lines | ForEach-Object {
    if ($_ -like "MINIO_SECURE=*") { "MINIO_SECURE=false" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $disabledTls -Encoding UTF8
  $disabledTlsResult = Invoke-ValidatorChild
  if ($disabledTlsResult.ExitCode -eq 0 -or $disabledTlsResult.Output -notmatch "TLS_DISABLED") {
    throw "Disabled production MinIO TLS was not rejected."
  }

  $rootAccessKey = (($lines | Where-Object { $_ -like "MINIO_ROOT_ACCESS_KEY=*" }) -split '=', 2)[1]
  $reusedMinioIdentity = $lines | ForEach-Object {
    if ($_ -like "MINIO_APP_ACCESS_KEY=*") { "MINIO_APP_ACCESS_KEY=$rootAccessKey" } else { $_ }
  }
  Set-Content -LiteralPath $resolvedTemp -Value $reusedMinioIdentity -Encoding UTF8
  $reusedMinioIdentityResult = Invoke-ValidatorChild
  if ($reusedMinioIdentityResult.ExitCode -eq 0 -or $reusedMinioIdentityResult.Output -notmatch "REUSED_IDENTITY") {
    throw "Reused production MinIO identity was not rejected."
  }

  Write-Output "PRODUCTION_SECRET_POLICY_SMOKE_OK valid=1 weak_rejected=1 reuse_rejected=1 rotation_reuse_rejected=1 jwt_reuse_rejected=1 cross_plane_jwt_reuse_rejected=1 stale_legacy_deadline_rejected=1 capacity_rejected=1 physical_admission_rejected=1 retention_rejected=1 tls_rejected=1 minio_identity_reuse_rejected=1 compose=1"
} finally {
  if ((Test-Path -LiteralPath $resolvedTemp) -and $resolvedTemp.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolvedTemp -Force
  }
}
