#!/usr/bin/env bash
# GitHub Actions target-side helper. No cloud CLI, provisioning or migrations.
set -euo pipefail
sha=${1:?Set release SHA}
digest=${2:?Set archive SHA256}
mode=${3:?Set preview or https}
demo=${4:-false}
[[ "$sha" =~ ^[a-f0-9]{40}$ && "$digest" =~ ^[a-f0-9]{64}$ ]] || exit 1
[[ "$mode" == preview || "$mode" == https ]] || exit 1
[[ "$demo" == true || "$demo" == false ]] || exit 1
exec 9>"$HOME/.realestate-deploy.lock"
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
unset APPLICATION_IMAGE GATEWAY_IMAGE DOMAIN WORKSPACE_PATH
config="$HOME/ec2"
for file in .env app.env global-bundle.pem; do
    test -s "$config/$file" || { echo "Configure $config/$file first"; exit 1; }
done
if grep -q 'replace-with' "$config/app.env" "$config/.env"; then
    echo 'Runtime configuration still contains example values'; exit 1
fi
# Read only the non-secret substitutions; never execute .env as shell code.
configuration=$(python3 - "$config/.env" <<'PYENV'
from pathlib import Path
import sys
values = dict(line.split("=", 1) for line in Path(sys.argv[1]).read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#") and "=" in line)
workspace = values.get("WORKSPACE_PATH", "/mnt/realestate/workspace")
domain = values.get("DOMAIN", "")
assert workspace.startswith("/") and "\n" not in workspace
print(workspace)
print(domain)
PYENV
)
mapfile -t settings <<< "$configuration"
workspace=${settings[0]}
domain=${settings[1]:-}
fstype=$(findmnt -n -T "$workspace" -o FSTYPE)
[[ "$fstype" == nfs || "$fstype" == nfs4 ]] || { echo 'Persistent NFS workspace is not mounted'; exit 1; }
for path in user/media output/media legacy-media; do test -d "$workspace/$path"; done
if [[ "$mode" == https ]]; then
    [[ "$domain" =~ ^[A-Za-z0-9][A-Za-z0-9.-]+$ && "$domain" != localhost && "$domain" != *replace-with* ]] || {
        echo 'Configure the public domain before HTTPS deployment'; exit 1;
    }
fi
memory_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
(( memory_kib >= 3500000 )) || { echo 'At least 4 GiB RAM is required'; exit 1; }
docker info >/dev/null
archive="$HOME/realestate-$sha.tar.gz"
printf '%s  %s\n' "$digest" "$archive" | sha256sum --check --status
mkdir -p "$HOME/realestate-releases"
release=$(mktemp -d "$HOME/realestate-releases/$sha.XXXXXX")
chmod 700 "$release"
tar -xzf "$archive" -C "$release" --no-same-owner
test "$(cat "$release/release.txt")" = "$sha"
cd "$release/ec2"
for file in .env app.env global-bundle.pem; do cp "$config/$file" "$file"; done
chmod 600 .env app.env
python3 - "$sha" <<'PYIMAGES'
import pathlib, sys
path = pathlib.Path(".env")
lines = [line for line in path.read_text().splitlines()
         if not line.startswith(("APPLICATION_IMAGE=", "GATEWAY_IMAGE="))]
lines += [f"APPLICATION_IMAGE=realestate-app:{sys.argv[1]}",
          f"GATEWAY_IMAGE=realestate-ec2-gateway:{sys.argv[1]}"]
path.write_text("\n".join(lines) + "\n")
PYIMAGES
# Demo visibility is independent of transport; it does not approve source records.
python3 - "$demo" <<'PYDEMO'
from pathlib import Path
import sys
path = Path("app.env")
lines = [line for line in path.read_text().splitlines()
         if line.partition("=")[0].strip() != "PROJECT_PUBLIC_PREVIEW_ENABLED"]
lines.append(f"PROJECT_PUBLIC_PREVIEW_ENABLED={sys.argv[1]}")
path.write_text("\n".join(lines) + "\n")
PYDEMO

docker load -i "$release/images.tar"
compose=(docker compose -f compose.yml)
if [[ "$mode" == preview ]]; then compose+=(-f compose.preview.yml); fi
"${compose[@]}" config --quiet
# Pending migrations fail before web replacement. Snapshot/review them separately.
"${compose[@]}" run --rm migrate python adminService/manage.py migrate --check --settings=admin_service.settings
"${compose[@]}" --profile processing stop worker
if [[ "$mode" == preview ]]; then "${compose[@]}" stop https; fi
services=(admin builder buyer gateway)
if [[ "$mode" == https ]]; then services+=(https); fi
"${compose[@]}" up -d "${services[@]}"
headers=()
if [[ "$mode" == https ]]; then headers=(-H 'X-Forwarded-Client-Proto: https'); fi
for path in /health/ready / /builder/login /admin/login/; do
    curl --fail --silent --show-error --location --retry 20 --retry-delay 3 --retry-connrefused \
        --max-time 30 --output /dev/null "${headers[@]}" "http://127.0.0.1:8080$path"
done
if [[ "$mode" == https ]]; then
    curl --fail --silent --show-error --retry 10 --retry-delay 3 --max-time 30 \
        --output /dev/null "https://$domain/health/ready"
fi
printf '%s\n' "$mode" > .deployment-mode
ln -sfn "$release/ec2" "$HOME/realestate-current"
echo "Deployed $sha in $mode mode. Processing remains off."
