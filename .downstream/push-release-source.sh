#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GitHub Actions token is required}"
: "${SOURCE_TAG:?Release source tag is required}"
if [[ ! "$SOURCE_TAG" =~ ^downstream-release-v[0-9]+\.[0-9]+(\.[0-9]+)?-[0-9a-f]{40}$ ]]; then
    echo 'Invalid release source tag.' >&2
    exit 1
fi
if [[ "${SOURCE_TAG##*-}" != "$(git rev-parse HEAD)" ]]; then
    echo 'Release source tag does not match the current commit.' >&2
    exit 1
fi
# Preserve corresponding source without ever replacing master with release code.
# This snapshot has the same workflow files as its fork-master parent.
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
git push origin "HEAD:refs/tags/$SOURCE_TAG"
