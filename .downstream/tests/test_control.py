"""Control refresh regression checks using real Git repositories."""
from test_sync import SyncTests, commit, git, module, write


class ControlTests(SyncTests):
    # Inherit source replay checks as direct contract neighbors.
    def test_control_refresh_preserves_overlay_and_is_noop_when_current(self):
        write(self.up, 'README.md', '# Fresh upstream\n')
        upstream = commit(self.up, 'Advance upstream')
        result = module.sync(self.fork, control=True)
        self.assertEqual(result['upstream_sha'], upstream)
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')
        self.assertEqual((self.fork / '.github/workflows/downstream.yml').read_text(), 'trusted fork workflow\n')
        self.assertTrue((self.fork / '.downstream/series').exists())
        self.assertEqual((self.fork / 'README.md').read_text().split('\n---\n\n')[1], '# Fresh upstream\n')
        again = module.sync(self.fork, control=True)
        self.assertEqual(again['source_sha'], result['source_sha'])
        self.assertTrue(module.is_ancestor(self.fork, upstream, again['source_sha']))

    def test_empty_upstream_commit_refreshes_once(self):
        first = module.sync(self.fork, control=True)
        git(self.up, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'Empty upstream advance')
        second = module.sync(self.fork, control=True)
        self.assertNotEqual(first['source_sha'], second['source_sha'])
        self.assertTrue(module.is_ancestor(self.fork, git(self.up, 'rev-parse', 'HEAD'), second['source_sha']))
        self.assertEqual(module.sync(self.fork, control=True)['source_sha'], second['source_sha'])

    def test_refreshed_control_rejects_direct_source_edit(self):
        module.sync(self.fork, control=True)
        write(self.fork, 'setting.txt', 'default=none\n')
        before = commit(self.fork, 'Unqueued revert')
        with self.assertRaisesRegex(RuntimeError, 'Unqueued'):
            module.sync(self.fork, control=True)
        self.assert_unchanged(before)

    def test_source_tree_same_before_and_after_control_refresh(self):
        import tempfile
        import subprocess
        from pathlib import Path
        before = self.base
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / 'source'
            subprocess.run(['git', 'clone', str(self.fork), str(other)], check=True, capture_output=True)
            original = module.sync(other, omit_queue=True)
            module.sync(self.fork, control=True)
            refreshed = module.sync(self.fork, omit_queue=True)
            self.assertEqual(original['source_tree'], refreshed['source_tree'])

    def test_legacy_readme_deletion_is_not_silently_restored(self):
        write(self.up, 'README.md', '# Upstream README\n')
        upstream = commit(self.up, 'Add upstream README')
        git(self.fork, 'fetch', str(self.up), 'master')
        import json
        config = json.loads((self.fork / '.downstream/upstream.json').read_text())
        config['commit'] = upstream
        write(self.fork, '.downstream/upstream.json', json.dumps(config))
        # Legacy control deleted upstream README, instead of maintaining prefix.
        before = commit(self.fork, 'Unqueued README deletion')
        with self.assertRaisesRegex(RuntimeError, 'README'):
            module.sync(self.fork, control=True)
        self.assert_unchanged(before)

    def test_control_refuses_default_branch_change(self):
        with self.assertRaisesRegex(RuntimeError, 'default branch changed'):
            module.sync(self.fork, upstream_ref='refs/heads/changed', control=True)

    def test_control_refuses_unknown_source_customization(self):
        write(self.fork, 'unknown.txt', 'must not disappear\n')
        before = commit(self.fork, 'Unqueued customization')
        with self.assertRaisesRegex(RuntimeError, 'Unqueued'):
            module.sync(self.fork, control=True)
        self.assert_unchanged(before)

    def test_control_accepts_known_already_applied_patch(self):
        write(self.fork, 'setting.txt', 'default=ask\n')
        commit(self.fork, 'Previously applied accepted patch')
        module.sync(self.fork, control=True)
        self.assertEqual((self.fork / 'setting.txt').read_text(), 'default=ask\n')

    def test_control_preserves_fresh_upstream_readme_without_duplicates(self):
        module.sync(self.fork, control=True)
        write(self.up, 'README.md', '# Changed README\n')
        upstream = commit(self.up, 'New README')
        module.sync(self.fork, control=True)
        text = (self.fork / 'README.md').read_text()
        self.assertEqual(text.count('This fork follows upstream'), 1)
        self.assertTrue(text.endswith('# Changed README\n'))
        self.assertTrue(module.is_ancestor(self.fork, upstream, 'HEAD'))
