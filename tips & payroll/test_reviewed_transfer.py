import base64
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from nacl.secret import SecretBox
from reviewed_transfer import unpack_package


class ReviewedTransferTests(unittest.TestCase):
    def package(self, extra_name=None):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            for name in ['tcp-audit.json', 'native-payroll.xml', 'gateway-input.json']:
                archive.writestr(name, 'source')
            for i in range(21):
                archive.writestr(extra_name if i == 0 and extra_name else f'{i}.xlsx', b'workbook')
        key = b'x' * SecretBox.KEY_SIZE
        cipher = bytes(SecretBox(key).encrypt(buf.getvalue()))
        return cipher, base64.b64encode(key).decode(), hashlib.sha256(cipher).hexdigest()

    def test_roundtrip_restores_only_expected_files(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / 'package'
            unpack_package(*self.package(), out)
            self.assertEqual(len(list(out.iterdir())), 24)

    def test_modified_upload_rejected(self):
        cipher, key, digest = self.package()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'differs'):
                unpack_package(cipher + b'x', key, digest, Path(root) / 'package')

    def test_path_traversal_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / 'package'
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                unpack_package(*self.package('../outside.xlsx'), out)
            self.assertFalse(out.exists())
