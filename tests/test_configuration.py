import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.configure import configure


class ConfigurationTests(unittest.TestCase):
    def test_preserves_values_and_creates_private_random_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text('OPENAI_API_KEY=test-secret-placeholder\nOPENAI_MODEL=model-name\nDATABASE_PASSWORD=\n')
            with contextlib.redirect_stdout(io.StringIO()):
                configure(path)
            values = dict(line.split('=', 1) for line in path.read_text().splitlines())
            self.assertEqual(values['OPENAI_API_KEY'], 'test-secret-placeholder')
            self.assertGreater(len(values['DATABASE_PASSWORD']), 30)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_atomic_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            original = 'OPENAI_API_KEY=test-secret-placeholder\n'
            path.write_text(original)
            with patch('scripts.configure.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    configure(path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
