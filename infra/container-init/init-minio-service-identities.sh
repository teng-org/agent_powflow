#!/bin/sh
set -eu
: "${MINIO_ENDPOINT:?MINIO_ENDPOINT is required}"
: "${MINIO_ROOT_ACCESS_KEY:?MINIO_ROOT_ACCESS_KEY is required}"
: "${MINIO_ROOT_SECRET_KEY:?MINIO_ROOT_SECRET_KEY is required}"
: "${MINIO_APP_ACCESS_KEY:?MINIO_APP_ACCESS_KEY is required}"
: "${MINIO_APP_SECRET_KEY:?MINIO_APP_SECRET_KEY is required}"
: "${MINIO_GOVERNANCE_ACCESS_KEY:?MINIO_GOVERNANCE_ACCESS_KEY is required}"
: "${MINIO_GOVERNANCE_SECRET_KEY:?MINIO_GOVERNANCE_SECRET_KEY is required}"
: "${MINIO_DOCUMENT_BUCKET:?MINIO_DOCUMENT_BUCKET is required}"
scheme="${MINIO_SCHEME:-http}"
case "$scheme" in http|https) ;; *) echo "MINIO_IDENTITY_INIT_FAILED reason=invalid_scheme" >&2; exit 1;; esac
case "$MINIO_DOCUMENT_BUCKET" in *[!a-z0-9.-]*|.*|*.) echo "MINIO_IDENTITY_INIT_FAILED reason=invalid_bucket" >&2; exit 1;; esac
root_alias="agent-powflow-root"; app_alias="agent-powflow-app"; attempt=0
until mc alias set "$root_alias" "${scheme}://${MINIO_ENDPOINT}" "$MINIO_ROOT_ACCESS_KEY" "$MINIO_ROOT_SECRET_KEY" >/dev/null 2>&1; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || { echo "MINIO_IDENTITY_INIT_FAILED reason=minio_unavailable" >&2; exit 1; }; sleep 2
done
mc mb --ignore-existing "$root_alias/$MINIO_DOCUMENT_BUCKET" >/dev/null
umask 077; app_policy="/tmp/apf-app-policy.json"; governance_policy="/tmp/apf-governance-policy.json"
printf '%s\n' '{' '"Version":"2012-10-17",' '"Statement":[' \
  '{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketVersions","s3:ListBucketMultipartUploads","s3:GetBucketVersioning","s3:GetLifecycleConfiguration"],"Resource":["arn:aws:s3:::'"$MINIO_DOCUMENT_BUCKET"'"]},' \
  '{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:DeleteObjectVersion","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::'"$MINIO_DOCUMENT_BUCKET"'/*"]}' ']}' > "$app_policy"
printf '%s\n' '{' '"Version":"2012-10-17",' '"Statement":[' \
  '{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:GetBucketVersioning","s3:PutBucketVersioning","s3:GetLifecycleConfiguration","s3:PutLifecycleConfiguration"],"Resource":["arn:aws:s3:::'"$MINIO_DOCUMENT_BUCKET"'"]}' ']}' > "$governance_policy"
mc admin policy create "$root_alias" agent-powflow-document-app "$app_policy" >/dev/null
mc admin policy create "$root_alias" agent-powflow-document-governance "$governance_policy" >/dev/null
mc admin user add "$root_alias" "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY" >/dev/null
mc admin user add "$root_alias" "$MINIO_GOVERNANCE_ACCESS_KEY" "$MINIO_GOVERNANCE_SECRET_KEY" >/dev/null
mc admin policy attach "$root_alias" agent-powflow-document-app --user "$MINIO_APP_ACCESS_KEY" >/dev/null
mc admin policy attach "$root_alias" agent-powflow-document-governance --user "$MINIO_GOVERNANCE_ACCESS_KEY" >/dev/null
mc alias set "$app_alias" "${scheme}://${MINIO_ENDPOINT}" "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY" >/dev/null
mc stat "$app_alias/$MINIO_DOCUMENT_BUCKET" >/dev/null
if mc admin info "$app_alias" >/dev/null 2>&1; then echo "MINIO_IDENTITY_INIT_FAILED reason=app_identity_has_admin_access" >&2; exit 1; fi
rm -f "$app_policy" "$governance_policy"
echo "MINIO_SERVICE_IDENTITIES_OK"
