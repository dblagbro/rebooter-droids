#!/usr/bin/env bash
# Cut a new rebooter-droids release.
#
# Usage: tools/cut-rebooter-release.sh <new-version>
#
# This script (in order):
#   1. Bumps version in pyproject.toml + app/version.py
#   2. Updates CHANGELOG.md placeholder
#   3. Commits + tags v<version>
#   4. Pushes the branch and the tag
#   5. Creates a GitHub release with the changelog excerpt
#   6. Builds and pushes the Docker image (<version> + latest)
#   7. Force-recreates the local container
#   8. Curls the prod /api/v1/version to confirm
#
# Aborts on any step failure.

set -euo pipefail

NEW_VERSION="${1:-}"
if [ -z "$NEW_VERSION" ]; then
  echo "usage: $0 <new-version>" >&2
  exit 1
fi

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CURRENT="$(grep '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
echo "→ Bumping ${CURRENT} → ${NEW_VERSION}"

sed -i -E "s/^version = \"${CURRENT}\"$/version = \"${NEW_VERSION}\"/" pyproject.toml
sed -i -E "s/^__version__ = \"${CURRENT}\"$/__version__ = \"${NEW_VERSION}\"/" app/version.py

if ! grep -q "^## \[${NEW_VERSION}\]" CHANGELOG.md; then
  TODAY="$(date -u +%F)"
  awk -v v="$NEW_VERSION" -v d="$TODAY" '
    /^## \[Unreleased\]$/ { print; print ""; print "## [" v "] - " d; next }
    { print }
  ' CHANGELOG.md > CHANGELOG.md.new
  mv CHANGELOG.md.new CHANGELOG.md
fi

git add pyproject.toml app/version.py CHANGELOG.md
git commit -m "chore: release v${NEW_VERSION}"
git tag -a "v${NEW_VERSION}" -m "v${NEW_VERSION}"
git push origin main
git push origin "v${NEW_VERSION}"

NOTES="$(awk -v v="$NEW_VERSION" '
  $0 ~ "^## \\[" v "\\]" { capture=1; next }
  capture && /^## \[/ { exit }
  capture { print }
' CHANGELOG.md)"

gh release create "v${NEW_VERSION}" \
  --title "v${NEW_VERSION}" \
  --notes "${NOTES:-Release v${NEW_VERSION}}"

echo "→ Building + pushing image"
sudo docker buildx build --push \
  -t "dblagbro/rebooter-droids:${NEW_VERSION}" \
  -t "dblagbro/rebooter-droids:latest" \
  "$REPO_ROOT"

echo "→ Recreating local container"
sudo docker compose -f /home/dblagbro/docker/docker-compose.yml \
  --env-file /home/dblagbro/docker/.env \
  up -d --force-recreate --no-deps rebooter-droids

echo "→ Verifying prod"
sleep 3
RESP="$(curl -fsS https://www.voipguru.org/rebooter/api/v1/version)"
echo "$RESP"
echo "$RESP" | grep -q "\"version\":\"${NEW_VERSION}\""
echo "✓ released v${NEW_VERSION}"
