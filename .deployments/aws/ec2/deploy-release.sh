#!/usr/bin/env bash
# Run as ec2-user over SSH. No migrations, IAM changes or provisioning.
set -euo pipefail
sha=${1:?Set release SHA}
digest=${2:?Set expected archive SHA256}
[[ "$sha" =~ ^[a-f0-9]{40}$ && "$digest" =~ ^[a-f0-9]{64}$ ]] || exit 1
exec 9>"$HOME/.realestate-deploy.lock"
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
mountpoint -q /mnt/realestate || { echo 'STOP: EFS is not mounted'; exit 1; }
for path in user/media output/media legacy-media; do
    test -d "/mnt/realestate/workspace/$path"
done
config="$HOME/ec2"
for file in .env app.env global-bundle.pem; do
    test -s "$config/$file" || { echo "Configure $config/$file first"; exit 1; }
done
memory_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
(( memory_kib >= 3500000 )) || { echo 'STOP: use at least 4 GiB RAM for this configuration'; exit 1; }
docker info >/dev/null
archive="$HOME/realestate-$sha.tar.gz"
printf '%s  %s\n' "$digest" "$archive" | sha256sum --check --status
release="$HOME/realestate-releases/$sha"
test ! -e "$release" || { echo 'Release exists; use a new build or roll back explicitly'; exit 1; }
mkdir -p "$HOME/realestate-releases"
mkdir -m 700 "$release"
tar -xzf "$archive" -C "$release" --no-same-owner
test "$(cat "$release/release.txt")" = "$sha"
cd "$release/ec2"
for file in .env app.env global-bundle.pem; do cp "$config/$file" "$file"; done
chmod 600 .env app.env
python3 - "$sha" <<'PY'
import pathlib, sys
sha = sys.argv[1]
path = pathlib.Path(".env")
lines = [line for line in path.read_text().splitlines()
         if not line.startswith(("APPLICATION_IMAGE=", "GATEWAY_IMAGE="))]
lines += [f"APPLICATION_IMAGE=realestate-app:{sha}",
          f"GATEWAY_IMAGE=realestate-ec2-gateway:{sha}"]
path.write_text("\n".join(lines) + "\n")
PY
docker load -i "$release/images.tar"
docker compose config --quiet
# --check fails on pending migrations; deployment never applies them.
docker compose run --rm migrate python adminService/manage.py migrate --check \
    --settings=admin_service.settings
bash start.sh
set -a
source ./.env
set +a
status=$(curl --fail --retry 10 --retry-delay 3 --output /dev/null \
    --write-out '%{http_code}' "https://$DOMAIN/health/ready")
test "$status" = 200 || { echo "Public readiness failed: HTTP $status"; exit 1; }
ln -sfn "$release/ec2" "$HOME/realestate-current"
echo "Deployed $sha. Processing remains off."
echo 'Verify login, catalogue and authenticated media before launch.'
