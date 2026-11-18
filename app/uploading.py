"""Keep private files outside /static. Store random filenames; serve only via authorized route."""
from pathlib import Path
from uuid import uuid4
import os
import logging
from fastapi import HTTPException
from starlette.datastructures import UploadFile
from .models import Attachment
from .file_validation import validate_content

log = logging.getLogger(__name__)


def cleanup_paths(paths):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.error('Upload cleanup failed; storage reconciliation required')


async def save_uploads(files: list[UploadFile], settings):
    for item in files:
        if not isinstance(item, UploadFile):
            raise HTTPException(400, 'Файлы должны загружаться через поле выбора файлов')
    meaningful = [f for f in files if f.filename]
    if len(meaningful) > settings.max_files_per_upload:
        raise HTTPException(400, f'Максимум {settings.max_files_per_upload} файлов за раз')
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    results = []
    paths = []
    try:
        for item in meaningful:
            filename = Path(item.filename.replace('\\', '/')).name[:255]
            if Path(filename).suffix.lower() not in settings.allowed_extensions:
                raise HTTPException(400, f'Нельзя загрузить файл «{filename}»: неподдерживаемый формат')
            storage_name = uuid4().hex
            location = settings.upload_root / storage_name
            paths.append(location)
            size = 0
            with location.open('xb') as target:
                while True:
                    chunk = await item.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > settings.max_file_mb * 1024 * 1024:
                        raise HTTPException(413, f'«{filename}» больше {settings.max_file_mb} МБ')
                    target.write(chunk)
            if size == 0:
                raise HTTPException(400, f'Файл «{filename}» пуст')
            validate_content(location, Path(filename).suffix.lower())
            results.append(Attachment(filename=filename, storage_name=storage_name, size_bytes=size))
        return results
    except BaseException:
        cleanup_paths(paths)
        raise
    finally:
        for item in files:
            await item.close()


def cleanup_records(records, settings):
    cleanup_paths([settings.upload_root / rec.storage_name for rec in records])
