param(
  [Parameter(Mandatory = $true)][string]$EnvFile
)

$ErrorActionPreference = "Stop"

if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
  throw "Production env file not found: $EnvFile"
}

$values = @{}
$locations = @{}
$findings = New-Object System.Collections.Generic.List[object]
$lineNumber = 0
foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
  $lineNumber++
  $trimmed = $line.Trim()
  if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
  if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
    $findings.Add([pscustomobject]@{ Key = "line:$lineNumber"; Code = "INVALID_LINE"; Message = "expected KEY=value without leading whitespace" })
    continue
  }
  $key = $Matches[1]
  $value = $Matches[2]
  if ($values.ContainsKey($key)) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "DUPLICATE_KEY"; Message = "also declared at line $($locations[$key])" })
    continue
  }
  $values[$key] = $value
  $locations[$key] = $lineNumber
}

$required = [ordered]@{
  APP_ENV = 10
  EXECUTION_ENV = 10
  POSTGRES_USER = 3
  POSTGRES_PASSWORD = 20
  MONGO_ROOT_USERNAME = 3
  MONGO_ROOT_PASSWORD = 20
  RABBITMQ_USERNAME = 3
  RABBITMQ_PASSWORD = 20
  MINIO_ROOT_ACCESS_KEY = 12
  MINIO_ROOT_SECRET_KEY = 32
  MINIO_APP_ACCESS_KEY = 12
  MINIO_APP_SECRET_KEY = 32
  MINIO_GOVERNANCE_ACCESS_KEY = 12
  MINIO_GOVERNANCE_SECRET_KEY = 32
  ALERTMANAGER_WEBHOOK_TOKEN = 32
  QDRANT_API_KEY = 32
  EXECUTION_ADMIN_TOKEN = 32
  EXECUTION_ADMIN_PASSWORD = 20
  EXECUTION_JWT_SECRET = 32
  EXECUTION_JWT_KEY_ID = 3
  CONTROL_PLANE_JWT_SECRET = 32
  CONTROL_PLANE_JWT_KEY_ID = 3
  CONTROL_PLANE_INTERNAL_TOKEN = 32
  WORKFLOW_SECRET_MASTER_KEY = 43
}
$secretKeys = @(
  "POSTGRES_PASSWORD", "MONGO_ROOT_PASSWORD", "RABBITMQ_PASSWORD",
  "MINIO_ROOT_SECRET_KEY", "MINIO_APP_SECRET_KEY", "MINIO_GOVERNANCE_SECRET_KEY",
  "ALERTMANAGER_WEBHOOK_TOKEN", "QDRANT_API_KEY", "EXECUTION_ADMIN_TOKEN",
  "EXECUTION_ADMIN_PASSWORD", "EXECUTION_JWT_SECRET", "CONTROL_PLANE_JWT_SECRET",
  "CONTROL_PLANE_INTERNAL_TOKEN", "WORKFLOW_SECRET_MASTER_KEY"
)
$uriSafeKeys = @(
  "POSTGRES_USER", "POSTGRES_PASSWORD", "MONGO_ROOT_USERNAME", "MONGO_ROOT_PASSWORD",
  "RABBITMQ_USERNAME", "RABBITMQ_PASSWORD", "MINIO_ROOT_ACCESS_KEY", "MINIO_ROOT_SECRET_KEY",
  "MINIO_APP_ACCESS_KEY", "MINIO_APP_SECRET_KEY", "MINIO_GOVERNANCE_ACCESS_KEY", "MINIO_GOVERNANCE_SECRET_KEY",
  "QDRANT_API_KEY"
)
$weakPattern = '(?i)^(?:change[-_]?me|admin123|password|passwd|secret|dev|test|example|placeholder|minioadmin|qwerty|letmein|guest|root|apf)(?:[-_].*)?$'

foreach ($entry in $required.GetEnumerator()) {
  $key = $entry.Key
  $minimum = [int]$entry.Value
  if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$values[$key])) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "MISSING"; Message = "required production value is absent" })
    continue
  }
  $value = [string]$values[$key]
  if ($value.Length -lt $minimum) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "TOO_SHORT"; Message = "minimum length is $minimum; actual length is $($value.Length)" })
  }
  if ($value -match $weakPattern -or $value.StartsWith('<') -or $value.StartsWith('${')) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "WEAK_OR_PLACEHOLDER"; Message = "value matches a forbidden development/default pattern" })
  }
  if ($uriSafeKeys -contains $key -and $value -notmatch '^[A-Za-z0-9._~-]+$') {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "NOT_URI_SAFE"; Message = "use URL-safe characters A-Z a-z 0-9 . _ ~ -" })
  }
}

