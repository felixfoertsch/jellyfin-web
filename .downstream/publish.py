#!/usr/bin/env python3
"""Publish evidence from this run using only exact-revision trusted control."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from channels import IMAGE, inspect_digest, promote
from freshness import check, dispatch


def publish(path: Path) -> None:
    evidence = json.loads(path.read_text())
    if evidence['CONTROL_SHA'] != os.environ['CONTROL_SHA'] or evidence['CHANNEL'] != os.environ['CHANNEL']:
        raise ValueError('Candidate control revision or channel mismatch.')
    for key in ['CONTROL_SHA', 'UPSTREAM_SHA', 'SOURCE_SHA', 'SOURCE_TREE', 'EXPECTED_BASE']:
        if not re.fullmatch(r'[0-9a-f]{40}', evidence[key]):
            raise ValueError(f'Invalid candidate {key}.')
    control = Path(__file__).resolve().parent
    remote = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/patch-queue'], text=True).split()
    if remote != [evidence['CONTROL_SHA'], 'refs/heads/patch-queue']:
        raise RuntimeError('Trusted patch queue changed; refusing stale publication.')
    channel = evidence['CHANNEL']
    if check(channel, evidence['UPSTREAM_REF'], evidence['UPSTREAM_SHA'])['current'] != 'true':
        dispatch()
        return
    # Reconstruction executes trusted control only, never source dependency code.
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source'
        subprocess.run(['git', 'worktree', 'add', '--detach', str(source), evidence['CONTROL_SHA']], check=True)
        try:
            subprocess.run([sys.executable, str(control / 'sync-upstream.py'), '--upstream-ref', evidence['UPSTREAM_REF']], cwd=source, check=True)
            actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD', 'HEAD^{tree}'], text=True).splitlines()
            if actual != [evidence['SOURCE_SHA'], evidence['SOURCE_TREE']]:
                raise RuntimeError('Candidate source differs from trusted reconstruction.')
            if evidence['BUILT'] == 'true':
                if inspect_digest(f"{IMAGE}@{evidence['DIGEST']}") != evidence['DIGEST']:
                    raise RuntimeError('Candidate digest lacks verified multiarch index.')
                configs = json.loads(subprocess.check_output(
                    ['docker', 'buildx', 'imagetools', 'inspect', f"{IMAGE}@{evidence['DIGEST']}",
                     '--format', '{{json .Image}}'], text=True))
                for platform in ['linux/amd64', 'linux/arm64']:
                    labels = configs[platform]['config']['Labels']
                    if (labels.get('org.opencontainers.image.revision') != evidence['SOURCE_SHA']
                            or labels.get('io.github.felixfoertsch.jellyfin-web.upstream') != evidence['UPSTREAM_SHA']
                            or labels.get('io.github.felixfoertsch.jellyfin-web.channel') != channel):
                        raise RuntimeError('Candidate image labels differ from verified source.')
            elif evidence['BUILT'] != 'false':
                raise ValueError('Invalid build flag.')
            env = {**os.environ, 'EXPECTED_BASE': evidence['EXPECTED_BASE'],
                   'EXPECTED_PATCH_QUEUE': evidence['CONTROL_SHA'], 'SOURCE_TAG': evidence['SOURCE_TAG']}
            # Recheck upstream immediately before source and container promotion.
            if check(channel, evidence['UPSTREAM_REF'], evidence['UPSTREAM_SHA'])['current'] != 'true':
                dispatch()
                return
            if channel == 'nightly':
                subprocess.run(['bash', str(control / 'push-source.sh')], cwd=source, env=env, check=True)
            elif evidence['BUILT'] == 'true':
                subprocess.run(['bash', str(control / 'push-release-source.sh')], cwd=source, env=env, check=True)
            if evidence['BUILT'] == 'true':
                promote(channel, evidence['UPSTREAM_VERSION'], evidence['MARKER'], evidence['DIGEST'], identity=evidence['SOURCE_TAG'])
            if check(channel, evidence['UPSTREAM_REF'], evidence['UPSTREAM_SHA'])['current'] != 'true':
                dispatch()
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(source)], check=True)


if __name__ == '__main__':
    publish(Path(sys.argv[1]))
