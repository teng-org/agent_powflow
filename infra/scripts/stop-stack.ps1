param(
  [string]$EnvFilePath = "config/stack.generated.env",
  [switch]$WithVolumes
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $EnvFilePath)) {
  throw "Env file not found: $EnvFilePath. Start once with infra/scripts/start-stack.ps1 first."
}

$args = @(
  "--env-file", $EnvFilePath,
  "-f", "infra/compose/docker-compose.stack.yml",
  "down"
)
if ($WithVolumes) { $args += "-v" }

Write-Host "Running: docker compose $($args -join ' ')"
& docker compose @args
if ($LASTEXITCODE -ne 0) {
  throw "docker compose down failed (exit code: $LASTEXITCODE)"
}

Write-Host "Stack stopped."