foreach ($key in @("APP_ENV", "EXECUTION_ENV")) {
  if ($values.ContainsKey($key) -and ([string]$values[$key]).ToLowerInvariant() -notin @("prod", "production")) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "NOT_PRODUCTION"; Message = "must be prod or production" })
  }
}

if (-not $values.ContainsKey("MINIO_SECURE") -or
    ([string]$values["MINIO_SECURE"]).Trim().ToLowerInvariant() -notin @("1", "true", "yes", "on")) {
  $findings.Add([pscustomobject]@{ Key = "MINIO_SECURE"; Code = "TLS_DISABLED"; Message = "production MinIO transport must use TLS" })
}
if (-not $values.ContainsKey("MINIO_CA_CERT_PATH") -or [string]::IsNullOrWhiteSpace([string]$values["MINIO_CA_CERT_PATH"])) {
  $findings.Add([pscustomobject]@{ Key = "MINIO_CA_CERT_PATH"; Code = "MISSING_CA_PATH"; Message = "production MinIO TLS requires an explicit CA certificate path" })
}

$minioIdentityKeys = @("MINIO_ROOT_ACCESS_KEY", "MINIO_APP_ACCESS_KEY", "MINIO_GOVERNANCE_ACCESS_KEY")
$configuredIdentityKeys = @($minioIdentityKeys | Where-Object { $values.ContainsKey($_) } | ForEach-Object { [string]$values[$_] })
if (@($configuredIdentityKeys | Select-Object -Unique).Count -ne $configuredIdentityKeys.Count) {
  $findings.Add([pscustomobject]@{ Key = "MINIO_*_ACCESS_KEY"; Code = "REUSED_IDENTITY"; Message = "root, application, and governance access keys must be distinct" })
}
foreach ($legacyKey in @("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")) {
  if ($values.ContainsKey($legacyKey) -and -not [string]::IsNullOrWhiteSpace([string]$values[$legacyKey])) {
    $findings.Add([pscustomobject]@{ Key = $legacyKey; Code = "LEGACY_CREDENTIAL"; Message = "legacy shared MinIO credentials are forbidden in production" })
  }
}

if (-not $values.ContainsKey("OBJECT_STORAGE_TENANT_QUOTA_BYTES") -or
    [string]::IsNullOrWhiteSpace([string]$values["OBJECT_STORAGE_TENANT_QUOTA_BYTES"])) {
  $findings.Add([pscustomobject]@{ Key = "OBJECT_STORAGE_TENANT_QUOTA_BYTES"; Code = "MISSING"; Message = "a positive production tenant quota is required" })
} else {
  [long]$storageQuota = 0
  if (-not [long]::TryParse([string]$values["OBJECT_STORAGE_TENANT_QUOTA_BYTES"], [ref]$storageQuota) -or $storageQuota -le 0) {
    $findings.Add([pscustomobject]@{ Key = "OBJECT_STORAGE_TENANT_QUOTA_BYTES"; Code = "INVALID_CAPACITY"; Message = "must be a positive integer byte count" })
  }
}

$boundedIntegerConfigs = [ordered]@{
  MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS = @(1, 3650)
  MINIO_DOCUMENT_NONCURRENT_RETAIN_VERSIONS = @(1, 100)
}
foreach ($entry in $boundedIntegerConfigs.GetEnumerator()) {
  $key = [string]$entry.Key
  [int]$parsedValue = 0
  $bounds = @($entry.Value)
  if (-not $values.ContainsKey($key) -or
      -not [int]::TryParse([string]$values[$key], [ref]$parsedValue) -or
      $parsedValue -lt [int]$bounds[0] -or $parsedValue -gt [int]$bounds[1]) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "INVALID_GOVERNANCE_CONFIG"; Message = "must be an integer between $($bounds[0]) and $($bounds[1])" })
  }
}

if (-not $values.ContainsKey("MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES") -or
    ([string]$values["MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES"]).Trim().ToLowerInvariant() -notin @("1", "true", "yes", "on")) {
  $findings.Add([pscustomobject]@{ Key = "MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES"; Code = "WRITE_ADMISSION_DISABLED"; Message = "production must fail closed when physical capacity is critical or unavailable" })
}

