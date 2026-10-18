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
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = git(root, 'merge-base', '--is-ancestor', ancestor, descendant, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'Cannot verify Git ancestry:\n{result.stderr}')
    return result.returncode == 0


def sync(root: Path, upstream_url: str | None = None, upstream_ref: str | None = None,
         omit_queue: bool = False, control: bool = False,
         audit: bool = True, selected_commit: str | None = None) -> dict[str, str]:
    if git(root, 'status', '--porcelain').stdout.strip():
        raise RuntimeError('Refusing to replace a dirty checkout; commit or stash changes first.')
    base = git(root, 'rev-parse', 'HEAD').stdout.strip()
    config = json.loads((root / '.downstream/upstream.json').read_text())
    url = upstream_url or config['repository']
    ref = upstream_ref or config['ref']
    if control and (url != config['repository'] or ref != config['ref']):
        raise RuntimeError('Upstream repository or default branch changed; review required.')
    git(root, 'fetch', '--no-tags', url, selected_commit or ref)
    upstream = git(root, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    if git(root, 'ls-tree', upstream, '--', '.downstream').stdout.strip():
        raise RuntimeError('Upstream now owns .downstream; reconcile this reserved path manually.')
    if control and not is_ancestor(root, config['commit'], upstream):
        raise RuntimeError('Previous upstream is not ancestor of selected upstream.')
    if control and audit and config.get('control_refresh') == 1:
        # Reconstruct old intended source at recorded upstream before refreshing.
        with tempfile.TemporaryDirectory(prefix='control-old-audit-') as directory:
            old_source = Path(directory) / 'source'
            git(root, 'worktree', 'add', '--detach', str(old_source), base)
            try:
                expected = sync(old_source, url, ref, control=True, audit=False,
                                selected_commit=config['commit'])
                if git(root, 'rev-parse', base + '^{tree}').stdout.strip() != expected['source_tree']:
                    raise RuntimeError('Unqueued source customization in refreshed control.')
            finally:
                git(root, 'worktree', 'remove', '--force', str(old_source))
    if control and audit and config.get('control_refresh') != 1:
        # Old control may contain any prefix of the accepted queue (migration).
        # Compare exact blobs, not merely filenames mentioned by a patch.
        previous = config['commit']
        original = git(root, 'show', previous + ':README.md', check=False)
        readme_path = root / 'README.md'
        if original.returncode == 0 and not readme_path.is_file():
            raise RuntimeError('Unqueued upstream README deletion.')
        if readme_path.exists():
            readme = readme_path.read_text()
            if original.returncode or '\n---\n\n' not in readme or readme.split('\n---\n\n', 1)[1] != original.stdout:
                raise RuntimeError('Unqueued upstream README customization.')
        with tempfile.TemporaryDirectory(prefix='control-audit-') as audit_dir:
            audit = Path(audit_dir) / 'source'
            git(root, 'worktree', 'add', '--detach', str(audit), previous)
            try:
                changed = git(root, 'diff', '--name-only', '-z', previous, base).stdout.split('\0')
                paths = [p for p in changed if p and p != 'README.md'
                         and not p.startswith(('.downstream/', '.github/workflows/'))]
                def blobs(tree: Path) -> dict[str, str]:
                    return {p: git(tree, 'ls-files', '--stage', '--', p).stdout for p in paths}
                states = [blobs(audit)]
                for entry in (root / '.downstream/series').read_text().splitlines():
                    name = entry.split('#', 1)[0].strip()
                    if not name:
                        continue
                    if Path(name).name != name or not name.endswith('.patch'):
                        raise RuntimeError('Invalid patch filename.')
                    patch = root / '.downstream/patches' / name
                    if patch.is_symlink() or not patch.is_file():
                        raise RuntimeError('Unsafe patch.')
                    if git(audit, 'apply', '--index', '--check', str(patch), check=False).returncode == 0:
                        git(audit, 'apply', '--index', str(patch))
                    elif git(audit, 'apply', '--index', '--reverse', '--check', str(patch), check=False).returncode:
                        raise RuntimeError('Cannot audit accepted patch against previous upstream.')
                    states.append(blobs(audit))
                actual = blobs(root)
                if any(actual[p] not in [state[p] for state in states] for p in paths):
                    raise RuntimeError('Unqueued source customization; reconcile before control refresh.')
            finally:
                git(root, 'worktree', 'remove', '--force', str(audit))
    if control:
        previous = config['commit']
        owned = git(root, 'ls-tree', '-r', '--name-only', base, '--', '.github/workflows').stdout.splitlines()
        if owned != ['.github/workflows/downstream.yml']:
            raise RuntimeError('Unknown executable control workflow.')
        for path in owned:
            old_blob = git(root, 'ls-tree', previous, '--', path).stdout
            new_blob = git(root, 'ls-tree', upstream, '--', path).stdout
            if new_blob and new_blob != old_blob:
                raise RuntimeError('Upstream ownership collision: ' + path)
    source = base
    # Stable metadata makes unchanged replay produce identical commit IDs.
    timestamp = git(root, 'show', '-s', '--format=%cI', upstream).stdout.strip()
    commit_env = {**os.environ, 'GIT_AUTHOR_DATE': timestamp, 'GIT_COMMITTER_DATE': timestamp}

    def record(candidate: Path, message: str) -> str:
        result = subprocess.run(
            ['git', '-C', str(candidate), '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
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
            if control:
                git(candidate, 'restore', '--source=' + base, '--staged', '--worktree',
                    '--', '.github/workflows/downstream.yml')
            config['ref'] = ref
            config['commit'] = upstream
            if control:
                config['control_refresh'] = 1
            else:
                config.pop('control_refresh', None)
            (candidate / '.downstream/upstream.json').write_text(json.dumps(config, indent=2) + '\n')
            readme = candidate / 'README.md'
            upstream_readme = readme.read_text() if readme.exists() else ''
            patch_items: list[str] = []
            patch_links: list[str] = []

            def update_readme() -> None:
                links = ', '.join(patch_links)
                readme.write_text('This fork follows upstream [Jellyfin Web](https://github.com/jellyfin/jellyfin-web) with accepted patches in order: ' + (links or 'none') + '. `patch-queue` owns patches and workflows; generated `main` contains upstream source plus complete accepted queue. Nightly builds follow upstream default branch; stable builds follow upstream releases.\n\n'
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
                patch_links.append(f'[{name.split("-", 1)[0]}](https://github.com/felixfoertsch/jellyfin-web/blob/patch-queue/.downstream/patches/{name})')
                forward = git(candidate, 'apply', '--index', '--check', str(patch), check=False)
                if forward.returncode == 0:
                    git(candidate, 'apply', '--index', '--whitespace=error-all', str(patch))
                    subject = next((line.removeprefix('Subject: ').strip()
                                    for line in patch.read_text().splitlines()
                                    if line.startswith('Subject: ')), name)
                    patch_items.append(f'{len(patch_items) + 1}. [{name.split("-", 1)[0]} {subject}](https://github.com/felixfoertsch/jellyfin-web/blob/patch-queue/.downstream/patches/{name})\n')
                    update_readme()
                    source = record(candidate, f'Apply downstream patch: {name}')
                    print(f'Applied {name}', flush=True)
                elif git(candidate, 'apply', '--index', '--reverse', '--check', str(patch), check=False).returncode == 0:
                    patch_items.append(f'{len(patch_items) + 1}. [{name.split("-", 1)[0]} {name} (upstream adopted)](https://github.com/felixfoertsch/jellyfin-web/blob/patch-queue/.downstream/patches/{name})\n')
                    update_readme()
                    source = record(candidate, f'Record upstream-adopted patch: {name}')
                    print(f'Already present upstream: {name}', flush=True)
                else:
                    raise RuntimeError(f'Patch no longer applies: {name}\n{forward.stderr}'
                                       'No source branch or image was published.')
            if omit_queue:
                git(candidate, 'rm', '-r', '--ignore-unmatch', '.downstream/patches', '.downstream/series')
                source = record(candidate, 'Keep accepted patch queue on patch-queue only')
            if not is_ancestor(root, upstream, source):
                raise RuntimeError('Refusing source that loses upstream ancestry.')
        finally:
            git(root, 'worktree', 'remove', '--force', str(candidate))
    if control and is_ancestor(root, upstream, base) and git(root, 'rev-parse', base + '^{tree}').stdout == git(root, 'rev-parse', source + '^{tree}').stdout:
        source = base
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
        result = sync(root, args.upstream_url, args.upstream_ref, omit_queue=True)
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    for key, value in result.items():
        print(f'{key}={value}')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.writelines(f'{key}={value}\n' for key, value in result.items())
