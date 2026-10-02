"""Offline integration tests with real temporary Git repositories."""
from __future__ import annotations

import difflib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'sync-upstream.py'
spec = importlib.util.spec_from_file_location('sync_upstream', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(root: Path, *args: str) -> str:
    return module.git(root, *args).stdout.strip()


def write(root: Path, name: str, text: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(root: Path, message: str) -> str:
    git(root, 'add', '--all')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.up = Path(self.temp.name) / 'upstream'
        self.up.mkdir()
        git(self.up, 'init', '-b', 'master')
        write(self.up, 'setting.txt', 'default=none\n')
        write(self.up, 'removed-next-release.txt', 'old\n')
        write(self.up, '.github/workflows/vendor.yml', 'upstream deployment\n')
        initial = commit(self.up, 'Upstream initial')
        self.fork = Path(self.temp.name) / 'fork'
        subprocess.run(['git', 'clone', str(self.up), str(self.fork)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git(self.fork, 'rm', '.github/workflows/vendor.yml')
        write(self.fork, '.github/workflows/downstream.yml', 'trusted fork workflow\n')
        write(self.fork, '.downstream/upstream.json', json.dumps({
            'repository': str(self.up), 'ref': 'refs/heads/master', 'commit': initial
        }, indent=2) + '\n')
        write(self.fork, '.downstream/series', 'settings.patch\n')
        diff = ''.join(difflib.unified_diff(['default=none\n'], ['default=ask\n'],
                       fromfile='a/setting.txt', tofile='b/setting.txt'))
        write(self.fork, '.downstream/patches/settings.patch', diff)
        write(self.fork, '.downstream/keep.txt', 'local tooling\n')
        self.base = commit(self.fork, 'Fork tooling')

    def sync(self) -> dict[str, str]:
        return module.sync(self.fork)

    def assert_unchanged(self, base: str) -> None:
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD'), base)
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')
        self.assertEqual(git(self.fork, 'worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_replays_patch_tracks_additions_and_deletions_and_keeps_own_ci(self) -> None:
        write(self.up, 'new.txt', 'new source\n')
        (self.up / 'removed-next-release.txt').unlink()
        write(self.up, '.github/workflows/new-vendor.yml', 'do not import\n')
        upstream = commit(self.up, 'New upstream release')
        result = self.sync()
        self.assertEqual(result['base_sha'], self.base)
        self.assertEqual(result['upstream_sha'], upstream)
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')
        self.assertEqual((self.fork / 'new.txt').read_text(), 'new source\n')
        self.assertFalse((self.fork / 'removed-next-release.txt').exists())
        self.assertEqual(sorted(p.name for p in (self.fork / '.github/workflows').iterdir()),
                         ['downstream.yml'])
        self.assertEqual((self.fork / '.downstream/keep.txt').read_text(), 'local tooling\n')
        self.assertEqual(git(self.fork, 'rev-list', '--parents', '-n', '1', 'HEAD').split()[1:],
                         [self.base])
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')

    def test_second_identical_sync_does_not_create_an_empty_commit(self) -> None:
        first = self.sync()
        second = self.sync()
        self.assertEqual(first['source_sha'], second['source_sha'])
        self.assertEqual(second['base_sha'], second['source_sha'])

    def test_upstream_adopts_patch_without_double_application(self) -> None:
        self.sync()
        write(self.up, 'setting.txt', 'default=ask\n')
        commit(self.up, 'Adopt downstream behavior')
        self.sync()
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')

    def test_conflict_leaves_fork_unchanged(self) -> None:
        write(self.up, 'setting.txt', 'upstream rewrote this feature\n')
        commit(self.up, 'Incompatible rewrite')
        with self.assertRaisesRegex(RuntimeError, 'Patch no longer applies'):
            self.sync()
        self.assert_unchanged(self.base)

    def test_upstream_cannot_replace_fork_tooling(self) -> None:
        write(self.up, '.downstream/series', 'unexpected upstream tooling\n')
        commit(self.up, 'Namespace collision')
        with self.assertRaisesRegex(RuntimeError, 'reserved path'):
            self.sync()
        self.assert_unchanged(self.base)

    def test_patch_path_must_stay_in_queue(self) -> None:
        write(self.fork, '.downstream/series', '../outside.patch\n')
        base = commit(self.fork, 'Invalid queue')
        with self.assertRaisesRegex(RuntimeError, 'Invalid patch filename'):
            self.sync()
        self.assert_unchanged(base)

    def test_dirty_checkout_is_not_overwritten(self) -> None:
        write(self.fork, 'setting.txt', 'my uncommitted work\n')
        with self.assertRaisesRegex(RuntimeError, 'dirty checkout'):
            self.sync()
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'my uncommitted work\n')
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD'), self.base)


if __name__ == '__main__':
    unittest.main()
