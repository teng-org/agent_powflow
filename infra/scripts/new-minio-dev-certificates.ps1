param(
  [string]$OutputDirectory = ".local/minio-certs",
  [int]$ValidDays = 825,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$target = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
  [IO.Path]::GetFullPath($OutputDirectory)
} else {
  [IO.Path]::GetFullPath((Join-Path $workspaceRoot $OutputDirectory))
}
$workspacePrefix = $workspaceRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $target.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw "OutputDirectory must stay inside workspace: $workspaceRoot"
}
if ($ValidDays -lt 1 -or $ValidDays -gt 825) { throw "ValidDays must be between 1 and 825" }

$required = @("ca.crt", "ca.key", "server.crt", "server.key")
if ((Test-Path -LiteralPath $target) -and -not $Force) {
  $existing = @($required | Where-Object { Test-Path -LiteralPath (Join-Path $target $_) })
  if ($existing.Count -gt 0) { throw "Refusing to overwrite existing MinIO certificates; use -Force after verifying the target" }
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
$configPath = Join-Path $target "server-openssl.cnf"
$config = @"
[req]
prompt = no
distinguished_name = dn
req_extensions = req_ext
[dn]
CN = minio
[req_ext]
subjectAltName = @alt_names
[alt_names]
DNS.1 = minio
DNS.2 = localhost
IP.1 = 127.0.0.1
"@
Set-Content -LiteralPath $configPath -Value $config -Encoding ASCII

& openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out (Join-Path $target "ca.key")
if ($LASTEXITCODE -ne 0) { throw "failed to generate MinIO development CA key" }
& openssl req -x509 -new -sha256 -key (Join-Path $target "ca.key") -subj "/CN=Agent Powflow MinIO Development CA" -days $ValidDays -out (Join-Path $target "ca.crt")
if ($LASTEXITCODE -ne 0) { throw "failed to generate MinIO development CA certificate" }
& openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out (Join-Path $target "server.key")
if ($LASTEXITCODE -ne 0) { throw "failed to generate MinIO server key" }
& openssl req -new -key (Join-Path $target "server.key") -config $configPath -out (Join-Path $target "server.csr")
if ($LASTEXITCODE -ne 0) { throw "failed to generate MinIO server CSR" }
& openssl x509 -req -sha256 -in (Join-Path $target "server.csr") -CA (Join-Path $target "ca.crt") -CAkey (Join-Path $target "ca.key") -CAcreateserial -days $ValidDays -extensions req_ext -extfile $configPath -out (Join-Path $target "server.crt")
if ($LASTEXITCODE -ne 0) { throw "failed to sign MinIO server certificate" }
& openssl verify -CAfile (Join-Path $target "ca.crt") (Join-Path $target "server.crt")
if ($LASTEXITCODE -ne 0) { throw "generated MinIO certificate failed verification" }

foreach ($name in @("server.csr", "server-openssl.cnf", "ca.srl")) {
  $path = Join-Path $target $name
  if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
Write-Output "MINIO_DEV_CERTIFICATES_OK path=$target valid_days=$ValidDays sans=minio,localhost,127.0.0.1"
