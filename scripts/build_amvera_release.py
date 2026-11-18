"""Allowlisted source package, manifest and no local database/configuration."""
import hashlib,json,re
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[1]
def main(output_dir=None):
    from app.cat_assets import private_release_files
    allowed=set(private_release_files())
    for folder in ('app','migrations'):
        for path in (ROOT/folder).rglob('*'):
            rel=path.relative_to(ROOT).as_posix()
            if not path.is_file() or path.is_symlink() or '__pycache__' in path.parts:continue
            if path.suffix.lower() not in ('.py','.html','.css','.js','.svg','.png','.ico','.jpg','.jpeg','.webp','.woff','.woff2','.mako'):continue
            if rel.startswith('app/private_assets/'):continue
            allowed.add(path)
    for name in ('Dockerfile','.dockerignore','amvera.yaml','alembic.ini','deploy/requirements.lock.txt','deploy/amvera.env.example',
                 'scripts/amvera_runtime.py','scripts/reminder_worker.py','scripts/personal_reminder_worker.py',
                 'scripts/backup.py','scripts/check_storage.py','scripts/amvera_schema_check.py',
                 'docs/TUTOR3_AMVERA_RELEASE_PLAN.md'):
        allowed.add(ROOT/name)
    output=(Path(output_dir) if output_dir is not None else ROOT/'dist')/'tutor-3.0-amvera-0016.zip';output.parent.mkdir(exist_ok=True)
    manifest={}
    # Rebuilding the generated archive is safe; source and data are never modified.
    with TemporaryDirectory(dir=output.parent) as stage:
        candidate = Path(stage) / output.name
        with ZipFile(candidate,'w',ZIP_DEFLATED) as archive:
            for path in sorted(allowed):
                data=path.read_bytes();rel=path.relative_to(ROOT).as_posix()
                if path.suffix in ('.py','.html','.js','.css','.md','.yaml','.txt','.json'):
                    if re.search(rb'[0-9]{7,12}:[A-Za-z0-9_-]{30,}',data):raise RuntimeError('Possible bot token; package rejected')
                    if b'-----BEGIN PRIVATE KEY-----' in data:raise RuntimeError('Private key; package rejected')
                archive.writestr(rel,data);manifest[rel]=hashlib.sha256(data).hexdigest()
            archive.writestr('MANIFEST.sha256.json',json.dumps(manifest,indent=2))
        with ZipFile(candidate) as verified:
            if verified.testzip() or set(verified.namelist()) != set(manifest) | {'MANIFEST.sha256.json'}:
                raise RuntimeError('Invalid release archive')
        candidate.replace(output)
    (output.with_suffix('.manifest.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(output),'files':len(manifest),'sha256':digest}))
if __name__=='__main__':main()
