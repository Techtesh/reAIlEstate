#!/usr/bin/env bash
# Packages built images and an explicit allowlist of non-secret config.
set -euo pipefail
sha=${1:?Usage: package.sh COMMIT_SHA OUTPUT_DIRECTORY}
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full commit SHA'; exit 1; }
output=$(realpath -m "${2:?Set output directory}")
root=$(cd -- "$(dirname -- "$0")/../../.." && pwd)
mkdir -p "$output"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir "$stage/ec2"
for file in compose.yml Caddyfile compose.preview.yml deploy-release.sh .env.example app.env.example; do
    cp "$root/.deployments/aws/ec2/$file" "$stage/ec2/$file"
done
printf '%s\n' "$sha" > "$stage/release.txt"
docker save -o "$stage/images.tar" \
    "realestate-app:$sha" "realestate-ec2-gateway:$sha" caddy:2-alpine
archive="realestate-$sha.tar.gz"
tar -czf "$output/$archive" -C "$stage" images.tar ec2 release.txt
(cd "$output" && sha256sum "$archive" > "$archive.sha256")
echo "Created $output/$archive"