if (-not $values.ContainsKey("MINIO_DOCUMENT_BUCKET") -or
    ([string]$values["MINIO_DOCUMENT_BUCKET"]) -notmatch '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$') {
  $findings.Add([pscustomobject]@{ Key = "MINIO_DOCUMENT_BUCKET"; Code = "INVALID_BUCKET"; Message = "a valid explicit production document bucket is required" })
}

function Test-JwtRing {
  param([Parameter(Mandatory = $true)][string]$Prefix)
  $idKey = "${Prefix}_JWT_KEY_ID"
  $secretKey = "${Prefix}_JWT_SECRET"
  $previousKey = "${Prefix}_JWT_PREVIOUS_KEYS"
  $previousExpiryKey = "${Prefix}_JWT_PREVIOUS_KEYS_EXPIRES_AT"
  $legacyExpiryKey = "${Prefix}_JWT_LEGACY_ACCEPT_UNTIL"

  if ($values.ContainsKey($idKey) -and ([string]$values[$idKey]) -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$') {
    $findings.Add([pscustomobject]@{ Key = $idKey; Code = "INVALID_KEY_ID"; Message = "must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$" })
  }
  $previousRaw = if ($values.ContainsKey($previousKey)) { [string]$values[$previousKey] } else { "" }
  $previousExpiryRaw = if ($values.ContainsKey($previousExpiryKey)) { [string]$values[$previousExpiryKey] } else { "" }
  if (-not [string]::IsNullOrWhiteSpace($previousRaw)) {
    $entries = @($previousRaw.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($entries.Count -gt 3) {
      $findings.Add([pscustomobject]@{ Key = $previousKey; Code = "TOO_MANY_PREVIOUS_KEYS"; Message = "at most 3 previous JWT keys are allowed" })
    }
    $ids = @()
    $secrets = @()
    foreach ($entry in $entries) {
      if ($entry -notmatch '^([A-Za-z0-9][A-Za-z0-9._-]{0,31}):([A-Za-z0-9._~-]{32,})$') {
        $findings.Add([pscustomobject]@{ Key = $previousKey; Code = "INVALID_PREVIOUS_KEY"; Message = "entries must use kid:URL-safe-secret" })
        continue
      }
      $ids += $Matches[1]
      $secrets += $Matches[2]
    }
    if (@($ids | Select-Object -Unique).Count -ne $ids.Count -or ($values.ContainsKey($idKey) -and $ids -contains [string]$values[$idKey])) {
      $findings.Add([pscustomobject]@{ Key = $previousKey; Code = "REUSED_KEY_ID"; Message = "primary and previous JWT key ids must be distinct" })
    }
    if (@($secrets | Select-Object -Unique).Count -ne $secrets.Count -or ($values.ContainsKey($secretKey) -and $secrets -contains [string]$values[$secretKey])) {
      $findings.Add([pscustomobject]@{ Key = $previousKey; Code = "REUSED_JWT_SECRET"; Message = "primary and previous JWT secrets must be distinct" })
    }
    if ([string]::IsNullOrWhiteSpace($previousExpiryRaw)) {
      $findings.Add([pscustomobject]@{ Key = $previousExpiryKey; Code = "MISSING_EXPIRY"; Message = "previous JWT keys require an expiry" })
    }
  } elseif (-not [string]::IsNullOrWhiteSpace($previousExpiryRaw)) {
    $findings.Add([pscustomobject]@{ Key = $previousExpiryKey; Code = "UNEXPECTED_EXPIRY"; Message = "expiry requires previous JWT keys" })
  }

  foreach ($deadlineKey in @($previousExpiryKey, $legacyExpiryKey)) {
    $rawDeadline = if ($values.ContainsKey($deadlineKey)) { [string]$values[$deadlineKey] } else { "" }
    if ([string]::IsNullOrWhiteSpace($rawDeadline)) { continue }
    try {
      if ($rawDeadline -notmatch '(?:Z|[+]00:00)$') { throw "deadline must explicitly use UTC" }
      $deadline = [DateTimeOffset]::Parse($rawDeadline, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
      $now = [DateTimeOffset]::UtcNow
      if ($deadline.Offset -ne [TimeSpan]::Zero) { throw "deadline is not UTC" }
      if ($deadline -le $now) { throw "deadline is not in the future" }
      if (($deadline - $now).TotalSeconds -gt 604800) { throw "deadline exceeds 7 days" }
    } catch {
      $findings.Add([pscustomobject]@{ Key = $deadlineKey; Code = "INVALID_EXPIRY"; Message = "must be future ISO-8601 UTC and no more than 7 days away" })
    }
  }
}

Test-JwtRing -Prefix "EXECUTION"
Test-JwtRing -Prefix "CONTROL_PLANE"

if ($values.ContainsKey("WORKFLOW_SECRET_MASTER_KEY")) {
  try {
    $fernet = [string]$values["WORKFLOW_SECRET_MASTER_KEY"]
    $normalized = $fernet.Replace('-', '+').Replace('_', '/')
    while ($normalized.Length % 4 -ne 0) { $normalized += '=' }
    $decoded = [Convert]::FromBase64String($normalized)
    if ($decoded.Length -ne 32) { throw "decoded length is not 32 bytes" }
  } catch {
    $findings.Add([pscustomobject]@{ Key = "WORKFLOW_SECRET_MASTER_KEY"; Code = "INVALID_FERNET_KEY"; Message = "must be URL-safe base64 encoding of exactly 32 bytes" })
  }
}

if ($values.ContainsKey("WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS") -and -not [string]::IsNullOrWhiteSpace([string]$values["WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"])) {
  $previousKeys = @(([string]$values["WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"]).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  if ($previousKeys.Count -eq 0) {
    $findings.Add([pscustomobject]@{ Key = "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"; Code = "INVALID_PREVIOUS_KEYS"; Message = "must be a comma-separated list of Fernet keys" })
  }
  $allRotationKeys = @([string]$values["WORKFLOW_SECRET_MASTER_KEY"]) + $previousKeys
  if (@($allRotationKeys | Select-Object -Unique).Count -ne $allRotationKeys.Count) {
    $findings.Add([pscustomobject]@{ Key = "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"; Code = "REUSED_ROTATION_KEY"; Message = "primary and previous Fernet keys must be distinct" })
  }
  foreach ($previousKey in $previousKeys) {
    try {
      $normalized = $previousKey.Replace('-', '+').Replace('_', '/')
      while ($normalized.Length % 4 -ne 0) { $normalized += '=' }
      $decoded = [Convert]::FromBase64String($normalized)
      if ($decoded.Length -ne 32) { throw "decoded length is not 32 bytes" }
    } catch {
      $findings.Add([pscustomobject]@{ Key = "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS"; Code = "INVALID_PREVIOUS_FERNET_KEY"; Message = "every previous key must decode to exactly 32 bytes" })
      break
    }
  }
}

$seenSecrets = @{}
foreach ($key in $secretKeys) {
  if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$values[$key])) { continue }
  $fingerprint = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$values[$key]))
  if ($seenSecrets.ContainsKey($fingerprint)) {
    $findings.Add([pscustomobject]@{ Key = $key; Code = "REUSED_SECRET"; Message = "duplicates secret material used by $($seenSecrets[$fingerprint])" })
  } else {
    $seenSecrets[$fingerprint] = $key
  }
}

# Optional sensitive values are also rejected when they contain obvious placeholders.
foreach ($entry in $values.GetEnumerator()) {
  if ($required.Contains($entry.Key)) { continue }
  if ($entry.Key -notmatch '(?i)(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY|PRIVATE_KEY)$') { continue }
  $value = [string]$entry.Value
  if ($value -and ($value -match $weakPattern -or $value.StartsWith('<') -or $value.StartsWith('${'))) {
    $findings.Add([pscustomobject]@{ Key = $entry.Key; Code = "WEAK_OPTIONAL_SECRET"; Message = "configured optional secret matches a forbidden development/default pattern" })
  }
}

if ($findings.Count -gt 0) {
  Write-Output "PRODUCTION_SECRET_VALIDATION_FAILED count=$($findings.Count)"
  foreach ($finding in $findings | Sort-Object Key, Code) {
    Write-Output "$($finding.Key)|$($finding.Code)|$($finding.Message)"
  }
  throw "Production secret validation failed; no secret values were printed."
}

Write-Output "PRODUCTION_SECRET_VALIDATION_OK required=$($required.Count) distinct_secrets=$($seenSecrets.Count)"
