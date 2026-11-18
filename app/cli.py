"""Run: python -m app.cli create-admin --username teacher --name '...'"""
import argparse
import os
import getpass
import re
from sqlalchemy import select
from .config import Settings
from .db import init_db
from .models import User
from .security import password_hash


def main():
    parser = argparse.ArgumentParser(description='CatTutor: административные команды')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create-admin', help='Создать преподавательский аккаунт')
    create.add_argument('--username', default='teacher')
    create.add_argument('--name', default='Преподавательница')
    args = parser.parse_args()
    if os.getenv('APP_ENV')=='production':
        from .production import production_settings
        settings,_=production_settings()
    else:
        settings = Settings.from_env()
    engine, sessionmaker = init_db(settings.database_url)
    if args.command == 'create-admin':
        if not re.fullmatch(r'[a-z0-9_.-]{3,60}', args.username):
            parser.error('Логин: латиница, цифры, ._- (3–60 символов)')
        with sessionmaker() as db:
            if db.scalar(select(User).where(User.username == args.username)):
                parser.error('Такой логин уже существует')
            first = getpass.getpass('Пароль преподавательницы (>=10 символов): ')
            second = getpass.getpass('Повторите пароль: ')
            if first != second:
                parser.error('Пароли не совпадают')
            try:
                pwd = password_hash(first)
            except ValueError as exc:
                parser.error(str(exc))
            db.add(User(username=args.username, display_name=args.name,
                        role='admin', password_hash=pwd, must_change_password=False))
            db.commit()
            print('Администратор создан. Теперь можно войти через /login.')
    engine.dispose()


if __name__ == '__main__':
    main()
