#!/usr/bin/env python3
"""Build a clean upstream snapshot plus a reviewed patch queue. Never force-push.

Only .downstream/ and .github/workflows/ are maintained directly in this fork.
Run in a clean checkout. This creates a LOCAL commit; the workflow publishes it
only after the build and smoke tests pass. Failed patch application leaves HEAD
and the checkout unchanged. No upstream CI workflow is imported or executed.
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
    with tempfile.TemporaryDirectory(prefix='jellyfin-web-sync-') as directory:
        candidate = Path(directory) / 'candidate'
        git(root, 'worktree', 'add', '--detach', str(candidate), upstream)
        try:
            # Keep the fork's trusted CI, not upstream deployment workflows.
            git(candidate, 'rm', '-r', '--ignore-unmatch', '.github/workflows')
            git(candidate, 'restore', '--source=' + base, '--staged', '--worktree',
                '--', '.downstream', '.github/workflows')
            series = (candidate / '.downstream/series').read_text().splitlines()
            for entry in series:
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
                    print(f'Applied {name}', flush=True)
                elif git(candidate, 'apply', '--index', '--reverse', '--check', str(patch), check=False).returncode == 0:
                    print(f'Already present upstream: {name}', flush=True)
                else:
                    raise RuntimeError(f'Patch no longer applies: {name}\n{forward.stderr}'
                                       'No source branch or image was published.')
            config['ref'] = ref
            config['commit'] = upstream
            (candidate / '.downstream/upstream.json').write_text(json.dumps(config, indent=2) + '\n')
            git(candidate, 'add', '--all')
            tree = git(candidate, 'write-tree').stdout.strip()
            if tree != git(root, 'rev-parse', base + '^{tree}').stdout.strip():
                # A linear snapshot avoids importing changes to upstream workflows
                # that the standard GITHUB_TOKEN is not allowed to manage.
                source = git(candidate, '-c', 'user.name=github-actions[bot]',
                             '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                             'commit-tree', tree, '-p', base, '-m',
                             f'Sync upstream {ref} at {upstream[:12]}; replay downstream patches\n\n'
                             f'Upstream-Commit: {upstream}').stdout.strip()
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
