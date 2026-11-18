"""Настройки, берём из переменных окружения и .env (без дополнительных пакетов)."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    timezone: str = 'Europe/Moscow'
    secure_cookies: bool = False
    max_file_mb: int = 15
    max_files_per_upload: int = 5
    upload_root: Path = ROOT / 'instance' / 'uploads'
    testing: bool = False
    demo_mode: bool = False
    allowed_extensions: tuple[str, ...] = ('.pdf','.jpg','.jpeg','.png','.webp','.docx','.xlsx','.pptx','.odt','.txt','.rtf')
    max_request_mb: int = 76
    livekit_ws_url: str = ''
    livekit_http_url: str = ''
    livekit_api_key: str = ''
    livekit_api_secret: str = ''

    def __post_init__(self):
        from .file_validation import SUPPORTED
        if not self.allowed_extensions or not set(self.allowed_extensions) <= SUPPORTED:
            raise ValueError('ALLOWED_EXTENSIONS содержит неподдерживаемые форматы')
        if min(self.max_file_mb,self.max_files_per_upload,self.max_request_mb)<1:
            raise ValueError('Лимиты загрузки должны быть положительными')

    @classmethod
    def from_env(cls):
        load_env_file(ROOT / '.env')
        secret = os.getenv('SECRET_KEY', '')
        if len(secret) < 32 or secret.startswith('REPLACE_ME'):
            raise RuntimeError('Установите SECRET_KEY: минимум 32 случайных символа в файле .env')
        return cls(
            secret_key=secret,
            database_url=os.getenv('DATABASE_URL', f'sqlite:///{ROOT / "instance" / "portal.db"}'),
            timezone=os.getenv('APP_TIMEZONE', 'Europe/Moscow'),
            secure_cookies=os.getenv('SECURE_COOKIES', 'false').lower() == 'true',
            max_file_mb=int(os.getenv('MAX_FILE_MB', '15')),
            max_files_per_upload=int(os.getenv('MAX_FILES_PER_UPLOAD', '5')),
            max_request_mb=int(os.getenv('MAX_REQUEST_MB', '76')),
            allowed_extensions=tuple(x.strip().lower() for x in os.getenv('ALLOWED_EXTENSIONS','.pdf,.jpg,.jpeg,.png,.webp,.docx,.xlsx,.pptx,.odt,.txt,.rtf').split(',') if x.strip()),
            upload_root=Path(os.getenv('UPLOAD_ROOT',str(ROOT / 'instance' / 'uploads'))).resolve(),
            livekit_ws_url=os.getenv('LIVEKIT_WS_URL', ''),
            livekit_http_url=os.getenv('LIVEKIT_HTTP_URL', ''),
            livekit_api_key=os.getenv('LIVEKIT_API_KEY', ''),
            livekit_api_secret=os.getenv('LIVEKIT_API_SECRET', ''),
        )
