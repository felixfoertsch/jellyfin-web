"""Offline release selection, tag isolation, and registry retry regression tests."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('channels', Path(__file__).resolve().parents[1] / 'channels.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChannelTests(unittest.TestCase):
    def release(self, **changes):
        data = {'tag_name': 'v12.1', 'draft': False, 'prerelease': False,
                'published_at': '2026-09-15T01:23:54Z'}
        return module.release_info({**data, **changes})

    def plan(self, channel='release', **changes):
        args = dict(channel=channel, version='12.1' if channel == 'release' else 'nightly',
                    source='a' * 40, tree='b' * 40, run_id='123', attempt='1',
                    now=datetime(2026, 10, 2, tzinfo=timezone.utc), inspect=lambda _: None,
                    release_tags=lambda: [])
        return module.plan(**{**args, **changes})

    def test_numeric_published_releases(self):
        for tag in ['v12.1', '12.1', 'v10.11.8', '13.0.0']:
            with self.subTest(tag=tag):
                self.assertEqual(self.release(tag_name=tag)['upstream_ref'], f'refs/tags/{tag}')

    def test_rejects_drafts_prereleases_and_unpublished_releases(self):
        for changes in [{'draft': True}, {'prerelease': True}, {'published_at': None},
                        {'tag_name': 'v13.0.0-rc.1'}, {'tag_name': 'master'},
                        {'tag_name': 'v12.1\nINJECT=1'}, {'tag_name': '--upload-pack=bad'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.release(**changes)

    def test_nightly_never_requests_latest_release(self):
        with patch.object(module.urllib.request, 'urlopen', side_effect=AssertionError('No API request')):
            self.assertEqual(module.resolve('nightly')['upstream_ref'], 'refs/heads/master')

    def test_channels_have_disjoint_aliases(self):
        release = set(module.aliases('release', '12.1'))
        nightly = set(module.aliases('nightly', 'nightly'))
        self.assertEqual(release, {'latest', 'stable', '12.1'})
        self.assertEqual(nightly, {'nightly', 'edge'})
        self.assertFalse(release & nightly)

    def test_nightly_plan_cannot_publish_latest(self):
        tags = self.plan('nightly')['tags'].splitlines()
        self.assertFalse(any(tag.endswith(':latest') or tag.endswith(':stable') for tag in tags))
        self.assertEqual(self.plan('nightly')['source_tag'], '')

    def test_release_has_unique_build_and_corresponding_source_tags(self):
        result = self.plan(release_tags=lambda: ['v12.1-2026.10.02.1', 'v12.1-2026.10.02.3'], upstream_tag='v12.1')
        self.assertIn('release-sha-' + 'a' * 40 + '-run-123-1', result['tags'])
        self.assertEqual(result['source_tag'], 'v12.1-2026.10.02.4')
        self.assertEqual(result['release_identity'], result['source_tag'])

    def test_release_date_uses_berlin_and_preserves_upstream_tag(self):
        result = self.plan(now=datetime(2026, 10, 2, 23, tzinfo=timezone.utc),
                           release_tags=lambda: [], upstream_tag='12.1')
        self.assertEqual(result['source_tag'], '12.1-2026.10.03.1')

    def test_versioned_container_tag_matches_reserved_source_identity(self):
        result = self.plan()
        digest = 'sha256:' + 'a' * 64
        run = Mock()
        module.promote('release', '12.1', result['marker'], digest,
                       inspect=lambda _: digest, run=run, identity=result['release_identity'])
        self.assertIn(module.IMAGE + ':' + result['source_tag'], run.call_args.args[0])
        with self.assertRaises(ValueError):
            module.promote('nightly', 'nightly', self.plan('nightly')['marker'], digest,
                           inspect=lambda _: digest, run=run, identity=result['source_tag'])

    def test_workflow_only_trusts_patch_queue_and_retains_publication_gates(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / '.github/workflows/downstream.yml').read_text()
        push = (root / '.downstream/push-source.sh').read_text()
        self.assertIn('branches: [patch-queue]', workflow)
        self.assertIn("FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'", workflow)
        self.assertIn('actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09', workflow)
        self.assertIn('node:24-bookworm-slim', (root / '.downstream/Dockerfile').read_text())
        self.assertEqual([p.name for p in root.iterdir() if p.is_file() and p.name.lower().startswith('readme')], ['README.md'])
        self.assertIn('ref: ${{ github.sha }}', workflow)
        build_job, publisher = workflow.split('  publish:', 1)
        self.assertNotIn('contents: write', build_job)
        self.assertIn('contents: write', publisher)
        self.assertIn('persist-credentials: false', publisher)
        self.assertNotIn('npm ', publisher)
        self.assertNotIn('refs/heads/automation', workflow + push)
        self.assertIn('platforms: linux/amd64,linux/arm64', workflow)
        self.assertIn('refs/heads/patch-queue', push)
        self.assertIn('--force-with-lease="refs/heads/main:$EXPECTED_BASE"', push)
        self.assertIn('publish.py evidence/candidate.json', publisher)

    def test_release_counter_errors_fail_closed(self):
        def fail():
            raise RuntimeError('Cannot read tags')
        with self.assertRaisesRegex(RuntimeError, 'Cannot read tags'):
            self.plan(release_tags=fail)

    def test_skip_only_when_marker_and_all_aliases_agree(self):
        self.assertEqual(self.plan(inspect=lambda _: 'same')['build'], 'false')
        self.assertEqual(self.plan(inspect=lambda image: 'other' if image.endswith(':latest') else 'same')['build'], 'true')
        self.assertEqual(self.plan(inspect=lambda image: None if image.endswith(':12.1') else 'same')['build'], 'true')
        self.assertEqual(self.plan()['build'], 'true')

    def test_forced_rebuild_does_not_depend_on_registry_checks(self):
        def fail(_):
            raise AssertionError('Force must bypass inspection')
        self.assertEqual(self.plan(force=True, inspect=fail)['build'], 'true')

    def test_weekly_marker_refreshes_base_images(self):
        first = self.plan()['tags'].splitlines()[-2]
        next_week = self.plan(now=datetime(2026, 10, 9, tzinfo=timezone.utc))['tags'].splitlines()[-2]
        self.assertNotEqual(first, next_week)

    def test_new_parent_commit_does_not_invalidate_source_tree_marker(self):
        first = self.plan()['tags'].splitlines()[-2]
        new_parent = self.plan(source='c' * 40)['tags'].splitlines()[-2]
        self.assertEqual(first, new_parent)

    def test_invalid_tag_inputs_are_rejected(self):
        for changes in [{'version': 'nightly'}, {'source': 'short'}, {'tree': 'bad'},
                        {'run_id': '../bad'}, {'attempt': 'bad\nvalue'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.plan(**changes)

    def test_registry_requires_both_platforms(self):
        manifest = {'digest': 'sha256:' + 'f' * 64, 'manifests': [
            {'platform': {'os': 'linux', 'architecture': 'amd64'}},
            {'platform': {'os': 'linux', 'architecture': 'arm64'}}]}
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(manifest))):
            self.assertEqual(module.inspect_digest('test:tag'), manifest['digest'])
        manifest['manifests'].pop()
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(manifest))):
            self.assertIsNone(module.inspect_digest('test:tag'))

    def test_registry_error_causes_retry_not_silent_success(self):
        with patch.object(module.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'docker')):
            self.assertIsNone(module.inspect_digest('test:tag'))

    def test_candidate_never_contains_a_rolling_alias(self):
        for channel in ['nightly', 'release']:
            result = self.plan(channel)
            self.assertEqual(result['candidate_image'], module.IMAGE + ':' + result['build_tag'])
            self.assertNotIn('\n', result['candidate_image'])

    def test_promotion_uses_candidate_digest_without_rebuild(self):
        digest = 'sha256:' + 'a' * 64
        for channel, version in [('nightly', 'nightly'), ('release', '12.1')]:
            run = Mock()
            marker = self.plan(channel)['marker']
            module.promote(channel, version, marker, digest, inspect=lambda _: digest, run=run)
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ['docker', 'buildx', 'imagetools', 'create'])
            self.assertEqual(command[-1], module.IMAGE + '@' + digest)
            promoted = [command[i + 1] for i, arg in enumerate(command) if arg == '--tag']
            expected = [module.IMAGE + ':' + tag for tag in [*module.aliases(channel, version), marker]]
            self.assertEqual(promoted, expected)

    def test_promotion_rejects_cross_channel_marker_or_mutable_source(self):
        for marker, digest in [(self.plan('nightly')['marker'], 'sha256:' + 'a' * 64),
                               (self.plan()['marker'], 'latest')]:
            run = Mock()
            with self.assertRaises(ValueError):
                module.promote('release', '12.1', marker, digest, run=run)
            run.assert_not_called()

    def test_promotion_rejects_incomplete_architecture_index(self):
        run = Mock()
        with self.assertRaises(RuntimeError):
            module.promote('release', '12.1', self.plan()['marker'], 'sha256:' + 'a' * 64,
                           inspect=lambda _: None, run=run)
        run.assert_not_called()

    def test_incomplete_alias_promotion_fails_for_retry(self):
        digest = 'sha256:' + 'a' * 64
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            module.promote('release', '12.1', self.plan()['marker'], digest,
                           inspect=lambda ref: None if ref.endswith(':stable') else digest,
                           run=Mock())


if __name__ == '__main__':
    unittest.main()
