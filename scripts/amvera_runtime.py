"""One instance: web + calendar + personal worker. No implicit existing-DB upgrades."""
import contextlib
import ipaddress
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.production import production_settings
from app.db import init_db, migrate
from app.models import User
from app.security import password_hash

@contextlib.contextmanager
def instance_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / '.runtime.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('Another runtime or maintenance operation holds the data lock') from None
    try:
        yield
    finally:
        stream.close()

def prepare():
    settings, _ = production_settings()
    root = Path(os.environ.get('TUTOR_DATA_DIR', '/data')).resolve()
    database = Path(make_url(settings.database_url).database).resolve()
    uploads = settings.upload_root.resolve()
    if database.parent != root or not uploads.is_relative_to(root) or uploads == root:
        raise RuntimeError('Database must be directly in TUTOR_DATA_DIR; uploads in its separate subdirectory')
    return settings, root, database

def bootstrap(settings, database):
    if not database.exists():
        if os.getenv('INIT_NEW_DATABASE') != 'true':
            raise RuntimeError('Database absent: explicit INIT_NEW_DATABASE=true required')
        username = os.getenv('BOOTSTRAP_ADMIN_USERNAME', '')
        password = os.getenv('BOOTSTRAP_ADMIN_PASSWORD', '')
        if not re.fullmatch(r'[a-z0-9_.-]{3,60}', username) or not 16 <= len(password) <= 256:
            raise RuntimeError('New database requires admin login and random password of at least 16 characters')
        # Migrate a sibling temporary database, then publish only after admin commit.
        # Failed initialization never leaves a partly initialized target database.
        temporary = database.with_name(database.name + '.initializing')
        if temporary.exists():
            raise RuntimeError('Interrupted initialization: inspect .initializing before retry')
        url = 'sqlite:///' + temporary.as_posix()
        migrate(url)
        engine, factory = init_db(url)
        try:
            with factory() as db:
                db.add(User(username=username, display_name='Пенсионный фонд', role='admin',
                            password_hash=password_hash(password), must_change_password=False))
                db.commit()
            with engine.connect() as connection:
                connection.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            engine.dispose()
        temporary.rename(database)
    engine, factory = init_db(settings.database_url)
    try:
        with factory() as db:
            if not db.scalar(select(User.id).where(User.role == 'admin')):
                raise RuntimeError('Existing database has no administrator; use explicit offline maintenance')
    finally:
        engine.dispose()
    settings.upload_root.mkdir(parents=True, exist_ok=True)

def commands():
    from app.reminders import TelegramConfig
    from app.personal_telegram import PersonalConfig
    if os.getenv('TELEGRAM_ENABLED') == 'true' and not TelegramConfig.from_env().configured:
        raise RuntimeError('Enabled calendar delivery requires complete Telegram configuration')
    if os.getenv('PERSONAL_TELEGRAM_ENABLED') == 'true' and not PersonalConfig.from_env().configured:
        raise RuntimeError('Enabled personal delivery requires complete Telegram configuration')
    proxies = os.getenv('TRUSTED_PROXY_IPS', '127.0.0.1')
    for item in proxies.split(','):
        ipaddress.ip_network(item.strip(), strict=False)
    result = [
        [sys.executable, '-m', 'uvicorn', 'app.production:create_app', '--factory',
         '--host', '0.0.0.0', '--port', os.getenv('PORT', '8000'), '--workers', '1',
         '--proxy-headers', '--forwarded-allow-ips', proxies],
        [sys.executable, '-m', 'scripts.reminder_worker'],
        [sys.executable, '-m', 'scripts.personal_reminder_worker', '--idle-unconfigured'],
    ]
    if os.getenv('TELEGRAM_ENABLED') == 'true': result[1].append('--send')
    if os.getenv('PERSONAL_TELEGRAM_ENABLED') == 'true': result[2].append('--send')
    return result

def main():
    processes = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings, root, database = prepare()
    with instance_lock(root):
        child_commands = commands()
        bootstrap(settings, database)
        env = {k:v for k,v in os.environ.items() if not k.startswith('BOOTSTRAP_ADMIN_')}
        try:
            for name, command in zip(('web', 'calendar', 'personal'), child_commands):
                child = subprocess.Popen(command, env=env)
                processes.append(child)
                print(f'Runtime started {name} pid={child.pid}', flush=True)
            while not stopping:
                if any(child.poll() is not None for child in processes):
                    raise RuntimeError('A child exited; stopping the complete runtime for platform restart')
                time.sleep(0.25)
        finally:
            for child in processes:
                if child.poll() is None:
                    if os.name == 'nt':
                        # Windows venv launchers own a separate Python child.
                        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    else: child.terminate()
            deadline = time.monotonic() + 12
            for child in processes:
                try: child.wait(timeout=max(0.1, deadline-time.monotonic()))
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            print('Runtime stopped all children', flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as error:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)

