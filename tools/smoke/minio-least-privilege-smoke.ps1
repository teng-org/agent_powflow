param(
  [string]$MinioContainer = "agentpowflow-minio",
  [string]$ExecutionContainer = "agentpowflow-execution-api",
  [string]$IdentityInitContainer = "agentpowflow-minio-service-identity-init"
)

$ErrorActionPreference = "Stop"

function Assert-ExactContainer([string]$Name) {
  $actual = (& docker inspect --format '{{.Name}}' $Name 2>$null).Trim().TrimStart('/')
  if ($LASTEXITCODE -ne 0 -or $actual -ne $Name) {
    throw "Expected exact container is unavailable: $Name"
  }
}

function Get-ContainerEnv([string]$Container, [string]$Key) {
  $prefix = "$Key="
  $line = @(& docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $Container) |
    Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) } |
    Select-Object -First 1
  if (-not $line) { throw "Required container environment entry is missing: $Key" }
  return $line.Substring($prefix.Length)
}

function Invoke-ContainerPython([string]$Container, [string]$Code, [string[]]$Environment = @()) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
  $arguments = @("exec")
  foreach ($entry in $Environment) { $arguments += @("-e", $entry) }
  $arguments += @($Container, "python", "-c", "import base64,sys;exec(base64.b64decode(sys.argv[1]))", $encoded)
  & docker @arguments
}

function Invoke-ContainerShell([string]$Container, [string]$Code, [string[]]$Environment = @()) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Code))
  $arguments = @("exec", "-e", "SMOKE_SHELL_B64=$encoded")
  foreach ($entry in $Environment) { $arguments += @("-e", $entry) }
  $arguments += @($Container, "sh", "-c", 'echo $SMOKE_SHELL_B64 | base64 -d | sh')
  & docker @arguments
}

Assert-ExactContainer $MinioContainer
Assert-ExactContainer $ExecutionContainer
Assert-ExactContainer $IdentityInitContainer

$bucket = Get-ContainerEnv $ExecutionContainer "MINIO_DOCUMENT_BUCKET"
$governanceAccessKey = Get-ContainerEnv $IdentityInitContainer "MINIO_GOVERNANCE_ACCESS_KEY"
$governanceSecretKey = Get-ContainerEnv $IdentityInitContainer "MINIO_GOVERNANCE_SECRET_KEY"
$key = "v1/system/least-privilege-smoke/$([Guid]::NewGuid().ToString('N')).txt"
if ($bucket -notmatch '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$' -or
    $key -notmatch '^v1/system/least-privilege-smoke/[a-f0-9]{32}[.]txt$') {
  throw "Refusing to run with an unexpected cleanup target"
}

try {
  $appWriteCode = @'
import os
from runtime.object_storage import object_storage
key = os.environ["SMOKE_KEY"]
payload = b"agent-powflow-minio-least-privilege-smoke"
stored = object_storage.put_bytes(key, payload, content_type="text/plain")
assert object_storage.get_bytes(stored.bucket, stored.key, stored.sha256) == payload
print("MINIO_APP_OBJECT_ACCESS_OK put=1 get=1")
'@
  Invoke-ContainerPython $ExecutionContainer $appWriteCode @("SMOKE_KEY=$key")
  if ($LASTEXITCODE -ne 0) {
    throw "Application identity object access failed"
  }

  $governanceProbe = @'
set -eu
mc alias set --insecure governance https://localhost:9000 "$GOVERNANCE_ACCESS_KEY" "$GOVERNANCE_SECRET_KEY" >/dev/null
if mc stat --insecure "governance/$SMOKE_BUCKET/$SMOKE_KEY" >/dev/null 2>&1; then
  echo "governance identity unexpectedly read an object" >&2
  exit 9
fi
echo "MINIO_GOVERNANCE_OBJECT_ACCESS_DENIED_OK"
'@
  $denial = Invoke-ContainerShell $MinioContainer $governanceProbe @(
    "GOVERNANCE_ACCESS_KEY=$governanceAccessKey",
    "GOVERNANCE_SECRET_KEY=$governanceSecretKey",
    "SMOKE_BUCKET=$bucket",
    "SMOKE_KEY=$key"
  )
  if ($LASTEXITCODE -ne 0) { throw "Governance identity object-access denial was not proven" }
  $denial

  $appDeleteCode = @'
import os
from runtime.object_storage import object_storage
object_storage.delete_object(object_storage.bucket, os.environ["SMOKE_KEY"])
print("MINIO_APP_OBJECT_DELETE_OK delete=1")
'@
  Invoke-ContainerPython $ExecutionContainer $appDeleteCode @("SMOKE_KEY=$key")
  if ($LASTEXITCODE -ne 0) {
    throw "Application identity object deletion failed"
  }
} finally {
  $cleanupProbe = @'
set -eu
mc alias set --insecure root https://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc rm --insecure --versions --force "root/$SMOKE_BUCKET/$SMOKE_KEY" >/dev/null
remaining=$(mc ls --insecure --versions "root/$SMOKE_BUCKET/$SMOKE_KEY")
if [ -n "$remaining" ]; then
  echo "exact smoke object cleanup failed" >&2
  exit 10
fi
'@
  Invoke-ContainerShell $MinioContainer $cleanupProbe @("SMOKE_BUCKET=$bucket", "SMOKE_KEY=$key")
  if ($LASTEXITCODE -ne 0) { throw "Exact smoke object version cleanup failed" }
}

Write-Output "MINIO_LEAST_PRIVILEGE_SMOKE_OK app_object_rw=1 governance_object_denied=1 exact_cleanup=1"
