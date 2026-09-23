import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_sessions
from test_altametrics_session import session


class LocalSessionTests(unittest.TestCase):
    def test_hosted_runner_cannot_use_local_logins(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                                    'GITHUB_REPOSITORY': 'AsefiAb/LC-AUTOMATION'}):
            with self.assertRaisesRegex(RuntimeError, 'owner-operated'):
                local_sessions.require_local_windows()

    @unittest.skipUnless(os.name == 'nt', 'Windows encryption integration test')
    def test_encrypted_roundtrip_and_updated_cache_precedes_bootstrap_secret(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                'LOCALAPPDATA': folder, 'GITHUB_ACTIONS': '',
                'CO_ALTAMETRICS_SESSION': json.dumps(session())}):
            original = local_sessions.load('CO')
            updated = session()
            updated['state']['cookies'][0]['value'] = 'rotated-private-cookie'
            local_sessions.save('CO', updated)
            encrypted = local_sessions.path_for('CO').read_bytes()
            self.assertNotIn(b'rotated-private-cookie', encrypted)
            self.assertEqual(local_sessions.load('CO'), updated)
            self.assertNotEqual(local_sessions.load('CO'), original)
            with self.assertRaises(ValueError):
                local_sessions.save('TX', updated)
            local_sessions.path_for('CO').write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError, 'reconnect'):
                local_sessions.load('CO')

