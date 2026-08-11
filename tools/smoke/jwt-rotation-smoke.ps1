param(
  [string]$EnvFile = "config/stack.generated.env",
  [string]$PortsFile = "config/service-ports.env"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))

function Read-EnvValues {
  param([string]$Path)
  $result = @{}
  $resolved = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $workspaceRoot $Path }
  if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { return $result }
  foreach ($line in Get-Content -LiteralPath $resolved -Encoding UTF8) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $result[$Matches[1]] = $Matches[2] }
  }
  return $result
}

function Get-Value {
  param([hashtable]$Primary, [hashtable]$Secondary, [string]$Key, [string]$Default)
  if ($Primary.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace([string]$Primary[$Key])) { return [string]$Primary[$Key] }
  if ($Secondary.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace([string]$Secondary[$Key])) { return [string]$Secondary[$Key] }
  return $Default
}

function Invoke-Json {
  param([string]$Method, [string]$Uri, [hashtable]$Headers = @{}, [object]$Body = $null)
  $params = @{ Method = $Method; Uri = $Uri; Headers = $Headers; UseBasicParsing = $true }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = $Body | ConvertTo-Json -Depth 8 -Compress
  }
  $response = Invoke-WebRequest @params
  if ([string]::IsNullOrWhiteSpace($response.Content)) { return $null }
  return $response.Content | ConvertFrom-Json
}

function Assert-Rejected {
  param([string]$Uri, [string]$Token)
  try {
    Invoke-WebRequest -Method GET -Uri $Uri -Headers @{ Authorization = "Bearer $Token" } -UseBasicParsing | Out-Null
  } catch {
    $status = [int]$_.Exception.Response.StatusCode
    if ($status -eq 401) { return }
    throw "Expected 401 from authentication boundary, received $status at $Uri"
  }
  throw "Authentication boundary unexpectedly accepted a cross-plane or replayed token at $Uri"
}

function Decode-JsonPart {
  param([string]$Part)
  $normalized = $Part.Replace('-', '+').Replace('_', '/')
  while ($normalized.Length % 4 -ne 0) { $normalized += '=' }
  return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($normalized)) | ConvertFrom-Json
}

$envValues = Read-EnvValues -Path $EnvFile
$portValues = Read-EnvValues -Path $PortsFile
$controlPort = Get-Value $envValues $portValues "CONTROL_PLANE_PORT" "8099"
$executionPort = Get-Value $envValues $portValues "EXECUTION_API_PORT" "8090"
$chatPort = Get-Value $envValues $portValues "CHAT_PLANE_PORT" "8092"
$username = Get-Value $envValues $portValues "EXECUTION_ADMIN_USERNAME" "admin"
$password = Get-Value $envValues $portValues "EXECUTION_ADMIN_PASSWORD" "admin123"
$tenant = Get-Value $envValues $portValues "CONTROL_PLANE_ADMIN_TENANT_ID" "platform"
$controlKid = Get-Value $envValues $portValues "CONTROL_PLANE_JWT_KEY_ID" "control-dev-primary"
$executionKid = Get-Value $envValues $portValues "EXECUTION_JWT_KEY_ID" "dev-primary"

$controlBase = "http://127.0.0.1:$controlPort"
$executionBase = "http://127.0.0.1:$executionPort"
$chatBase = "http://127.0.0.1:$chatPort"

$control = Invoke-Json POST "$controlBase/api/v1/auth/login" -Body @{
  tenantCode = $tenant; username = $username; password = $password
}
$execution = Invoke-Json POST "$executionBase/api/v1/admin/auth/login" -Body @{
  username = $username; password = $password
}
$controlToken = [string]$control.accessToken
$executionToken = [string]$execution.access_token
$refreshToken = [string]$control.refreshToken
if ($controlToken.Split('.').Count -ne 3 -or $executionToken.Split('.').Count -ne 3) {
  throw "Newly issued access tokens must use the standard three-part JWT format."
}
$controlHeader = Decode-JsonPart $controlToken.Split('.')[0]
$controlPayload = Decode-JsonPart $controlToken.Split('.')[1]
$executionHeader = Decode-JsonPart $executionToken.Split('.')[0]
$executionPayload = Decode-JsonPart $executionToken.Split('.')[1]
if ($controlHeader.alg -ne "HS256" -or $controlHeader.typ -ne "JWT" -or $controlHeader.kid -ne $controlKid) { throw "Control JWT header contract failed." }
if ($executionHeader.alg -ne "HS256" -or $executionHeader.typ -ne "JWT" -or $executionHeader.kid -ne $executionKid) { throw "Execution JWT header contract failed." }
if ($controlPayload.iss -ne "agentpowflow-control" -or $controlPayload.aud -ne "control-plane") { throw "Control JWT issuer/audience contract failed." }
if ($executionPayload.iss -ne "agentpowflow-execution" -or $executionPayload.aud -ne "execution-plane") { throw "Execution JWT issuer/audience contract failed." }

$controlHeaders = @{ Authorization = "Bearer $controlToken" }
Write-Output "JWT_ROTATION_SMOKE_STEP control_me"
Invoke-Json GET "$controlBase/api/v1/auth/me" -Headers $controlHeaders | Out-Null
Write-Output "JWT_ROTATION_SMOKE_STEP control_token_to_chat"
Invoke-Json GET "$chatBase/api/v1/chat/sessions?limit=1" -Headers $controlHeaders | Out-Null
Write-Output "JWT_ROTATION_SMOKE_STEP cross_plane_rejection"
Assert-Rejected "$executionBase/api/v1/admin/kbs" $controlToken
Assert-Rejected "$controlBase/api/v1/auth/me" $executionToken
Assert-Rejected "$chatBase/api/v1/chat/sessions?limit=1" $executionToken

Write-Output "JWT_ROTATION_SMOKE_STEP refresh_rotate"
$rotated = Invoke-Json POST "$controlBase/api/v1/auth/refresh" -Body @{ refreshToken = $refreshToken }
$rotatedRefresh = [string]$rotated.refreshToken
if ([string]::IsNullOrWhiteSpace($rotatedRefresh) -or $rotatedRefresh -eq $refreshToken) {
  throw "Refresh rotation did not return a distinct replacement token."
}
Write-Output "JWT_ROTATION_SMOKE_STEP rotated_access"
Invoke-Json GET "$controlBase/api/v1/auth/me" -Headers @{ Authorization = "Bearer $($rotated.accessToken)" } | Out-Null
Write-Output "JWT_ROTATION_SMOKE_STEP refresh_replay"
try {
  Invoke-Json POST "$controlBase/api/v1/auth/refresh" -Body @{ refreshToken = $refreshToken } | Out-Null
  throw "Revoked refresh token replay was unexpectedly accepted."
} catch {
  if ($_.Exception.Message -eq "Revoked refresh token replay was unexpectedly accepted.") { throw }
  $status = [int]$_.Exception.Response.StatusCode
  if ($status -ne 401) { throw "Refresh replay returned unexpected status $status." }
}
Write-Output "JWT_ROTATION_SMOKE_STEP logout"
Invoke-Json POST "$controlBase/api/v1/auth/logout" -Headers @{ Authorization = "Bearer $($rotated.accessToken)" } -Body @{ refreshToken = $rotatedRefresh } | Out-Null

Write-Output "JWT_ROTATION_SMOKE_OK standard_tokens=2 control_to_chat=1 cross_plane_rejected=3 refresh_rotated=1 refresh_replay_rejected=1"
