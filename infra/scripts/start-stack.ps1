param(
  [string]$PortsConfigPath = "config/service-ports.json",
  [string]$RuntimeConfigDefaultPath = "config/runtime-config.default.json",
  [string]$RuntimeConfigUserPath = "config/runtime-config.user.json",
  [string]$RuntimeConfigPath = "config/runtime-config.json",
  [string]$EnvFilePath = "config/stack.generated.env",
  [switch]$Build,
  [switch]$ForceRecreate,
  [switch]$GenerateOnly,
  [switch]$Production,
  [switch]$Observability,
  [switch]$MinioTls,
  [switch]$EdgeGateway,
  [string]$ProductionEnvFile = ".env.production",
  [switch]$SkipPrePull,
  [int]$PrePullRetries = 3,
  [int]$ComposeParallelLimitOnBuild = 1
)

$ErrorActionPreference = "Stop"

if ($Production -and -not $Observability) {
  throw "Production startup requires -Observability for physical-capacity write admission and alerts."
}
if ($Production -and -not $MinioTls) {
  throw "Production startup requires -MinioTls; plaintext MinIO is not permitted."
}

if (!(Test-Path $PortsConfigPath)) {
  throw "Ports config not found: $PortsConfigPath"
}
if (!(Test-Path $RuntimeConfigDefaultPath) -and !(Test-Path $RuntimeConfigPath)) {
  throw "Runtime default config not found: $RuntimeConfigDefaultPath (fallback: $RuntimeConfigPath)"
}

$ports = Get-Content $PortsConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeDefaultPath = if (Test-Path $RuntimeConfigDefaultPath) { $RuntimeConfigDefaultPath } else { $RuntimeConfigPath }
$runtimeDefault = Get-Content $runtimeDefaultPath -Raw -Encoding UTF8 | ConvertFrom-Json
$runtimeUser = $null
if (Test-Path $RuntimeConfigUserPath) {
  try {
    $raw = Get-Content $RuntimeConfigUserPath -Raw -Encoding UTF8
    if ($raw -and $raw.Trim().Length -gt 0) {
      $runtimeUser = $raw | ConvertFrom-Json
    }
  } catch {
    throw "Invalid runtime user config JSON: $RuntimeConfigUserPath"
  }
}

function ConvertTo-HashtableDeep {
  param($InputObject)
  if ($null -eq $InputObject) { return $null }
  if ($InputObject -is [System.Collections.IDictionary]) {
    $table = @{}
    foreach ($key in $InputObject.Keys) {
      $table[$key] = ConvertTo-HashtableDeep -InputObject $InputObject[$key]
    }
    return $table
  }
  if (($InputObject -is [System.Collections.IEnumerable]) -and -not ($InputObject -is [string])) {
    $items = @()
    foreach ($item in $InputObject) {
      $items += ,(ConvertTo-HashtableDeep -InputObject $item)
    }
    return $items
  }
  if ($InputObject -is [psobject]) {
    $table = @{}
    foreach ($prop in $InputObject.PSObject.Properties) {
      $table[$prop.Name] = ConvertTo-HashtableDeep -InputObject $prop.Value
    }
    return $table
  }
  return $InputObject
}

function Merge-HashtableDeep {
  param(
    [Parameter(Mandatory = $true)][hashtable]$Target,
    [Parameter(Mandatory = $true)][hashtable]$Source
  )
  foreach ($name in $Source.Keys) {
    $srcValue = $Source[$name]
    if (
      $Target.ContainsKey($name) -and
      ($Target[$name] -is [System.Collections.IDictionary]) -and
      ($srcValue -is [System.Collections.IDictionary])
    ) {
      Merge-HashtableDeep -Target $Target[$name] -Source $srcValue
      continue
    }
    $Target[$name] = $srcValue
  }
}

$runtimeMerged = ConvertTo-HashtableDeep -InputObject $runtimeDefault
if ($runtimeUser -ne $null) {
  $runtimeUserTable = ConvertTo-HashtableDeep -InputObject $runtimeUser
  if ($runtimeUserTable -is [System.Collections.IDictionary]) {
    Merge-HashtableDeep -Target $runtimeMerged -Source $runtimeUserTable
  }
}
$runtime = $runtimeMerged | ConvertTo-Json -Depth 20 | ConvertFrom-Json

