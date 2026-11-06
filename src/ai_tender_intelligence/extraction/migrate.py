"""Default-protected derivative stream; P45 permits exact backed-up development upgrades only."""
from ai_tender_intelligence.assets import root as asset_root
from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy import text
from ..acquisition.migrate import PROTECTED
from ..persistence.database import Database,DatabaseSettings


def upgrade(db,*,development_permit=None):
    with db.engine.begin() as c:
        name=c.scalar(text('SELECT current_database()'))
        if name.lower() in PROTECTED:
            raise ValueError('Protected database: extraction schema must already be installed.')
        for table,head in [('alembic_version','0004_searches'),('discovery_schema_version','d0001_discovery'),('document_schema_version','e0001_documents')]:
            if c.scalar(text('SELECT version_num FROM '+table))!=head:raise ValueError('Existing foundation required.')
        cfg=Config();cfg.set_main_option('script_location',str(asset_root()/'extraction_migrations'))
        cfg.attributes['connection']=c;command.upgrade(cfg,'head')


def main():
    db=None
    try:
        db=Database(DatabaseSettings.from_environment());upgrade(db);print('Extraction migration completed.');return 0
    except Exception:
        print('Extraction migration refused or failed; use an explicitly prepared isolated database.');return 2
    finally:
        if db:db.close()
if __name__=='__main__':raise SystemExit(main())
