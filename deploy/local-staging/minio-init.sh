#!/bin/sh
# The bucket, private, and the one storage identity the application uses.
#
# **The application never holds the root pair.** In production the API and
# the worker get an IAM role that can read and write objects in one bucket
# and do nothing else (docs/07-security.md §5.3). Here that role is a MinIO
# user with the policy beside this file, and `checks.py` proves what it
# cannot do: see or touch another bucket, delete this one, or change its policy.
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing local/zipzop-media
mc anonymous set none local/zipzop-media

# A second bucket the application identity must never see, so the checks
# can prove the scope by trying (`checks.py storage_identity_is_scoped`).
mc mb --ignore-existing local/zipzop-other
echo "only the root identity reads this" | mc pipe local/zipzop-other/canary.txt >/dev/null

mc admin policy create local zipzop-app /init/app-policy.json
mc admin user add local "$S3_ACCESS_KEY_ID" "$S3_SECRET_ACCESS_KEY"
mc admin policy attach local zipzop-app --user "$S3_ACCESS_KEY_ID" 2>/dev/null || true

echo "bucket ready: private, application identity scoped to zipzop-media"
