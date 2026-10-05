"""Release snapshots must be isolated from unreleased master changes."""
import json
import unittest
import test_sync
from test_sync import git, write, commit, module


class ReleaseSyncTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp

    def test_release_uses_annotated_tag_not_later_master(self) -> None:
        release = git(self.up, 'rev-parse', 'HEAD')
        git(self.up, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
            'tag', '-a', 'v12.1', '-m', 'Stable release')
        write(self.up, 'nightly-only.txt', 'not in the official release\n')
        commit(self.up, 'Unreleased changes')
        result = module.sync(self.fork, upstream_ref='refs/tags/v12.1')
        self.assertEqual(result['upstream_sha'], release)
        self.assertFalse((self.fork / 'nightly-only.txt').exists())
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')
        self.assertEqual(json.loads((self.fork / '.downstream/upstream.json').read_text())['ref'],
                         'refs/tags/v12.1')
        self.assertEqual(result['source_tree'], git(self.fork, 'rev-parse', 'HEAD^{tree}'))

    def test_stable_source_tree_is_independent_of_nightly_parent(self) -> None:
        git(self.up, 'tag', 'v12.1')
        first = module.sync(self.fork, upstream_ref='refs/tags/v12.1')
        git(self.fork, 'reset', '--hard', self.base)
        write(self.up, 'nightly-only.txt', 'unreleased\n')
        commit(self.up, 'Move nightly forward')
        module.sync(self.fork, upstream_ref='refs/heads/master')
        second = module.sync(self.fork, upstream_ref='refs/tags/v12.1')
        self.assertEqual(first['source_tree'], second['source_tree'])
        self.assertEqual(first['source_sha'], second['source_sha'])

    def test_switching_back_to_nightly_restores_master_metadata(self) -> None:
        git(self.up, 'tag', 'v12.1')
        module.sync(self.fork, upstream_ref='refs/tags/v12.1')
        write(self.up, 'nightly-only.txt', 'unreleased\n')
        upstream = commit(self.up, 'Move nightly forward')
        result = module.sync(self.fork, upstream_ref='refs/heads/master')
        self.assertEqual(result['upstream_sha'], upstream)
        self.assertTrue((self.fork / 'nightly-only.txt').exists())
        self.assertEqual(json.loads((self.fork / '.downstream/upstream.json').read_text())['ref'],
                         'refs/heads/master')


if __name__ == '__main__':
    unittest.main()
