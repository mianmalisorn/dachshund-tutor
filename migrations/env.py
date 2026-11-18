from alembic import context
from sqlalchemy import create_engine
from app.config import Settings
from app.models import Base

import os
url = context.config.attributes.get('database_url')
if not url:
    if os.getenv('APP_ENV')=='production':
        from app.production import production_settings
        settings,_=production_settings()
    else:
        settings=Settings.from_env()
    url=settings.database_url
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
