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

# Batch A QA hygiene (2026-06-13) — BUG-061: any commit between the
# last tag and HEAD that references BUG-NNN in its message must touch
# docs/bug-log.md so the status doesn't go stale. Soft warn (don't
# fail the release) — the operator decides when a status update is
# worth blocking on. To bypass entirely set ALLOW_STALE_BUGLOG=1.
if [ "${ALLOW_STALE_BUGLOG:-0}" != "1" ]; then
  LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo '')"
  RANGE="${LAST_TAG:+${LAST_TAG}..HEAD}"
  if [ -n "${RANGE}" ]; then
    # Commits that reference BUG-NNN
    BUG_TOUCHING="$(git log --pretty=%H "${RANGE}" -- ':!docs/bug-log.md' | while read -r sha; do
      msg="$(git log -1 --pretty=%B "$sha")"
      if echo "$msg" | grep -qE 'BUG-[0-9]{3}'; then
        # Did THIS commit also touch bug-log.md?
        if ! git show --pretty='' --name-only "$sha" | grep -q 'docs/bug-log.md'; then
          echo "$sha $(echo "$msg" | head -1 | cut -c1-80)"
        fi
      fi
    done)"
    if [ -n "${BUG_TOUCHING}" ]; then
      echo "→ WARN: commits since ${LAST_TAG} reference BUG-NNN without updating docs/bug-log.md:" >&2
      echo "${BUG_TOUCHING}" | sed 's/^/    /' >&2
      echo "    (set ALLOW_STALE_BUGLOG=1 to skip this check)" >&2
    fi
  fi
fi

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
