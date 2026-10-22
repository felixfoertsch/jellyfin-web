"""Publisher rejects mismatched evidence before network or write operations."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('publisher', Path(__file__).resolve().parents[1] / 'publish.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublishTests(unittest.TestCase):
    def test_untrusted_control_and_channel_never_reach_actuator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            for control, channel in [('b' * 40, 'release'), ('a' * 40, 'nightly')]:
                path.write_text(json.dumps({'CONTROL_SHA': control, 'CHANNEL': channel}))
                with patch.dict(os.environ, CONTROL_SHA='a' * 40, CHANNEL='release'), patch.object(module.subprocess, 'check_output') as network:
                    with self.assertRaisesRegex(ValueError, 'mismatch'):
                        module.publish(path)
                    network.assert_not_called()

    def test_changed_queue_never_reaches_reconstruction(self):
        data = {key: 'a' * 40 for key in ['CONTROL_SHA', 'UPSTREAM_SHA', 'SOURCE_SHA', 'SOURCE_TREE', 'EXPECTED_BASE']}
        data['CHANNEL'] = 'release'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            path.write_text(json.dumps(data))
            with patch.dict(os.environ, CONTROL_SHA='a' * 40, CHANNEL='release'), patch.object(module.subprocess, 'check_output', return_value='b' * 40 + '\trefs/heads/patch-queue\n'), patch.object(module.subprocess, 'run') as actuator:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    module.publish(path)
                actuator.assert_not_called()
