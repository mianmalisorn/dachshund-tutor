"""Private-message worker. Poll before delivery so revocations are handled first."""
import argparse, logging, time, os
from app.db import init_db
from app.personal_telegram import PersonalConfig, poll
from app.personal import dispatch_one

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--test',action='store_true'); parser.add_argument('--send',action='store_true'); parser.add_argument('--once',action='store_true'); parser.add_argument('--idle-unconfigured',action='store_true'); args=parser.parse_args()
    if args.test:
        from scripts.personal_lab import settings, config, verify
        cfg=config(); app_settings=settings()
    else:
        from app.production import production_settings
        if os.getenv('APP_ENV')!='production': raise SystemExit('Use --test for the isolated lab or explicitly configure APP_ENV=production.')
        app_settings,_=production_settings(); cfg=PersonalConfig.from_env(); verify=lambda factory:None
    if not cfg.configured and not args.idle_unconfigured: raise SystemExit('Personal test/production bot is not configured. No network calls made.')
    # Polling /start must work before the separate sending opt-in.
    # --send permits dispatch only while config.enabled is also true.
    engine,factory=init_db(app_settings.database_url)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    failed=False
    try:
        verify(factory)
        while True:
            try:
                # Re-read test opt-in/allowlist each cycle; removing opt-in stops dispatch.
                if args.test: cfg=config(); verify(factory)
                if cfg.configured: poll(factory,cfg)
                if args.send and cfg.enabled and cfg.configured:
                    for _ in range(10):
                        if not dispatch_one(factory,cfg): break
                logging.info('Personal worker cycle completed; unconfigured transport stays offline. API acceptance does not imply reading.')
            except Exception:
                failed=True
                logging.error('Personal worker cycle failed; delivery skipped or uncertain. Inspect local configuration/state. No private error details logged.')
            if args.once: break
            time.sleep(15)
    finally: engine.dispose()
    if args.once and failed: raise SystemExit(1)

if __name__=='__main__': main()
