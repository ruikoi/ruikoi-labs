import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('payloads', ROOT / 'tools/payloads.py')
payloads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payloads)


class DownloadIntegrityTests(unittest.TestCase):
    def test_hash_accepts_correct_content_and_rejects_changed_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'source.tar'
            p.write_bytes(b'known source')
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            payloads.verify_file(p, digest)
            p.write_bytes(b'changed source')
            with self.assertRaises(ValueError):
                payloads.verify_file(p, digest)

    def test_archive_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'bad.tar'
            with tarfile.open(archive, 'w') as tf:
                item = tarfile.TarInfo('../outside')
                item.size = 1
                tf.addfile(item, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                payloads.safe_extract(archive, Path(d) / 'output')
            self.assertFalse((Path(d) / 'outside').exists())

    def test_archive_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'bad-link.tar'
            with tarfile.open(archive, 'w') as tf:
                item = tarfile.TarInfo('link')
                item.type = tarfile.SYMTYPE
                item.linkname = '../../outside'
                tf.addfile(item)
            with self.assertRaises(ValueError):
                payloads.safe_extract(archive, Path(d) / 'output')

    def test_normal_archive_is_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / 'good.tar'
            with tarfile.open(archive, 'w') as tf:
                item = tarfile.TarInfo('source/README')
                item.size = 2
                tf.addfile(item, io.BytesIO(b'ok'))
            out = Path(d) / 'output'
            out.mkdir()
            payloads.safe_extract(archive, out)
            self.assertEqual((out / 'source/README').read_bytes(), b'ok')


if __name__ == '__main__':
    unittest.main()
