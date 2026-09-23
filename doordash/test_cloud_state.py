import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cloud_state


class CloudStateTests(unittest.TestCase):
    def test_round_trip_preserves_history_and_uncertain_email(self):
        saved = {'version': 1, 'ledger': {'version': 1, 'orders': {'example': {'status': 'pending'}}},
                 'email_receipts': {'2026-09-23': {'status': 'sending'}},
                 'tracker_snapshot': {'version': 1, 'platforms': {'Uber Eats': {'recovered_cents': 1000}}}}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DOORDASH_DATA_DIR': folder}):
            with patch.object(cloud_state, 'request', return_value={
                    'sha': 'original-sha', 'content': base64.b64encode(json.dumps(saved).encode()).decode()}):
                cloud_state.restore()
            with patch.object(cloud_state, 'request', return_value={'content': {'sha': 'next-sha'}}) as put:
                cloud_state.persist()
                payload = put.call_args.kwargs['json']
                self.assertEqual(payload['sha'], 'original-sha')
                self.assertEqual(json.loads(base64.b64decode(payload['content'])), saved)
                self.assertEqual((Path(folder) / 'cloud-sha').read_text(), 'next-sha')

    def test_missing_restore_cannot_replace_cloud_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DOORDASH_DATA_DIR': folder}), \
                patch.object(cloud_state, 'request') as request:
            with self.assertRaises(FileNotFoundError): cloud_state.persist()
            request.assert_not_called()
