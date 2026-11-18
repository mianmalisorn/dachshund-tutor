import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scripts.amvera_runtime import bootstrap, report_startup_error


class DiagnosticsTests(unittest.TestCase):
    def test_known_error_visible(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            report_startup_error(RuntimeError('Interrupted initialization: inspect .initializing before retry'))
        self.assertIn('Interrupted initialization', output.getvalue())

    def test_private_exception_hidden(self):
        for error in (RuntimeError('password=DO_NOT_LOG'), ValueError('token=DO_NOT_LOG')):
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                report_startup_error(error)
            self.assertNotIn('DO_NOT_LOG', output.getvalue())

    def test_invalid_password_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'tutor.db'
            with patch.dict(os.environ, {'INIT_NEW_DATABASE': 'true', 'BOOTSTRAP_ADMIN_USERNAME': 'malisorn', 'BOOTSTRAP_ADMIN_PASSWORD': 'short'}):
                with self.assertRaisesRegex(RuntimeError, 'BOOTSTRAP_ADMIN_PASSWORD'):
                    bootstrap(SimpleNamespace(), database)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_username_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'INIT_NEW_DATABASE': 'true', 'BOOTSTRAP_ADMIN_USERNAME': 'Malisorn', 'BOOTSTRAP_ADMIN_PASSWORD': 'x' * 32}):
                with self.assertRaisesRegex(RuntimeError, 'BOOTSTRAP_ADMIN_USERNAME'):
                    bootstrap(SimpleNamespace(), Path(directory) / 'tutor.db')


if __name__ == '__main__':
    unittest.main()
