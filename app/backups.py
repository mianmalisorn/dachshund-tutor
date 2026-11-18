"""Offline SQLite snapshots and verified restore to a NEW directory only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy.engine import make_url
import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
from contextlib import closing


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def check_database(path):
    with closing(sqlite3.connect(path)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity check failed')
        names=[row[0] for row in db.execute('SELECT storage_name FROM attachments')]
        if any(not re.fullmatch('[a-f0-9]{32}',name) for name in names):
            raise ValueError('Invalid storage filename')
        return names


def create_backup(settings, destination, *, writes_stopped=False):
    if not writes_stopped:
        raise ValueError('Stop the application AND reminder worker before backing up database and files together')
    url=make_url(settings.database_url)
    if url.get_backend_name()!='sqlite' or not url.database or url.database==':memory:':
        raise ValueError('This backup tool supports file-based SQLite only')
    source=Path(url.database).resolve()
    if not source.is_file():
        raise ValueError('Database does not exist')
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory() as temp:
        root=Path(temp)
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(root/'portal.db')) as copy:
            original.backup(copy)
        names=check_database(root/'portal.db')
        (root/'uploads').mkdir()
        upload_root=settings.upload_root.resolve()
        for name in names:
            source_file=(upload_root/name).resolve()
            if source_file.parent!=upload_root or not source_file.is_file():
                raise ValueError('Referenced attachment is missing or outside storage')
            shutil.copyfile(source_file,root/'uploads'/name)
        hashes={str(p.relative_to(root)).replace('\\','/'):digest(p) for p in root.rglob('*') if p.is_file()}
        (root/'manifest.json').write_text(json.dumps({'version':1,'sha256':hashes}),encoding='utf-8')
        try:
            with destination.open('xb') as output, tarfile.open(fileobj=output,mode='w:gz') as archive:
                for name in ['manifest.json',*hashes]:
                    archive.add(root/name,arcname=name,recursive=False)
        except FileExistsError:
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination


def restore_backup(archive_path, destination):
    destination=Path(destination).resolve()
    if destination.exists():
        raise ValueError('Restore destination must not exist; existing data is never overwritten')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        root=Path(temp)/'restored'; root.mkdir()
        with tarfile.open(archive_path,'r:gz') as archive:
            names=set()
            for member in archive:
                if (not member.isfile() or member.name in names or
                    not (member.name in {'portal.db','manifest.json'} or re.fullmatch(r'uploads/[a-f0-9]{32}',member.name))):
                    raise ValueError('Unsafe or duplicate archive entry')
                names.add(member.name)
                target=root/member.name; target.parent.mkdir(exist_ok=True)
                with archive.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source,output)
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        hashes=manifest['sha256']
        if manifest['version']!=1 or set(hashes)!=(names-{'manifest.json'}) or 'portal.db' not in hashes:
            raise ValueError('Invalid manifest')
        for name,expected in hashes.items():
            if digest(root/name)!=expected:
                raise ValueError('Backup checksum mismatch')
        referenced=check_database(root/'portal.db')
        if {'uploads/'+name for name in referenced} != {name for name in hashes if name.startswith('uploads/')}:
            raise ValueError('Database and files disagree')
        (root/'uploads').mkdir(exist_ok=True)
        root.rename(destination)
    return destination
