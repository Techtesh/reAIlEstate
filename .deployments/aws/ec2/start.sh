#!/usr/bin/env bash
# Run on EC2. Does not migrate or start processing.
set -euo pipefail
cd -- "$(dirname -- "$0")"
set -a
source ./.env
set +a
mountpoint -q /mnt/realestate || { echo 'STOP: EFS is not mounted'; exit 1; }
for directory in user/media output/media legacy-media; do
    test -d "${WORKSPACE_PATH:-/mnt/realestate/workspace}/$directory" || {
        echo "STOP: missing restored directory $directory"; exit 1;
    }
done
test -s global-bundle.pem
test -s app.env
if grep -q 'replace-with' app.env .env; then
    echo 'STOP: replace example settings first'; exit 1
fi
chmod 600 app.env .env
docker compose config --quiet
# A previous deployment might have enabled the optional worker.
docker compose --profile processing stop worker
docker compose up -d admin builder buyer gateway https
status=$(curl --fail --retry 20 --retry-delay 3 --retry-connrefused \
    --output /dev/null --write-out '%{http_code}' \
    -H 'X-Forwarded-Client-Proto: https' http://127.0.0.1:8080/health/ready)
test "$status" = 200 || { echo "Readiness failed: HTTP $status"; exit 1; }
echo 'Database readiness: HTTP 200. Check the public HTTPS URL next.'
