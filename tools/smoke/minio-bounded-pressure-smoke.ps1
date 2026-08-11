param(
  [ValidateRange(1, 32)][int]$LargeObjectMiB = 16,
  [ValidateRange(1, 32)][int]$ConcurrentObjects = 16,
  [ValidateRange(1, 4)][int]$ConcurrentObjectMiB = 1,
  [ValidateRange(2, 32)][int]$VersionCount = 10,
  [ValidateRange(1, 16)][int]$Workers = 8,
  [string]$PrometheusUrl = "http://127.0.0.1:9090",
  [string]$MinioContainer = "agentpowflow-minio",
  [string]$ExecutionContainer = "agentpowflow-execution-api"
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

function Get-PrometheusMetric([string]$Query) {
  $encodedQuery = [Uri]::EscapeDataString($Query)
  $response = Invoke-RestMethod -Method Get -Uri "$($script:prometheus)/api/v1/query?query=$encodedQuery"
  $rows = @($response.data.result)
  if ($response.status -ne "success" -or $rows.Count -lt 1) {
    throw "Required Prometheus metric is unavailable: $Query"
  }
  return [double]$rows[0].value[1]
}

$prometheusUri = [Uri]$PrometheusUrl
if ($prometheusUri.Scheme -notin @("http", "https") -or -not $prometheusUri.Host -or $prometheusUri.UserInfo) {
  throw "PrometheusUrl must be an http(s) URL without embedded credentials"
}
$script:prometheus = $PrometheusUrl.TrimEnd('/')

$totalWriteMiB = $LargeObjectMiB + ($ConcurrentObjects * $ConcurrentObjectMiB) + [Math]::Ceiling($VersionCount / 16.0)
if ($totalWriteMiB -gt 192) { throw "Configured smoke write volume exceeds the 192 MiB safety bound" }
if ($Workers -gt $ConcurrentObjects) { $Workers = $ConcurrentObjects }

Assert-ExactContainer $MinioContainer
Assert-ExactContainer $ExecutionContainer
$bucket = Get-ContainerEnv $ExecutionContainer "MINIO_DOCUMENT_BUCKET"
$secure = (Get-ContainerEnv $ExecutionContainer "MINIO_SECURE").Trim().ToLowerInvariant()
if ($secure -notin @("1", "true", "yes", "on")) { throw "Pressure smoke requires the TLS MinIO overlay" }

$smokeId = [Guid]::NewGuid().ToString('N')
$prefix = "v1/system/bounded-pressure-smoke/$smokeId"
$versionKey = "$prefix/versioned.bin"
if ($bucket -notmatch '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$' -or
    $prefix -notmatch '^v1/system/bounded-pressure-smoke/[a-f0-9]{32}$') {
  throw "Refusing to run with an unexpected cleanup target"
}

$targets = Invoke-RestMethod -Method Get -Uri "$($script:prometheus)/api/v1/targets"
$minioTargets = @($targets.data.activeTargets | Where-Object { $_.labels.job -eq "minio-cluster" })
if ($minioTargets.Count -ne 1 -or $minioTargets[0].health -ne "up" -or $minioTargets[0].scrapeUrl -notlike "https://*") {
  throw "MinIO HTTPS Prometheus target is not uniquely healthy"
}
$capacityBefore = Get-PrometheusMetric "minio_cluster_capacity_usable_total_bytes"
$freeBefore = Get-PrometheusMetric "minio_cluster_capacity_usable_free_bytes"
if ($capacityBefore -le 0 -or $freeBefore -le 0) { throw "MinIO capacity metrics are invalid before pressure smoke" }

$probeCode = @'
import concurrent.futures
import hashlib
import json
import os
import time
from runtime.object_storage import object_storage

prefix = os.environ["SMOKE_PREFIX"]
large_size = int(os.environ["SMOKE_LARGE_BYTES"])
concurrent_size = int(os.environ["SMOKE_CONCURRENT_BYTES"])
concurrent_count = int(os.environ["SMOKE_CONCURRENT_COUNT"])
version_count = int(os.environ["SMOKE_VERSION_COUNT"])
workers = int(os.environ["SMOKE_WORKERS"])

started = time.monotonic()
large_payload = b"L" * large_size
large = object_storage.put_bytes(f"{prefix}/large.bin", large_payload)
assert object_storage.get_bytes(large.bucket, large.key, large.sha256) == large_payload
large_seconds = time.monotonic() - started

def write_and_verify(index):
    payload = bytes([(index % 251) + 1]) * concurrent_size
    stored = object_storage.put_bytes(f"{prefix}/concurrent/{index:04d}.bin", payload)
    received = object_storage.get_bytes(stored.bucket, stored.key, stored.sha256)
    if received != payload:
        raise AssertionError(f"concurrent object mismatch: {index}")
    return stored.size_bytes

concurrent_started = time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
    sizes = list(executor.map(write_and_verify, range(concurrent_count)))
concurrent_seconds = time.monotonic() - concurrent_started
assert len(sizes) == concurrent_count and sum(sizes) == concurrent_count * concurrent_size

latest_payload = b""
for version in range(version_count):
    latest_payload = hashlib.sha256(f"version-{version}".encode()).digest() * 2048
    object_storage.put_bytes(f"{prefix}/versioned.bin", latest_payload)
latest = object_storage.get_bytes(object_storage.bucket, f"{prefix}/versioned.bin")
assert latest == latest_payload

print(json.dumps({
    "large_bytes": large_size,
    "large_seconds": round(large_seconds, 3),
    "concurrent_objects": concurrent_count,
    "concurrent_bytes": sum(sizes),
    "concurrent_seconds": round(concurrent_seconds, 3),
    "versions_written": version_count,
    "workers": workers,
    "elapsed_seconds": round(time.monotonic() - started, 3),
}, sort_keys=True))
'@

$rootAliasCode = @'
set -eu
mc alias set --insecure root https://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
'@

try {
  Invoke-ContainerPython $ExecutionContainer $probeCode @(
    "SMOKE_PREFIX=$prefix",
    "SMOKE_LARGE_BYTES=$($LargeObjectMiB * 1MB)",
    "SMOKE_CONCURRENT_BYTES=$($ConcurrentObjectMiB * 1MB)",
    "SMOKE_CONCURRENT_COUNT=$ConcurrentObjects",
    "SMOKE_VERSION_COUNT=$VersionCount",
    "SMOKE_WORKERS=$Workers"
  )
  if ($LASTEXITCODE -ne 0) { throw "Bounded MinIO pressure probe failed" }

  Invoke-ContainerShell $MinioContainer $rootAliasCode
  if ($LASTEXITCODE -ne 0) { throw "Root cleanup alias initialization failed" }

  $versionInventory = @(& docker exec $MinioContainer mc ls --insecure --versions "root/$bucket/$versionKey")
  if ($LASTEXITCODE -ne 0 -or $versionInventory.Count -ne $VersionCount) {
    throw "Expected $VersionCount physical versions; observed $($versionInventory.Count)"
  }
  $inventory = @(& docker exec $MinioContainer mc ls --insecure --recursive --versions "root/$bucket/$prefix/")
  if ($LASTEXITCODE -ne 0 -or $inventory.Count -lt ($ConcurrentObjects + $VersionCount + 1)) {
    throw "Pressure object inventory is incomplete"
  }

  $capacityAfter = Get-PrometheusMetric "minio_cluster_capacity_usable_total_bytes"
  $freeAfter = Get-PrometheusMetric "minio_cluster_capacity_usable_free_bytes"
  if ($capacityAfter -le 0 -or $freeAfter -le 0) { throw "MinIO capacity metrics are invalid after pressure smoke" }
  Write-Output "MINIO_BOUNDED_PRESSURE_PROBE_OK large_mib=$LargeObjectMiB concurrent_objects=$ConcurrentObjects concurrent_object_mib=$ConcurrentObjectMiB versions=$VersionCount workers=$Workers inventory_versions=$($inventory.Count)"
} finally {
  Invoke-ContainerShell $MinioContainer $rootAliasCode
  if ($LASTEXITCODE -ne 0) { throw "Root cleanup alias initialization failed" }
  & docker exec $MinioContainer mc rm --insecure --recursive --force --versions "root/$bucket/$prefix/" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Exact pressure prefix cleanup failed" }
  $remaining = @(& docker exec $MinioContainer mc ls --insecure --recursive --versions "root/$bucket/$prefix/")
  if ($LASTEXITCODE -ne 0 -or $remaining.Count -ne 0) { throw "Pressure prefix cleanup verification failed" }
}

Write-Output "MINIO_BOUNDED_PRESSURE_SMOKE_OK tls_target=up bounded_write_mib=$totalWriteMiB exact_cleanup=1"
