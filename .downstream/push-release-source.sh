#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GitHub Actions token is required}"
: "${SOURCE_TAG:?Release source tag is required}"
if [[ ! "$SOURCE_TAG" =~ ^v?[0-9]+\.[0-9]+(\.[0-9]+)?-[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[1-9][0-9]*$ ]]; then
    echo 'Invalid release source tag.' >&2
    exit 1
fi
# Creating tag without force reserves identity. A collision fails closed before
# container aliases move; never replace an existing publication.
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
if [[ -n "$(git ls-remote origin "refs/tags/$SOURCE_TAG")" ]]; then
    echo 'Release identity already exists; refusing immutable publication collision.' >&2
    exit 1
fi
git push --force-with-lease="refs/tags/$SOURCE_TAG:" origin "HEAD:refs/tags/$SOURCE_TAG"
