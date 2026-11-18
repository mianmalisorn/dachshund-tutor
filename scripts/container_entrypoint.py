"""Repair mounted storage ownership, then permanently drop root privileges.

Only used by the Docker image. Never follows symlinks or modifies file contents.
"""
import os
from pathlib import Path
import stat
import sys

APP_UID = 10001
APP_GID = 10001


def repair_entry(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise RuntimeError('Unsupported special file in persistent storage')
    if (info.st_uid, info.st_gid) != (APP_UID, APP_GID):
        os.chown(path, APP_UID, APP_GID, follow_symlinks=False)
    required = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
    mode = stat.S_IMODE(info.st_mode)
    if mode & required != required:
        os.chmod(path, mode | required, follow_symlinks=False)


def prepare_volume(root):
    if root.is_symlink():
        raise RuntimeError('Persistent storage root must not be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    repair_entry(root)
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + files:
            repair_entry(Path(directory) / name)


def main():
    # Fixed mount boundary: a bad environment variable must not cause chown /.
    root = Path('/data')
    if Path(os.getenv('TUTOR_DATA_DIR', '/data')) != root:
        raise RuntimeError('Docker entrypoint requires TUTOR_DATA_DIR=/data')
    if os.geteuid() == 0:
        print('Container stage: preparing persistent volume ownership', flush=True)
        prepare_volume(root)
        os.setgroups([])
        os.setgid(APP_GID)
        os.setuid(APP_UID)
    if (os.geteuid(), os.getegid()) != (APP_UID, APP_GID):
        raise RuntimeError('Container must run as the dedicated application user')
    print('Container stage: starting runtime as uid=10001 gid=10001', flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'scripts.amvera_runtime'])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No arbitrary paths, environment values, or exception messages in logs.
        print(f'Container startup failed ({type(error).__name__}); check /data ownership and mount permissions', file=sys.stderr)
        raise SystemExit(1)
