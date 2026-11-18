from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from alembic.config import Config
from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from .config import ROOT


def migrate(url: str):
    config = Config(str(ROOT / 'alembic.ini'))
    config.attributes['database_url'] = url
    command.upgrade(config, 'head')


def init_db(url: str):
    engine = create_engine(url, connect_args={'check_same_thread': False} if url.startswith('sqlite') else {}, pool_pre_ping=True)
    if url.startswith('sqlite'):
        def unicode_casefold(value):
            return value.casefold() if isinstance(value, str) else value

        @event.listens_for(engine, 'connect')
        def setup_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()
            connection.create_function('unicode_casefold', 1, unicode_casefold, deterministic=True)
    with engine.connect() as connection:
        head = ScriptDirectory.from_config(Config(str(ROOT / 'alembic.ini'))).get_current_head()
        if MigrationContext.configure(connection).get_current_revision() != head:
            engine.dispose()
            raise RuntimeError('База требует миграции: python -m alembic upgrade head (сначала резервная копия).')
    return engine, sessionmaker(engine, expire_on_commit=False)
