"""Freshness gates and catch-up events must not change channel/source selection."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import freshness as module


class FreshnessTests(unittest.TestCase):
    def test_same_master_commit_is_current(self):
        with patch.object(module, 'remote_commit', return_value='a' * 40):
            self.assertEqual(module.check('nightly', 'refs/heads/master', 'a' * 40)['current'], 'true')

    def test_master_moving_during_build_is_stale(self):
        with patch.object(module, 'remote_commit', return_value='b' * 40):
            self.assertEqual(module.check('nightly', 'refs/heads/master', 'a' * 40)['current'], 'false')

    def test_new_release_is_stale_even_when_it_points_at_same_commit(self):
        with patch.object(module, 'resolve', return_value={'upstream_ref': 'refs/tags/v12.2'}), \
                patch.object(module, 'remote_commit', return_value='a' * 40):
            result = module.check('release', 'refs/tags/v12.1', 'a' * 40)
            self.assertEqual(result['current'], 'false')
            self.assertEqual(result['current_ref'], 'refs/tags/v12.2')

    def test_release_does_not_compare_against_master(self):
        with patch.object(module, 'resolve', return_value={'upstream_ref': 'refs/tags/v12.1'}), \
                patch.object(module, 'remote_commit', return_value='a' * 40) as remote:
            self.assertEqual(module.check('release', 'refs/tags/v12.1', 'a' * 40)['current'], 'true')
            remote.assert_called_once_with('refs/tags/v12.1')

    def test_annotated_tag_uses_peeled_commit(self):
        ref = 'refs/tags/v12.1'
        output = 'a' * 40 + '\t' + ref + '\n' + 'b' * 40 + '\t' + ref + '^{}\n'
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)):
            self.assertEqual(module.remote_commit(ref), 'b' * 40)

    def test_branch_uses_commit_not_payload(self):
        output = 'a' * 40 + '\trefs/heads/master\n'
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            self.assertEqual(module.remote_commit('refs/heads/master'), 'a' * 40)
            self.assertIn(module.UPSTREAM, run.call_args.args[0])

    def test_invalid_refs_and_unexpected_responses_fail_closed(self):
        for ref in ['master', 'refs/heads/untrusted', '--upload-pack=bad', 'refs/tags/v12.1\n']:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                module.remote_commit(ref)
        for text in ['', 'short\trefs/heads/master\n', 'a' * 40 + '\trefs/heads/other\n']:
            with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, text)):
                with self.assertRaises(ValueError):
                    module.remote_commit('refs/heads/master')

    def test_network_error_does_not_become_current(self):
        with patch.object(module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                module.check('nightly', 'refs/heads/master', 'a' * 40)

    def test_dispatch_is_fixed_repository_and_wakeup_only(self):
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': module.DESTINATION, 'GH_TOKEN': 'test-only'}), \
                patch.object(module.urllib.request, 'urlopen', return_value=response) as send:
            module.dispatch()
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.github.com/repos/' + module.DESTINATION + '/dispatches')
            self.assertEqual(json.loads(request.data), {'event_type': 'upstream-updated'})
            self.assertEqual(request.method, 'POST')

    def test_dispatch_refuses_other_repository_or_missing_token(self):
        for env in [{'GITHUB_REPOSITORY': 'someone/else', 'GH_TOKEN': 'test-only'},
                    {'GITHUB_REPOSITORY': module.DESTINATION}]:
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(module.urllib.request, 'urlopen') as send:
                with self.assertRaises(ValueError):
                    module.dispatch()
                send.assert_not_called()

    def test_main_dispatches_only_after_a_detected_change(self):
        for current in ['true', 'false']:
            with patch.object(sys, 'argv', ['freshness.py', 'nightly', '--dispatch-if-changed']), \
                    patch.dict(os.environ, {'UPSTREAM_REF': 'refs/heads/master', 'UPSTREAM_SHA': 'a' * 40}), \
                    patch.object(module, 'check', return_value={'current': current}), \
                    patch.object(module, 'outputs'), patch.object(module, 'dispatch') as send:
                module.main()
                self.assertEqual(send.call_count, int(current == 'false'))

    def test_failed_check_never_dispatches_an_unverified_wakeup(self):
        with patch.object(sys, 'argv', ['freshness.py', 'nightly', '--dispatch-if-changed']), \
                patch.dict(os.environ, {'UPSTREAM_REF': 'refs/heads/master', 'UPSTREAM_SHA': 'a' * 40}), \
                patch.object(module, 'check', side_effect=OSError('offline')), \
                patch.object(module, 'dispatch') as send:
            with self.assertRaises(OSError):
                module.main()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
