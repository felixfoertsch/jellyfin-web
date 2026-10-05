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
    return module.git(root, '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
                      *args).stdout.strip()


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
        self.assertFalse((self.fork / '.github/workflows').exists())
        self.assertEqual((self.fork / '.downstream/keep.txt').read_text(), 'local tooling\n')
        self.assertEqual(git(self.fork, 'rev-list', '--count', upstream + '..HEAD'), '2')
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD~2'), upstream)
        self.assertFalse(module.is_ancestor(self.fork, self.base, 'HEAD'))
        self.assertEqual(git(self.fork, 'rev-list', '--count', 'HEAD..' + upstream), '0')
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')

    def test_readme_items_belong_to_each_patch_commit_in_queue_order(self) -> None:
        write(self.up, 'README.md', '# Upstream README\n')
        upstream = commit(self.up, 'Add README')
        write(self.fork, '.downstream/series', 'settings.patch\nsecond.patch\n')
        diff = ''.join(difflib.unified_diff(['default=ask\n'], ['default=skip\n'],
                       fromfile='a/setting.txt', tofile='b/setting.txt'))
        write(self.fork, '.downstream/patches/second.patch', 'Subject: Second behavior\n\n' + diff)
        commit(self.fork, 'Add second patch')
        self.sync()
        tooling = git(self.fork, 'show', 'HEAD~2:README.md')
        first = git(self.fork, 'show', 'HEAD~1:README.md')
        second = git(self.fork, 'show', 'HEAD:README.md')
        self.assertTrue(tooling.startswith('This fork follows upstream [Jellyfin Web]'))
        self.assertNotIn('1. [', tooling)
        self.assertIn('1. [settings.patch]', first)
        self.assertNotIn('Second behavior', first)
        self.assertLess(second.index('1. [settings.patch]'), second.index('2. [Second behavior]'))
        self.assertEqual((self.fork / 'README.md').read_text().split('\n---\n\n', 1)[1], '# Upstream README\n')
        self.assertIn('https://github.com/felixfoertsch/jellyfin-web/blob/automation/.downstream/patches/', second)
        self.assertEqual(git(self.fork, 'rev-list', '--count', upstream + '..HEAD'), '3')

    def test_second_identical_sync_does_not_create_an_empty_commit(self) -> None:
        first = self.sync()
        second = self.sync()
        self.assertEqual(first['source_sha'], second['source_sha'])
        self.assertEqual(second['base_sha'], second['source_sha'])

    def test_upstream_adopts_patch_without_double_application(self) -> None:
        self.sync()
        write(self.up, 'setting.txt', 'default=ask\n')
        commit(self.up, 'Adopt downstream behavior')
        result = self.sync()
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')
        self.assertEqual(git(self.fork, 'rev-list', '--count', result['upstream_sha'] + '..HEAD'), '1')

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

    def test_repairs_snapshot_ancestry_even_with_identical_source_tree(self) -> None:
        write(self.up, 'new.txt', 'already copied by old snapshot sync\n')
        upstream = commit(self.up, 'New upstream')
        first = self.sync()
        # Simulate the previous tool: identical result, but only a fork parent.
        snapshot = git(self.fork, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', first['source_tree'], '-p', self.base, '-m', 'Old snapshot')
        git(self.fork, 'reset', '--hard', snapshot)
        self.assertFalse(module.is_ancestor(self.fork, upstream, snapshot))
        result = self.sync()
        self.assertEqual(result['source_tree'], first['source_tree'])
        self.assertNotEqual(result['source_sha'], snapshot)
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD~2'), upstream)
        self.assertEqual(git(self.fork, 'rev-list', '--count', upstream + '..HEAD'), '2')
        self.assertEqual(git(self.fork, 'rev-list', '--count', 'HEAD..' + upstream), '0')
        self.assertEqual(self.sync()['source_sha'], result['source_sha'])

    def test_repeated_updates_always_have_zero_missing_upstream_commits(self) -> None:
        previous = self.base
        for number in range(3):
            write(self.up, 'new.txt', f'upstream {number}\n')
            upstream = commit(self.up, f'Upstream {number}')
            result = self.sync()
            self.assertFalse(module.is_ancestor(self.fork, previous, result['source_sha']))
            self.assertEqual(git(self.fork, 'rev-list', '--count', upstream + '..HEAD'), '2')
            self.assertEqual(git(self.fork, 'rev-list', '--count', 'HEAD..' + upstream), '0')
            self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')
            previous = result['source_sha']

    def test_upstream_workflow_changes_are_in_history_not_executed_tree(self) -> None:
        write(self.up, '.github/workflows/vendor.yml', 'changed upstream deployment\n')
        upstream = commit(self.up, 'Upstream CI change')
        self.sync()
        self.assertTrue(module.is_ancestor(self.fork, upstream, 'HEAD'))
        self.assertFalse((self.fork / '.github/workflows/vendor.yml').exists())
        self.assertEqual(git(self.fork, 'show', upstream + ':.github/workflows/vendor.yml'),
                         'changed upstream deployment')

    def test_existing_upstream_ancestor_does_not_create_redundant_merge_parent(self) -> None:
        self.sync()
        upstream = git(self.up, 'rev-parse', 'HEAD')
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD~2'), upstream)
        self.assertEqual(git(self.fork, 'rev-list', '--count', upstream + '..HEAD'), '2')

    def test_ref_is_annotated_release_tag_not_newer_master(self) -> None:
        write(self.up, 'new.txt', 'release\n')
        release = commit(self.up, 'Release')
        git(self.up, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            'tag', '-a', 'v1.2', '-m', 'Release')
        write(self.up, 'new.txt', 'unreleased\n')
        commit(self.up, 'Development after release')
        result = module.sync(self.fork, upstream_ref='refs/tags/v1.2')
        self.assertEqual(result['upstream_sha'], release)
        self.assertTrue(module.is_ancestor(self.fork, release, result['source_sha']))
        self.assertEqual((self.fork / 'new.txt').read_text(), 'release\n')
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')

    def test_unlisted_source_edits_cannot_leak_into_reconstructed_source(self) -> None:
        write(self.fork, 'unlisted.txt', 'not a reviewed patch\n')
        commit(self.fork, 'Ad hoc edit')
        self.sync()
        self.assertFalse((self.fork / 'unlisted.txt').exists())


if __name__ == '__main__':
    unittest.main()
