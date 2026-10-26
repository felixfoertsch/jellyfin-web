#!/bin/sh
set -eu
# HTTP backend host:port only. TLS belongs at the public reverse proxy.
# Deliberately reject paths, credentials, whitespace, and nginx directives.
if ! printf '%s\n' "${JELLYFIN_SERVER:-}" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*:[0-9]{1,5}$'; then
    echo 'JELLYFIN_SERVER must be a backend hostname:port, e.g. jellyfin:8096 (no scheme or path).' >&2
    exit 1
fi
port=${JELLYFIN_SERVER##*:}
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo 'JELLYFIN_SERVER port must be between 1 and 65535.' >&2
    exit 1
fi
