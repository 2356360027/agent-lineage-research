"""Mocked download tests: no network, API usage or model execution."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.fetch_deepseek_tokenizer_reference import fetch, FILES, REVISION


class ReferenceFetchTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'runtime' / 'runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'reference'

    def test_pinned_files_and_hashes(self):
        with patch('urllib.request.urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'public')) as net:
            with redirect_stdout(io.StringIO()): result = fetch(self.output)
        self.assertEqual(set(result['files']), set(FILES))
        self.assertEqual(result['inference_calls'], 0)
        self.assertEqual(net.call_count, 5)
        for call in net.call_args_list:
            self.assertIn('/resolve/' + REVISION + '/', call.args[0])
        self.assertEqual(result['status'], 'DOWNLOADED_NOT_EXECUTED')

    def test_partial_failure_retained_without_retry(self):
        with patch('urllib.request.urlopen', side_effect=[io.BytesIO(b'license'), OSError('test')]) as net:
            with redirect_stdout(io.StringIO()), self.assertRaises(OSError): fetch(self.output)
        self.assertEqual(net.call_count, 2)
        self.assertEqual((self.output / 'LICENSE').read_bytes(), b'license')
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'FAILED_PARTIAL_RETAINED')
        self.assertEqual(set(manifest['files']), {'LICENSE'})

    def test_existing_directory_not_overwritten(self):
        self.output.mkdir()
        with patch('urllib.request.urlopen') as net, self.assertRaises(FileExistsError):
            fetch(self.output)
        net.assert_not_called()


if __name__ == '__main__': unittest.main()
