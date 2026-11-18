"""Read-only reconciliation. Run offline: python -m scripts.check_storage."""
from sqlalchemy import create_engine, select
from app.config import Settings
from app.models import Attachment


def inspect_storage(settings):
    engine=create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            expected=set(connection.scalars(select(Attachment.storage_name)))
        root=settings.upload_root
        actual={p.name for p in root.iterdir() if p.is_file()} if root.exists() else set()
        return {'missing':sorted(expected-actual),'unreferenced':sorted(actual-expected)}
    finally:
        engine.dispose()


if __name__=='__main__':
    import json
    import os
    if os.getenv('APP_ENV')=='production':
        from app.production import production_settings
        settings,_=production_settings()
    else:settings=Settings.from_env()
    print(json.dumps(inspect_storage(settings),indent=2))