$mapping = [ordered]@{
  "CONTROL_PLANE_PORT"       = [string]$ports.control_plane_port
  "EXECUTION_API_PORT"       = [string]$ports.execution_api_port
  "CHAT_PLANE_PORT"          = [string]$ports.chat_plane_port
  "DEBUG_WEB_PORT"           = [string]$ports.debug_web_port
  "KB_ADMIN_WEB_PORT"        = [string]$ports.kb_admin_web_port
  "WEB_APP_PORT"             = [string]$ports.web_app_port
  "WEB_ADMIN_PORT"           = [string]$ports.web_admin_port
  "POSTGRES_PORT"            = [string]$ports.postgres_port
  "MONGODB_PORT"             = [string]$ports.mongodb_port
  "RABBITMQ_PORT"            = [string]$ports.rabbitmq_port
  "RABBITMQ_MANAGEMENT_PORT" = [string]$ports.rabbitmq_management_port
  "MINIO_API_PORT"           = [string]$ports.minio_api_port
  "MINIO_CONSOLE_PORT"       = [string]$ports.minio_console_port
  "QDRANT_HTTP_PORT"         = [string]$ports.qdrant_http_port
  "QDRANT_GRPC_PORT"         = [string]$ports.qdrant_grpc_port
  "EXECUTION_ADMIN_TOKEN"    = [string]$runtime.execution_admin_token
  "EXECUTION_ADMIN_USERNAME" = [string]$runtime.execution_admin_username
  "EXECUTION_ADMIN_PASSWORD" = [string]$runtime.execution_admin_password
  "EXECUTION_ADMIN_ROLE"     = [string]$runtime.execution_admin_role
  "EXECUTION_JWT_SECRET"     = [string]$runtime.execution_jwt_secret
  "EXECUTION_JWT_TTL_SECONDS"= [string]$runtime.execution_jwt_ttl_seconds
  "EXECUTION_ENV"            = [string]$runtime.execution_env
  "EXECUTION_AUDIT_ENABLED"  = [string]$runtime.execution_audit_enabled
  "EXECUTION_AUDIT_BASE_DIR" = [string]$runtime.execution_audit_base_dir
  "CONTROL_PLANE_CORS_ORIGINS" = [string]$runtime.execution_cors_origins
  "GLOBAL_KB_ID"             = [string]$runtime.global_kb_id
  "RUNTIME_CONFIG_DEFAULT_PATH" = "/app/config/runtime-config.default.json"
  "RUNTIME_CONFIG_USER_PATH"    = "/app/config/runtime-config.user.json"
  "EXECUTION_CORS_ORIGINS"   = [string]$runtime.execution_cors_origins
  "EMBEDDING_MODE"           = [string]$runtime.embedding_mode
  "EMBEDDING_MAX_CHARS"      = [string]$runtime.embedding_max_chars
}

$lines = $mapping.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
Set-Content -Path $EnvFilePath -Value ($lines -join "`r`n") -Encoding utf8
Write-Host "Generated env file: $EnvFilePath"

if ($Production) {
  $validator = Join-Path $PSScriptRoot "validate-production-secrets.ps1"
  & $validator -EnvFile $ProductionEnvFile
}

function Invoke-DockerPullWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Image,
    [int]$Retries = 3
  )
  for ($i = 1; $i -le $Retries; $i++) {
    Write-Host "Pre-pull image ($i/$Retries): $Image"
    & docker pull $Image
    if ($LASTEXITCODE -eq 0) { return $true }
    Start-Sleep -Seconds ([Math]::Min(10, 2 * $i))
  }
  return $false
}

if ($GenerateOnly) {
  Write-Host "GenerateOnly enabled. Skip docker compose up."
  return
}

if ($MinioTls) {
  $certificateDir = Join-Path ([System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))) ".local/minio-certs"
  $certificateFiles = @("server.crt", "server.key", "ca.crt") | ForEach-Object { Join-Path $certificateDir $_ }
  $missingCertificates = @($certificateFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
  if ($missingCertificates.Count -gt 0) {
    if ($Production) {
      throw "Production MinIO TLS certificates are missing under $certificateDir. Provision server.crt, server.key, and ca.crt through the approved secret/certificate process."
    }
    & (Join-Path $PSScriptRoot "new-minio-dev-certificates.ps1")
  }
}

if ($Build -and -not $SkipPrePull) {
  # Reduce cold-start build failures caused by transient auth.docker.io timeout in parallel BuildKit metadata resolve.
  $baseImages = @(
    "python:3.11-slim",
    "node:20-bookworm-slim",
    "maven:3.9.9-eclipse-temurin-21",
    "eclipse-temurin:21-jre"
  )
  foreach ($img in $baseImages) {
    $ok = Invoke-DockerPullWithRetry -Image $img -Retries $PrePullRetries
    if (-not $ok) {
      throw "Failed to pre-pull image after retries: $img"
    }
  }
  if ($Observability) {
    foreach ($img in @("prom/prometheus:v3.13.2", "prom/alertmanager:v0.33.1")) {
      $ok = Invoke-DockerPullWithRetry -Image $img -Retries $PrePullRetries
      if (-not $ok) { throw "Failed to pre-pull observability image after retries: $img" }
    }
  }
  if ($EdgeGateway -or $Production) {
    $ok = Invoke-DockerPullWithRetry -Image "kong:3.9" -Retries $PrePullRetries
    if (-not $ok) { throw "Failed to pre-pull edge gateway image after retries: kong:3.9" }
  }
}

$args = @(
  "--env-file", $EnvFilePath
)
if ($Production) {
  $args += @("--env-file", $ProductionEnvFile)
}
$args += @("-f", "infra/compose/docker-compose.stack.yml")
if ($Production) {
  $args += @("-f", "infra/compose/docker-compose.production.yml")
}
if ($Observability) {
  $args += @("-f", "infra/compose/docker-compose.observability.yml")
}
if ($MinioTls) {
  $args += @("-f", "infra/compose/docker-compose.minio-tls.yml")
}
if ($EdgeGateway -or $Production) {
  $args += @("--profile", "edge-gateway")
}
$args += @("up", "-d")
if ($Build) { $args += "--build" }
if ($ForceRecreate) { $args += "--force-recreate" }

Write-Host "Running: docker compose $($args -join ' ')"
$origComposeParallel = $env:COMPOSE_PARALLEL_LIMIT
if ($Build -and $ComposeParallelLimitOnBuild -gt 0) {
  $env:COMPOSE_PARALLEL_LIMIT = [string]$ComposeParallelLimitOnBuild
}
& docker compose @args
if ($null -eq $origComposeParallel) {
  Remove-Item Env:\COMPOSE_PARALLEL_LIMIT -ErrorAction SilentlyContinue
} else {
  $env:COMPOSE_PARALLEL_LIMIT = $origComposeParallel
}
if ($LASTEXITCODE -ne 0) {
  throw "docker compose up failed (exit code: $LASTEXITCODE)"
}

Write-Host "Stack started successfully."
