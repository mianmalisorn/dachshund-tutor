"""Separate schedule/outbox worker. Network sends require --send AND explicit env opt-in."""
import argparse,time,logging
from app.config import Settings
from app.db import init_db
from app.scheduling import refresh_all
from app.reminders import TelegramConfig,enqueue,dispatch_one

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--send',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    import os
    if os.getenv('APP_ENV')=='production':
        from app.production import production_settings
        settings,_=production_settings()
    else:settings=Settings.from_env()
    cfg=TelegramConfig.from_env();engine,factory=init_db(settings.database_url)
    if args.send and (not cfg.enabled or not cfg.configured):raise SystemExit('Telegram is not explicitly enabled/configured; no messages sent.')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    failed=False
    try:
        while True:
            try:
                with factory() as db:
                    errors=refresh_all(db)
                    if errors:logging.warning('Schedule expansion blocked for %d rules; inspect admin calendar.',len(errors))
                    if cfg.admin_id:enqueue(db,cfg.admin_id);db.commit()
                if args.send:
                    for _ in range(50):
                        if not dispatch_one(factory,cfg,settings.timezone):break
            except Exception:
                failed=True
                logging.error('Worker cycle failed; inspect database connectivity/configuration. No private error details logged.')
            if args.once:break
            time.sleep(30)
    finally:engine.dispose()
    if args.once and failed:raise SystemExit(1)

if __name__=='__main__':main()
