#!/usr/bin/env python3
"""Refresh control using exact old-control code, verified backups and atomic leases."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPO = 'felixfoertsch/jellyfin-web'
UPSTREAM = 'jellyfin/jellyfin-web'
WORKFLOW = '.github/workflows/downstream.yml'
SHA = re.compile(r'[0-9a-f]{40}')
spec = importlib.util.spec_from_file_location('reconstruct', Path(__file__).with_name('sync-upstream.py'))
reconstruct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconstruct)


def run(*args, cwd=None, env=None):
    if args[0] == 'git':
        args = ('git', '-c', 'core.hooksPath=/dev/null', '-c', 'init.templateDir=', *args[1:])
        env = {**(env or os.environ), 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def git(root, *args):
    return run('git', '-C', str(root), *args)


def api(endpoint):
    return json.loads(run('gh', 'api', endpoint))


def selection():
    branch = api(f'repos/{UPSTREAM}')['default_branch']
    ref = 'refs/heads/' + branch
    rows = run('git', 'ls-remote', 'https://github.com/' + UPSTREAM + '.git', ref).split()
    if len(rows) != 2 or rows[1] != ref or not SHA.fullmatch(rows[0]):
        raise RuntimeError('Cannot resolve upstream default branch.')
    return ref, rows[0]


def remote_head(root, ref):
    rows = git(root, 'ls-remote', 'origin', ref).split()
    if not rows:
        return ''
    if len(rows) != 2 or rows[1] != ref or not SHA.fullmatch(rows[0]):
        raise RuntimeError('Unexpected remote ref response.')
    return rows[0]


def verify_backup(directory, expected):
    manifest = json.loads((directory / 'backup.json').read_text())
    bundle = directory / 'control.bundle'
    if manifest != {'control': expected, 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}:
        raise RuntimeError('Backup hash or old control mismatch.')
    with tempfile.TemporaryDirectory(prefix='control-restore-') as temp:
        run('git', 'init', '--bare', temp)
        run('git', '-C', temp, 'bundle', 'verify', str(bundle.resolve()))
        run('git', '-C', temp, 'fetch', str(bundle.resolve()), 'refs/heads/patch-queue:refs/heads/restored')
        git(temp, 'fsck', '--full', '--no-reflogs')
        if git(temp, 'rev-parse', 'refs/heads/restored') != expected:
            raise RuntimeError('Backup does not restore exact old head.')


def backup(root, directory, old):
    directory.mkdir(parents=True, exist_ok=True)
    # Bundle only reachable old history, with no prerequisites.
    with tempfile.TemporaryDirectory(prefix='control-bundle-') as temp:
        run('git', 'clone', '--bare', '--no-hardlinks', str(root), temp)
        git(temp, 'update-ref', 'refs/heads/patch-queue', old)
        git(temp, 'bundle', 'create', str((directory / 'control.bundle').resolve()), 'refs/heads/patch-queue')
    digest = hashlib.sha256((directory / 'control.bundle').read_bytes()).hexdigest()
    (directory / 'backup.json').write_text(json.dumps({'control': old, 'sha256': digest}) + '\n')
    verify_backup(directory, old)


def candidate(root, old, ref, upstream):
    # Called only through this exact old-control module; no candidate code executes.
    token = os.environ.pop('GH_TOKEN', None)
    os.environ['GIT_CONFIG_GLOBAL'] = '/dev/null'
    os.environ['GIT_CONFIG_NOSYSTEM'] = '1'
    try:
        result = reconstruct.sync(root, upstream_url='https://github.com/' + UPSTREAM + '.git',
                                  upstream_ref=ref, control=True)
    finally:
        if token is not None:
            os.environ['GH_TOKEN'] = token
    if result['base_sha'] != old or result['upstream_sha'] != upstream:
        raise RuntimeError('Control or upstream changed during reconstruction.')
    if git(root, 'ls-tree', '-r', old, '--', '.github/workflows') != git(root, 'ls-tree', '-r', 'HEAD', '--', '.github/workflows'):
        raise RuntimeError('Recurring refresh cannot change trusted workflow bytes.')
    return result


def plan(root, directory, expected=''):
    old = git(root, 'rev-parse', 'HEAD')
    if expected and expected != old:
        raise RuntimeError('Dispatch targeted another control revision.')
    if remote_head(root, 'refs/heads/patch-queue') != old:
        raise RuntimeError('Stale control run.')
    ref, upstream = selection()
    with tempfile.TemporaryDirectory(prefix='control-plan-') as temp:
        run('git', 'clone', '--no-hardlinks', str(root), temp)
        git(temp, 'checkout', '--detach', old)
        result = candidate(Path(temp), old, ref, upstream)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {'control': old, 'ref': ref, 'upstream': upstream,
                'candidate': result['source_sha'], 'tree': result['source_tree']}
    (directory / 'candidate.json').write_text(json.dumps(evidence) + '\n')
    changed = result['source_sha'] != old
    if changed:
        backup(root, directory, old)
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a') as stream:
            stream.write('changed=' + str(changed).lower() + '\n')
    return evidence


def push(root, old, new):
    if remote_head(root, 'refs/heads/patch-queue') != old:
        raise RuntimeError('Control moved before atomic push.')
    archive = 'refs/tags/archive/patch-queue/' + old
    existing = remote_head(root, archive)
    if existing and existing != old:
        raise RuntimeError('Archive tag must point directly to exact old commit.')
    token = os.environ['GH_TOKEN']
    auth = base64.b64encode(('x-access-token:' + token).encode()).decode()
    env = {**os.environ, 'GIT_CONFIG_COUNT': '1',
           'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
           'GIT_CONFIG_VALUE_0': 'AUTHORIZATION: basic ' + auth}
    args = ['git', '-C', str(root), 'push', '--atomic',
            '--force-with-lease=refs/heads/patch-queue:' + old,
            '--force-with-lease=' + archive + ':' + existing, 'origin',
            new + ':refs/heads/patch-queue', old + ':' + archive]
    # No fallback: unsupported atomic pushes must fail without changing either ref.
    subprocess.run(args, env=env, check=True)


def dispatch(new, rebuild):
    # workflow_dispatch supports exact revision guard + explicit rebuild intent.
    payload = {'ref': 'patch-queue', 'inputs': {'expected_control': new, 'rebuild': rebuild}}
    subprocess.run(['gh', 'api', '--method', 'POST',
                    f'repos/{REPO}/actions/workflows/downstream.yml/dispatches', '--input', '-'],
                   input=json.dumps(payload), text=True, check=True)


def promote(root, directory, rebuild=False):
    evidence = json.loads((directory / 'candidate.json').read_text())
    if set(evidence) != {'control', 'ref', 'upstream', 'candidate', 'tree'}:
        raise ValueError('Unexpected evidence fields.')
    for key in ['control', 'upstream', 'candidate', 'tree']:
        if not isinstance(evidence[key], str) or not SHA.fullmatch(evidence[key]):
            raise ValueError('Invalid evidence SHA.')
    old = git(root, 'rev-parse', 'HEAD')
    if evidence['control'] != old or old == evidence['candidate']:
        raise RuntimeError('Incorrect old control or no-op promotion.')
    verify_backup(directory, old)
    ref, upstream = selection()
    if (ref, upstream) != (evidence['ref'], evidence['upstream']):
        raise RuntimeError('Upstream moved before promotion.')
    with tempfile.TemporaryDirectory(prefix='control-promote-') as temp:
        run('git', 'clone', '--no-hardlinks', str(root), temp)
        git(temp, 'checkout', '--detach', old)
        result = candidate(Path(temp), old, ref, upstream)
        if (result['source_sha'], result['source_tree']) != (evidence['candidate'], evidence['tree']):
            raise RuntimeError('Candidate differs from trusted reconstruction.')
        # Push from candidate object store, but only to authoritative original URL.
        git(temp, 'remote', 'set-url', 'origin', git(root, 'remote', 'get-url', 'origin'))
        if selection() != (ref, upstream) or remote_head(root, 'refs/heads/patch-queue') != old:
            raise RuntimeError('Control or upstream moved immediately before promotion.')
        push(Path(temp), old, result['source_sha'])
        if (remote_head(root, 'refs/heads/patch-queue') != result['source_sha']
                or remote_head(root, 'refs/tags/archive/patch-queue/' + old) != old):
            raise RuntimeError('Atomic promotion readback differs from intended control/archive.')
    # A dispatch failure fails job; next scheduled run recovers from new control.
    dispatch(evidence['candidate'], rebuild)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'verify', 'promote'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--expected-control', default='')
    parser.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    root = Path.cwd()
    if args.command == 'plan':
        plan(root, args.directory, args.expected_control)
    elif args.command == 'verify':
        verify_backup(args.directory, git(root, 'rev-parse', 'HEAD'))
    else:
        promote(root, args.directory, args.rebuild)


if __name__ == '__main__':
    main()
