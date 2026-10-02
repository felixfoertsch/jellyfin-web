#!/usr/bin/env python3
"""Resolve upstream channels and plan safe, independently tagged image publication."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import urllib.request

IMAGE = 'ghcr.io/felixfoertsch/jellyfin-web'
RELEASE_API = 'https://api.github.com/repos/jellyfin/jellyfin-web/releases/latest'
VERSION = re.compile(r'v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)')
SHA = re.compile(r'[0-9a-f]{40}')


def release_info(data: dict) -> dict[str, str]:
    """Use the explicitly published stable release, never a guessed highest tag."""
    if data.get('draft') is not False or data.get('prerelease') is not False:
        raise ValueError('Upstream latest must be a published non-prerelease.')
    tag = data.get('tag_name', '')
    match = VERSION.fullmatch(tag) if isinstance(tag, str) else None
    if not match or not data.get('published_at'):
        raise ValueError('Expected a published numeric release tag such as v12.1 or v10.11.8.')
    return {'upstream_ref': f'refs/tags/{tag}', 'version': match.group(1)}


def resolve(channel: str) -> dict[str, str]:
    if channel == 'nightly':
        return {'upstream_ref': 'refs/heads/master', 'version': 'nightly'}
    if channel != 'release':
        raise ValueError('Unknown channel.')
    headers = {'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'jellyfin-web-downstream'}
    if token := os.environ.get('GH_TOKEN'):
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(RELEASE_API, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return release_info(json.load(response))


def aliases(channel: str, version: str) -> list[str]:
    if channel == 'release':
        if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', version):
            raise ValueError('Unsafe release version.')
        return ['latest', 'stable', version]
    if channel == 'nightly' and version == 'nightly':
        return ['nightly', 'edge']
    raise ValueError('Unknown or inconsistent channel.')


def inspect_digest(image: str) -> str | None:
    """Check the complete multi-platform index; any error means retry the build."""
    try:
        result = subprocess.run(
            ['docker', 'buildx', 'imagetools', 'inspect', image,
             '--format', '{{json .Manifest}}'],
            check=True, capture_output=True, text=True, timeout=90)
        manifest = json.loads(result.stdout)
        platforms = {(item.get('platform', {}).get('os'),
                      item.get('platform', {}).get('architecture'))
                     for item in manifest.get('manifests', [])}
        digest = manifest.get('digest', '')
        if {('linux', 'amd64'), ('linux', 'arm64')} <= platforms and re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            return digest
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def already_published(marker: str, image_aliases: list[str], inspect=inspect_digest) -> bool:
    expected = inspect(f'{IMAGE}:{marker}')
    return bool(expected) and all(inspect(f'{IMAGE}:{alias}') == expected for alias in image_aliases)


def plan(channel: str, version: str, source: str, tree: str, run_id: str,
         attempt: str, force: bool = False, now: datetime | None = None,
         inspect=inspect_digest) -> dict[str, str]:
    if not SHA.fullmatch(source) or not SHA.fullmatch(tree):
        raise ValueError('Expected full source and source-tree SHA values.')
    if not re.fullmatch(r'[0-9]+', run_id) or not re.fullmatch(r'[0-9]+', attempt):
        raise ValueError('Expected numeric workflow run and attempt IDs.')
    image_aliases = aliases(channel, version)
    # Source tree, rather than commit parent/time, avoids rebuilding releases just
    # because nightly master advanced. Refresh base images at least weekly.
    year, week, _ = (now or datetime.now(timezone.utc)).isocalendar()
    marker = f'{channel}-tree-{tree}-{year}w{week:02d}'
    build_tag = f'{channel}-sha-{source}-run-{run_id}-{attempt}'
    build = force or not already_published(marker, image_aliases, inspect)
    return {
        'build': str(build).lower(),
        'tags': '\n'.join(f'{IMAGE}:{tag}' for tag in [*image_aliases, marker, build_tag]),
        'image': f'{IMAGE}:{image_aliases[0]}',
        'build_tag': build_tag,
        'source_tag': f'downstream-release-v{version}-{source}' if channel == 'release' else '',
    }


def outputs(values: dict[str, str]) -> None:
    text = ''
    for key, value in values.items():
        print(f'{key}={value}')
        text += f'{key}<<DOWNSTREAM_VALUE\n{value}\nDOWNSTREAM_VALUE\n'
    if path := os.environ.get('GITHUB_OUTPUT'):
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['resolve', 'plan'])
    parser.add_argument('channel', choices=['release', 'nightly'])
    args = parser.parse_args()
    if args.command == 'resolve':
        outputs(resolve(args.channel))
    else:
        outputs(plan(args.channel, os.environ['UPSTREAM_VERSION'],
                     os.environ['SOURCE_SHA'], os.environ['SOURCE_TREE'],
                     os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'],
                     os.environ.get('FORCE_REBUILD') == 'true'))


if __name__ == '__main__':
    main()
