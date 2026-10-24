#!/usr/bin/env bash
# Offline packaging check: Docker is a shell mock.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
scratch=$(mktemp -d)
docker() {
    test "$1" = save
    test "$2" = -o
    printf 'mock-image-archive\n' > "$3"
}
export -f docker
sha=1111111111111111111111111111111111111111
bash "$script_dir/package.sh" "$sha" "$scratch"
archive="$scratch/realestate-$sha.tar.gz"
(cd "$scratch" && sha256sum --check "$(basename "$archive").sha256")
listing=$(tar -tzf "$archive")
for expected in images.tar ec2/compose.yml ec2/compose.preview.yml ec2/deploy-release.sh ec2/.env.example ec2/app.env.example release.txt; do
    grep -Fxq "$expected" <<< "$listing"
done
if grep -Eq '^ec2/(\.env|app\.env|global-bundle\.pem)$' <<< "$listing"; then
    echo 'Secret material included in release'; exit 1
fi
test "$(tar -xOf "$archive" release.txt)" = "$sha"
if bash "$script_dir/package.sh" invalid "$scratch" >/dev/null 2>&1; then
    echo 'Invalid SHA accepted'; exit 1
fi
echo 'Release packaging checks passed (mock Docker; no deployment).'
