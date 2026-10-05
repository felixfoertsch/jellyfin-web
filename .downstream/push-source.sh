#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GitHub Actions token is required}"
: "${EXPECTED_BASE:?Expected fork base SHA is required}"
# Keep credentials out of the checkout, Docker context, and build steps.
# Replace only the observed patch stack; concurrent pushes must win.
: "${EXPECTED_PATCH_QUEUE:?Expected patch-queue SHA is required}"
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
remote=$(git ls-remote origin refs/heads/main | cut -f1)
patch_queue=$(git ls-remote origin refs/heads/patch-queue | cut -f1)
if [[ "$remote" != "$EXPECTED_BASE" || "$patch_queue" != "$EXPECTED_PATCH_QUEUE" ]]; then
    echo 'main or patch-queue changed during this build; refusing stale publication.' >&2
    exit 1
fi
git push --force-with-lease="refs/heads/main:$EXPECTED_BASE" origin HEAD:refs/heads/main
