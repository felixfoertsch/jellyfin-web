#!/usr/bin/env python3
"""Reconstruct upstream plus reviewed patches, preserving upstream Git ancestry.

Only .downstream/ and .github/workflows/ are maintained directly in this fork.
Run in a clean checkout. This creates a LOCAL commit; the workflow publishes it
only after validation. Conflicts leave HEAD and the checkout unchanged.
Upstream workflows are retained in history, but never in the resulting CI tree.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(['git', '-C', str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = git(root, 'merge-base', '--is-ancestor', ancestor, descendant, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'Cannot verify Git ancestry:\n{result.stderr}')
    return result.returncode == 0


def sync(root: Path, upstream_url: str | None = None, upstream_ref: str | None = None) -> dict[str, str]:
    if git(root, 'status', '--porcelain').stdout.strip():
        raise RuntimeError('Refusing to replace a dirty checkout; commit or stash changes first.')
    base = git(root, 'rev-parse', 'HEAD').stdout.strip()
    config = json.loads((root / '.downstream/upstream.json').read_text())
    url = upstream_url or config['repository']
    ref = upstream_ref or config['ref']
    git(root, 'fetch', '--no-tags', url, ref)
    upstream = git(root, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    if git(root, 'ls-tree', upstream, '--', '.downstream').stdout.strip():
        raise RuntimeError('Upstream now owns .downstream; reconcile this reserved path manually.')
    source = base
    # Stable metadata makes unchanged replay produce identical commit IDs.
    timestamp = git(root, 'show', '-s', '--format=%cI', upstream).stdout.strip()
    commit_env = {**os.environ, 'GIT_AUTHOR_DATE': timestamp, 'GIT_COMMITTER_DATE': timestamp}

    def record(candidate: Path, message: str) -> str:
        result = subprocess.run(
            ['git', '-C', str(candidate), '-c', 'commit.gpgsign=false',
             '-c', 'user.name=github-actions[bot]',
             '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
             'commit', '-m', message], env=commit_env, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f'Cannot record patch stack:\n{result.stderr}')
        return git(candidate, 'rev-parse', 'HEAD').stdout.strip()
    with tempfile.TemporaryDirectory(prefix='jellyfin-web-sync-') as directory:
        candidate = Path(directory) / 'candidate'
        git(root, 'worktree', 'add', '--detach', str(candidate), upstream)
        try:
            # Keep our trusted CI. Do not take an automatic merge's file result:
            # source MUST be freshly reconstructed upstream + the entire queue.
            git(candidate, 'rm', '-r', '--ignore-unmatch', '.github/workflows')
            git(candidate, 'restore', '--source=' + base, '--staged', '--worktree',
                '--', '.downstream')
            config['ref'] = ref
            config['commit'] = upstream
            (candidate / '.downstream/upstream.json').write_text(json.dumps(config, indent=2) + '\n')
            readme = candidate / 'README.md'
            upstream_readme = readme.read_text() if readme.exists() else ''
            patch_items: list[str] = []

            def update_readme() -> None:
                readme.write_text('This fork follows upstream [Jellyfin Web](https://github.com/jellyfin/jellyfin-web) and applies patches below in order. `automation` owns patches and workflows; generated `main` contains upstream source plus these patches. Nightly builds follow upstream default branch; stable builds follow upstream releases.\n\n'
                                  '# Patched Jellyfin Web\n\n'
                                  'Applied patches, oldest first:\n\n'
                                  + ''.join(patch_items) + '\n---\n\n' + upstream_readme)
                git(candidate, 'add', '--', 'README.md')

            update_readme()
            git(candidate, 'add', '--all')
            source = record(candidate, 'Maintain downstream tooling and publication workflow')
            for entry in (candidate / '.downstream/series').read_text().splitlines():
                name = entry.split('#', 1)[0].strip()
                if not name:
                    continue
                if Path(name).name != name or not name.endswith('.patch'):
                    raise RuntimeError(f'Invalid patch filename: {name!r}')
                patch = candidate / '.downstream/patches' / name
                if not patch.is_file() or patch.is_symlink():
                    raise RuntimeError(f'Missing or unsafe patch: {name}')
                forward = git(candidate, 'apply', '--index', '--check', str(patch), check=False)
                if forward.returncode == 0:
                    git(candidate, 'apply', '--index', '--whitespace=error-all', str(patch))
                    subject = next((line.removeprefix('Subject: ').strip()
                                    for line in patch.read_text().splitlines()
                                    if line.startswith('Subject: ')), name)
                    patch_items.append(f'{len(patch_items) + 1}. [{subject}](https://github.com/felixfoertsch/jellyfin-web/blob/automation/.downstream/patches/{name})\n')
                    update_readme()
                    source = record(candidate, f'Apply downstream patch: {name}')
                    print(f'Applied {name}', flush=True)
                elif git(candidate, 'apply', '--index', '--reverse', '--check', str(patch), check=False).returncode == 0:
                    print(f'Already present upstream: {name}', flush=True)
                else:
                    raise RuntimeError(f'Patch no longer applies: {name}\n{forward.stderr}'
                                       'No source branch or image was published.')
            if not is_ancestor(root, upstream, source):
                raise RuntimeError('Refusing source that loses upstream ancestry.')
        finally:
            git(root, 'worktree', 'remove', '--force', str(candidate))
    if source != base:
        git(root, 'reset', '--hard', source)
    return {'base_sha': base, 'upstream_sha': upstream, 'source_sha': source,
            'source_tree': git(root, 'rev-parse', source + '^{tree}').stdout.strip()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-url', help='Override for local integration tests.')
    parser.add_argument('--upstream-ref', help='Explicit upstream branch or release-tag ref.')
    args = parser.parse_args()
    root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').stdout.strip())
    try:
        result = sync(root, args.upstream_url, args.upstream_ref)
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    for key, value in result.items():
        print(f'{key}={value}')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.writelines(f'{key}={value}\n' for key, value in result.items())
