#!/usr/bin/env python3
"""Reject stale channel promotion and request an immediate catch-up run.

The dispatch is only a wake-up signal. Source is always resolved from the fixed
upstream repository, never from an event payload. Network errors fail closed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.request

from channels import outputs, resolve

UPSTREAM = 'https://github.com/jellyfin/jellyfin-web.git'
DESTINATION = 'felixfoertsch/jellyfin-web'
REF = re.compile(r'refs/(?:heads/master|tags/v?[0-9]+\.[0-9]+(?:\.[0-9]+)?)')
SHA = re.compile(r'[0-9a-f]{40}')


def remote_commit(ref: str) -> str:
    if not REF.fullmatch(ref):
        raise ValueError('Unsupported upstream ref.')
    result = subprocess.run(['git', 'ls-remote', '--exit-code', UPSTREAM, ref, ref + '^{}'],
                            check=True, capture_output=True, text=True, timeout=60)
    refs = {}
    for line in result.stdout.splitlines():
        sha, name = line.split('\t', 1)
        if name not in (ref, ref + '^{}') or not SHA.fullmatch(sha):
            raise ValueError('Unexpected upstream ref response.')
        refs[name] = sha
    # Annotated tags must be peeled to the commit, not compared as tag objects.
    if ref not in refs:
        raise ValueError('Upstream ref is missing.')
    return refs.get(ref + '^{}', refs[ref])


def check(channel: str, expected_ref: str, expected_sha: str) -> dict[str, str]:
    if not REF.fullmatch(expected_ref) or not SHA.fullmatch(expected_sha):
        raise ValueError('Expected a validated upstream ref and full SHA.')
    selected = resolve(channel)['upstream_ref']
    current_sha = remote_commit(selected)
    return {'current': str(selected == expected_ref and current_sha == expected_sha).lower(),
            'current_ref': selected, 'current_sha': current_sha}


def dispatch() -> None:
    # Do not allow a changed environment/payload to direct a write elsewhere.
    if os.environ.get('GITHUB_REPOSITORY') != DESTINATION:
        raise ValueError('Catch-up dispatch is restricted to the downstream repository.')
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('GH_TOKEN is required for catch-up dispatch.')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{DESTINATION}/dispatches',
        data=json.dumps({'event_type': 'upstream-updated'}).encode(),
        headers={'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json',
                 'X-GitHub-Api-Version': '2022-11-28',
                 'Authorization': f'Bearer {token}',
                 'User-Agent': 'jellyfin-web-downstream'}, method='POST')
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError(f'Catch-up dispatch returned HTTP {response.status}.')
    print('Requested a catch-up run because upstream changed during this run.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('channel', choices=['release', 'nightly'])
    parser.add_argument('--dispatch-if-changed', action='store_true')
    args = parser.parse_args()
    result = check(args.channel, os.environ['UPSTREAM_REF'], os.environ['UPSTREAM_SHA'])
    outputs(result)
    if result['current'] == 'false':
        print('::notice::Upstream moved; do not promote this stale candidate.')
        if args.dispatch_if_changed:
            dispatch()


if __name__ == '__main__':
    main()
