import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from scripts import container_entrypoint as entry


class VolumeTests(unittest.TestCase):
    def test_preserves_database_and_repairs_owner_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            root.mkdir()
            uploads = root / 'uploads'
            uploads.mkdir()
            database = root / 'tutor.db'
            database.write_bytes(b'existing database contents')
            database.chmod(0o400)
            with patch.object(entry, 'APP_UID', os.getuid()), patch.object(entry, 'APP_GID', os.getgid()):
                entry.prepare_volume(root)
            self.assertEqual(database.read_bytes(), b'existing database contents')
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            self.assertFalse((root / '.runtime.lock').exists())

    def test_does_not_follow_external_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / 'outside'
            outside.write_text('private')
            root = Path(tmp) / 'data'
            root.mkdir()
            (root / 'linked').symlink_to(outside)
            with patch.object(entry.os, 'chown') as chown:
                entry.prepare_volume(root)
            self.assertNotIn(outside, [call.args[0] for call in chown.call_args_list])
            self.assertNotIn(root / 'linked', [call.args[0] for call in chown.call_args_list])
            self.assertEqual(outside.read_text(), 'private')

    def test_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            root.symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaises(RuntimeError):
                entry.prepare_volume(root)

    def test_drops_privileges_before_exec(self):
        with patch.dict(os.environ, {'TUTOR_DATA_DIR': '/data'}), \
             patch.object(entry.os, 'geteuid', side_effect=[0, 10001]), \
             patch.object(entry.os, 'getegid', return_value=10001), \
             patch.object(entry, 'prepare_volume') as prepare, \
             patch.object(entry.os, 'setgroups') as groups, \
             patch.object(entry.os, 'setgid') as gid, \
             patch.object(entry.os, 'setuid') as uid, \
             patch.object(entry.os, 'execv') as execute:
            entry.main()
            prepare.assert_called_once_with(Path('/data'))
            groups.assert_called_once_with([])
            gid.assert_called_once_with(10001)
            uid.assert_called_once_with(10001)
            execute.assert_called_once_with(entry.sys.executable, [entry.sys.executable, '-m', 'scripts.amvera_runtime'])

    def test_rejects_unsafe_root(self):
        with patch.dict(os.environ, {'TUTOR_DATA_DIR': '/'}):
            with self.assertRaises(RuntimeError):
                entry.main()
