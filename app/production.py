"""Explicit deployment entry point. Never falls back to the local .env."""
import os
import re
from pathlib import Path
from sqlalchemy.engine import make_url
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ROOT, Settings


def production_settings():
    required = ('APP_ENV','SECRET_KEY','DATABASE_URL','UPLOAD_ROOT','ALLOWED_HOSTS','SECURE_COOKIES')
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError('Production requires explicit APP_ENV, SECRET_KEY, DATABASE_URL, UPLOAD_ROOT, ALLOWED_HOSTS and SECURE_COOKIES')
    secret = os.environ['SECRET_KEY']
    if os.environ['APP_ENV'] != 'production' or os.environ['SECURE_COOKIES'].lower() != 'true':
        raise RuntimeError('Production requires APP_ENV=production and SECURE_COOKIES=true')
    if len(secret) < 48 or 'REPLACE' in secret.upper() or len(set(secret)) < 12:
        raise RuntimeError('Generate a new random production SECRET_KEY of at least 48 characters')
    hosts = tuple(x.strip().lower() for x in os.environ['ALLOWED_HOSTS'].split(','))
    if not hosts or any(not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', h)
                        or h.endswith(('.example','.invalid','.test','.localhost')) or h=='example.com' for h in hosts):
        raise RuntimeError('ALLOWED_HOSTS must contain explicit real DNS names, without scheme, wildcard or port')
    url = make_url(os.environ['DATABASE_URL'])
    if url.get_backend_name() != 'sqlite' or not url.database:
        raise RuntimeError('This deployment recipe has only been prepared for file-based SQLite')
    database = Path(url.database)
    uploads = Path(os.environ['UPLOAD_ROOT'])
    for path in (database, uploads):
        if not path.is_absolute() or path.resolve().is_relative_to(ROOT.resolve()) or any(p.startswith('.test-') for p in path.parts):
            raise RuntimeError('Production data must use absolute paths outside the source tree and demo directories')
    if database.resolve().is_relative_to(uploads.resolve()):
        raise RuntimeError('Keep the database outside the upload directory')
    return Settings(secret_key=secret, database_url=str(url), upload_root=uploads,
                    secure_cookies=True, timezone=os.getenv('APP_TIMEZONE','Europe/Moscow'),
                    max_file_mb=int(os.getenv('MAX_FILE_MB','15')),
                    max_files_per_upload=int(os.getenv('MAX_FILES_PER_UPLOAD','5')),
                    max_request_mb=int(os.getenv('MAX_REQUEST_MB','76')),
                    allowed_extensions=tuple(x.strip().lower() for x in os.getenv('ALLOWED_EXTENSIONS','.pdf,.jpg,.jpeg,.png,.webp,.docx,.xlsx,.pptx,.odt,.txt,.rtf').split(','))), hosts


def create_app():
    from .main import create_app as create_local_app
    settings, hosts = production_settings()
    app = create_local_app(settings)
    app.add_middleware(HTTPSRedirectMiddleware)
    # Validate Host before redirecting, so it cannot become an open redirect.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts), www_redirect=False)
    return app
