import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.archive_completed_deepseek_batch import file_hashes, verify_tar, recover


class ArchiveTests(unittest.TestCase):
    def test_verified_and_tampered_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'original'
            root.mkdir()
            (root/'record.json').write_bytes(b'{"status":"recorded"}')
            hashes = file_hashes(root)
            good = Path(tmp)/'good.tar.gz'
            with tarfile.open(good, 'x:gz') as stream:
                stream.add(root, arcname=root.name)
            self.assertEqual(verify_tar(good, root.name, hashes), 1)
            self.assertEqual(file_hashes(root), hashes)
            for kind in ('changed', 'duplicate', 'extra', 'missing'):
                path = Path(tmp)/(kind+'.tar.gz')
                with tarfile.open(path, 'x:gz') as stream:
                    if kind != 'missing':
                        data = b'wrong' if kind == 'changed' else (root/'record.json').read_bytes()
                        item = tarfile.TarInfo('original/record.json')
                        item.size = len(data)
                        stream.addfile(item, io.BytesIO(data))
                        if kind == 'duplicate':
                            stream.addfile(item, io.BytesIO(data))
                        if kind == 'extra':
                            item = tarfile.TarInfo('original/extra')
                            item.size = 1
                            stream.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    verify_tar(path, root.name, hashes)

    def test_requires_process_check(self):
        with self.assertRaisesRegex(ValueError, 'process check'):
            recover('unused', 'unused', 'unused')


if __name__ == '__main__':
    unittest.main()
