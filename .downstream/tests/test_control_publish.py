"""Backup, atomic lease and dispatch boundaries; no forge writes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_sync import commit, git, write

spec = importlib.util.spec_from_file_location('control', Path(__file__).resolve().parents[1] / 'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-b', 'patch-queue')
        write(self.root, 'source.txt', 'first\n')
        self.old = commit(self.root, 'First')
        self.backup = Path(self.temp.name) / 'backup'

    def test_backup_roundtrip_and_corruption_fail_closed(self):
        control.backup(self.root, self.backup, self.old)
        control.verify_backup(self.backup, self.old)
        with (self.backup / 'control.bundle').open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'hash'):
            control.verify_backup(self.backup, self.old)

    def test_prerequisite_bundle_cannot_restore_empty_repository(self):
        self.backup.mkdir()
        write(self.root, 'source.txt', 'second\n')
        new = commit(self.root, 'Second')
        bundle = self.backup / 'control.bundle'
        git(self.root, 'bundle', 'create', str(bundle), 'HEAD', '^' + self.old)
        import hashlib
        (self.backup / 'backup.json').write_text(json.dumps({'control': new, 'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}))
        with self.assertRaises(subprocess.CalledProcessError):
            control.verify_backup(self.backup, new)

    def test_atomic_archive_and_exact_lease_real_remote(self):
        remote = Path(self.temp.name) / 'remote.git'
        subprocess.run(['git', 'clone', '--bare', str(self.root), str(remote)], check=True, capture_output=True)
        git(self.root, 'remote', 'add', 'origin', str(remote))
        write(self.root, 'source.txt', 'second\n')
        new = commit(self.root, 'Second')
        with patch.dict(os.environ, {'GH_TOKEN': 'unused-local-transport'}):
            control.push(self.root, self.old, new)
        self.assertEqual(git(remote, 'rev-parse', 'patch-queue'), new)
        archive = 'refs/tags/archive/patch-queue/' + self.old
        self.assertEqual(git(remote, 'cat-file', '-t', archive), 'commit')
        self.assertEqual(git(remote, 'rev-parse', archive), self.old)
        with patch.dict(os.environ, {'GH_TOKEN': 'unused-local-transport'}), self.assertRaisesRegex(RuntimeError, 'Control moved'):
            control.push(self.root, self.old, new)

    def test_existing_correct_archive_reused_with_explicit_lease(self):
        with patch.object(control, 'remote_head', side_effect=[self.old, self.old]), patch.dict(os.environ, {'GH_TOKEN': 'unused'}), patch.object(subprocess, 'run') as run:
            control.push(self.root, self.old, 'b' * 40)
            args = run.call_args.args[0]
            archive = 'refs/tags/archive/patch-queue/' + self.old
            self.assertIn('--atomic', args)
            self.assertIn('--force-with-lease=' + archive + ':' + self.old, args)
            self.assertIn(self.old + ':' + archive, args)

    def test_wrong_archive_ref_stops_before_push(self):
        with patch.object(control, 'remote_head', side_effect=[self.old, 'a' * 40]), patch.object(subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Archive tag'):
                control.push(self.root, self.old, 'b' * 40)
            run.assert_not_called()

    def test_dispatch_exact_control_preserves_rebuild_intent(self):
        for rebuild in [False, True]:
            with patch.object(subprocess, 'run') as run:
                control.dispatch(self.old, rebuild)
                payload = json.loads(run.call_args.kwargs['input'])
                self.assertEqual(payload, {'ref': 'patch-queue', 'inputs': {'expected_control': self.old, 'rebuild': rebuild}})

    def test_promotion_readback_must_match_before_dispatch(self):
        from contextlib import ExitStack
        new, upstream = 'b' * 40, 'c' * 40
        git(self.root, 'remote', 'add', 'origin', str(self.root))
        self.backup.mkdir()
        evidence = {'control': self.old, 'candidate': new, 'upstream': upstream,
                    'ref': 'refs/heads/master', 'tree': 'd' * 40}
        (self.backup / 'candidate.json').write_text(json.dumps(evidence))
        for readback in [[self.old, new, self.old], [self.old, '', self.old],
                         [self.old, new, 'e' * 40], [self.old, new, '']]:
            with ExitStack() as stack:
                stack.enter_context(patch.object(control, 'verify_backup'))
                stack.enter_context(patch.object(control, 'selection', return_value=('refs/heads/master', upstream)))
                stack.enter_context(patch.object(control, 'candidate', return_value={'source_sha': new, 'source_tree': 'd' * 40}))
                stack.enter_context(patch.object(control, 'push'))
                stack.enter_context(patch.object(control, 'remote_head', side_effect=readback))
                dispatch = stack.enter_context(patch.object(control, 'dispatch'))
                if readback[1:] == [new, self.old]:
                    control.promote(self.root, self.backup)
                    dispatch.assert_called_once_with(new, False)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'readback'):
                        control.promote(self.root, self.backup)
                    dispatch.assert_not_called()
        with patch.object(control, 'verify_backup'), patch.object(control, 'selection', return_value=('refs/heads/master', upstream)), patch.object(control, 'candidate', return_value={'source_sha': new, 'source_tree': 'd' * 40}), patch.object(control, 'push'), patch.object(control, 'remote_head', side_effect=[self.old, RuntimeError('read failure')]), patch.object(control, 'dispatch') as dispatch:
            with self.assertRaisesRegex(RuntimeError, 'read failure'):
                control.promote(self.root, self.backup)
            dispatch.assert_not_called()

    def test_expected_control_mismatch_stops_before_api_or_backup(self):
        with patch.object(control, 'selection') as select:
            with self.assertRaisesRegex(RuntimeError, 'another control'):
                control.plan(self.root, self.backup, 'a' * 40)
            select.assert_not_called()
            self.assertFalse(self.backup.exists())
