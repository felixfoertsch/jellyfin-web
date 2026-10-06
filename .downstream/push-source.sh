#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GitHub Actions token is required}"
: "${EXPECTED_BASE:?Expected fork base SHA is required}"
# Keep credentials out of the checkout, Docker context, and build steps.
# Never use --force: a concurrent human push must win over this sync.
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
remote=$(git ls-remote origin refs/heads/master | cut -f1)
if [[ "$remote" != "$EXPECTED_BASE" ]]; then
    echo 'master changed during this build; refusing to publish stale source or image.' >&2
    exit 1
fi
git push origin HEAD:refs/heads/master
