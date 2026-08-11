param(
  [string]$EnvFilePath = "config/stack.generated.env",
  [switch]$WithVolumes,
  [switch]$Observability,
  [switch]$MinioTls,
  [switch]$EdgeGateway,
  [switch]$Production,
  [string]$ProductionEnvFile = ".env.production"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $EnvFilePath)) {
  throw "Env file not found: $EnvFilePath. Start once with infra/scripts/start-stack.ps1 first."
}

$args = @(
  "--env-file", $EnvFilePath
)
if ($Production) { $args += @("--env-file", $ProductionEnvFile) }
$args += @("-f", "infra/compose/docker-compose.stack.yml")
if ($Production) { $args += @("-f", "infra/compose/docker-compose.production.yml") }
if ($Observability) { $args += @("-f", "infra/compose/docker-compose.observability.yml") }
if ($MinioTls) { $args += @("-f", "infra/compose/docker-compose.minio-tls.yml") }
if ($EdgeGateway -or $Production) { $args += @("--profile", "edge-gateway") }
$args += "down"
if ($WithVolumes) { $args += "-v" }

Write-Host "Running: docker compose $($args -join ' ')"
& docker compose @args
if ($LASTEXITCODE -ne 0) {
  throw "docker compose down failed (exit code: $LASTEXITCODE)"
}

Write-Host "Stack stopped."
