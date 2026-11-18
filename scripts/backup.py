"""Offline backup/restore: python -m scripts.backup --help."""
import argparse, os
from datetime import datetime
from pathlib import Path
from app.config import Settings, ROOT
from app.backups import create_backup, restore_backup

def main():
    parser=argparse.ArgumentParser(description='Offline SQLite backup. Stop the web server AND reminder worker first.')
    sub=parser.add_subparsers(dest='command',required=True)
    backup=sub.add_parser('create')
    backup.add_argument('--writes-stopped',action='store_true',required=True)
    backup.add_argument('--output',type=Path,default=None)
    restore=sub.add_parser('restore')
    restore.add_argument('archive',type=Path)
    restore.add_argument('new_directory',type=Path)
    args=parser.parse_args()
    if args.command=='create':
        if os.getenv('APP_ENV') == 'production':
            from app.production import production_settings
            settings,_=production_settings()
            from scripts.amvera_runtime import prepare, instance_lock
            _,root,_=prepare()
            target=args.output or root/'backups'/f'tutor-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
            with instance_lock(root):
                print(create_backup(settings,target,writes_stopped=args.writes_stopped))
        else:
            target=args.output or ROOT/'backups'/f'cattutor-{datetime.now():%Y%m%d-%H%M%S}.tar.gz'
            print(create_backup(Settings.from_env(),target,writes_stopped=args.writes_stopped))
    else:
        print(restore_backup(args.archive,args.new_directory))

if __name__=='__main__':
    main()
