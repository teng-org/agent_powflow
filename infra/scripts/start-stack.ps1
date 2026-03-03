param(
  [string]$PortsConfigPath = "config/service-ports.json",
  [string]$RuntimeConfigPath = "config/runtime-config.json",
  [string]$EnvFilePath = "config/stack.generated.env",
  [switch]$Build,
  [switch]$ForceRecreate,
  [switch]$GenerateOnly,
  [switch]$SkipPrePull,
  [int]$PrePullRetries = 3,
  [int]$ComposeParallelLimitOnBuild = 1
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $PortsConfigPath)) {
  throw "Ports config not found: $PortsConfigPath"
}
if (!(Test-Path $RuntimeConfigPath)) {
  throw "Runtime config not found: $RuntimeConfigPath"
}

$ports = Get-Content $PortsConfigPath -Raw | ConvertFrom-Json
$runtime = Get-Content $RuntimeConfigPath -Raw | ConvertFrom-Json

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
  "GLOBAL_KB_ID"             = [string]$runtime.global_kb_id
  "EXECUTION_CORS_ORIGINS"   = [string]$runtime.execution_cors_origins
  "SILICONFLOW_API_KEY"      = [string]$runtime.siliconflow_api_key
  "EMBEDDING_MODE"           = [string]$runtime.embedding_mode
  "EMBEDDING_MAX_CHARS"      = [string]$runtime.embedding_max_chars
}

$lines = $mapping.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
Set-Content -Path $EnvFilePath -Value ($lines -join "`r`n") -Encoding utf8
Write-Host "Generated env file: $EnvFilePath"

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

if ($Build -and -not $SkipPrePull) {
  # Reduce cold-start build failures caused by transient auth.docker.io timeout in parallel BuildKit metadata resolve.
  $baseImages = @(
    "python:3.11-slim",
    "maven:3.9.9-eclipse-temurin-21",
    "eclipse-temurin:21-jre"
  )
  foreach ($img in $baseImages) {
    $ok = Invoke-DockerPullWithRetry -Image $img -Retries $PrePullRetries
    if (-not $ok) {
      throw "Failed to pre-pull image after retries: $img"
    }
  }
}

$args = @(
  "--env-file", $EnvFilePath,
  "-f", "infra/compose/docker-compose.stack.yml",
  "up",
  "-d"
)
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
