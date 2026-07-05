#!/usr/bin/env bash
# Poll GitHub Actions for the latest push's CI run + report result. Use after
# `git push` when you want to know the CI outcome before moving on.
#
# Usage:
#   bash scripts/wait_for_ci.sh            # wait for the latest run on the current branch
#   bash scripts/wait_for_ci.sh --timeout 600
#
# Requires: gh CLI authenticated to dblagbro/rebooter-droids.
#
# Ported from devingpt after the 2026-07-05 v0.6.56→v0.6.57 4-day-red-CI
# incident where a push wasn't checked and CI sat red across two commits.
# The release script (tools/cut-rebooter-release.sh) now blocks on this
# before tagging/pushing images so red CI cannot deploy.
set -euo pipefail

TIMEOUT="${TIMEOUT:-600}"
while [ "${1:-}" != "" ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

REPO="dblagbro/rebooter-droids"
BRANCH=$(git branch --show-current)
HEAD_SHA=$(git rev-parse HEAD)
echo "wait_for_ci: watching CI on branch $BRANCH @ ${HEAD_SHA:0:7} (timeout ${TIMEOUT}s) …"

# GitHub can take ~5-30s to register a push. Poll for our SHA (not just the
# latest run on branch — that risks matching a previous push's run) and
# retry until it shows up.
RUN_ID=""
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  sleep 5
  RUN_ID=$(gh run list --repo "$REPO" --workflow CI --limit 15 \
           --json databaseId,headSha \
           --jq ".[] | select(.headSha == \"$HEAD_SHA\") | .databaseId" 2>/dev/null | head -1)
  if [ -n "$RUN_ID" ]; then break; fi
  echo "wait_for_ci: not registered yet (attempt $attempt/12)…"
done
if [ -z "$RUN_ID" ]; then
  echo "wait_for_ci: no CI run found for ${HEAD_SHA:0:7} after 60s — check the Actions page manually"
  exit 2
fi
echo "wait_for_ci: watching run $RUN_ID  (https://github.com/$REPO/actions/runs/$RUN_ID)"

if gh run watch "$RUN_ID" --repo "$REPO" --exit-status --interval 10 2>&1 | tail -20; then
  echo
  echo "wait_for_ci: PASS ✓"
  exit 0
else
  echo
  echo "wait_for_ci: FAIL ✗  → gh run view $RUN_ID --repo $REPO --log-failed"
  exit 1
fi
