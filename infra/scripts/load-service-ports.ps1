param(
  [string]$ConfigPath = "config/service-ports.json",
  [switch]$WriteEnvFile
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $ConfigPath)) {
  throw "Ports config not found: $ConfigPath"
}

$ports = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$mapping = @{
  "CONTROL_PLANE_PORT"      = [string]$ports.control_plane_port
  "EXECUTION_API_PORT"      = [string]$ports.execution_api_port
  "CHAT_PLANE_PORT"         = [string]$ports.chat_plane_port
  "DEBUG_WEB_PORT"          = [string]$ports.debug_web_port
  "KB_ADMIN_WEB_PORT"       = [string]$ports.kb_admin_web_port
  "WEB_APP_PORT"            = [string]$ports.web_app_port
  "WEB_ADMIN_PORT"          = [string]$ports.web_admin_port
  "POSTGRES_PORT"           = [string]$ports.postgres_port
  "MONGODB_PORT"            = [string]$ports.mongodb_port
  "RABBITMQ_PORT"           = [string]$ports.rabbitmq_port
  "RABBITMQ_MANAGEMENT_PORT"= [string]$ports.rabbitmq_management_port
  "MINIO_API_PORT"          = [string]$ports.minio_api_port
  "MINIO_CONSOLE_PORT"      = [string]$ports.minio_console_port
  "QDRANT_HTTP_PORT"        = [string]$ports.qdrant_http_port
  "QDRANT_GRPC_PORT"        = [string]$ports.qdrant_grpc_port
}

foreach ($k in $mapping.Keys) {
  Set-Item -Path "Env:$k" -Value $mapping[$k]
}

if ($WriteEnvFile) {
  $envFile = "config/service-ports.env"
  $lines = $mapping.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }
  Set-Content -Path $envFile -Value ($lines -join "`r`n") -Encoding utf8
  Write-Host "Generated $envFile"
}

Write-Host "Loaded service ports from $ConfigPath"
$mapping.GetEnumerator() | Sort-Object Name | ForEach-Object { Write-Host "$($_.Name)=$($_.Value)" }
