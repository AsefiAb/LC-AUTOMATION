import base64
import unittest
from unittest.mock import patch, Mock
from connect_onedrive import GitHubSecrets


class SecretUploadTests(unittest.TestCase):
    def test_only_ciphertext_is_uploaded_to_fixed_repository(self):
        try:
            from nacl.public import PrivateKey, SealedBox
        except ImportError:
            self.skipTest('Optional local setup dependency not installed')
        private = PrivateKey.generate()
        writer = GitHubSecrets.__new__(GitHubSecrets)
        writer.box = SealedBox(private.public_key)
        writer.key_id = 'test-key'
        writer.headers = {'Authorization': 'Bearer test'}
        writer.url = 'https://api.github.com/repos/AsefiAb/LC-AUTOMATION'
        with patch('connect_onedrive.requests.put', return_value=Mock(status_code=201)) as put:
            writer.save('MS_REFRESH_TOKEN', 'test-secret-value')
            payload = put.call_args.kwargs['json']
            self.assertNotIn('test-secret-value', str(put.call_args))
            decrypted = SealedBox(private).decrypt(base64.b64decode(payload['encrypted_value']))
            self.assertEqual(decrypted, b'test-secret-value')
            with self.assertRaises(RuntimeError):
                writer.save('UNRELATED_SECRET', 'value')
