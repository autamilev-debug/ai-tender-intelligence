"""Default-protected document schema; P45 permits only verified additive development upgrades."""
from ai_tender_intelligence.assets import root as asset_root
from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy import text
from ..persistence.database import Database,DatabaseSettings

PROTECTED={'p5_search_dev','p5_luna_eval','p5_luna_v3_batch'}
def config():
 c=Config();c.set_main_option('script_location',str(asset_root()/'document_migrations'));return c

def upgrade(db,*,development_permit=None):
 with db.engine.begin() as conn:
  name=conn.scalar(text('SELECT current_database()'))
  if name.lower() in PROTECTED:
   raise ValueError('Protected database: document schema must already be installed.')
  if conn.scalar(text('SELECT version_num FROM alembic_version'))!='0004_searches' or conn.scalar(text('SELECT version_num FROM discovery_schema_version'))!='d0001_discovery':raise ValueError('Existing foundation required.')
  c=config();c.attributes['connection']=conn;command.upgrade(c,'head')

def main():
 db=None
 try:
  db=Database(DatabaseSettings.from_environment());upgrade(db);print('Document evidence migration completed.');return 0
 except Exception:print('Document migration refused or failed. Use an explicitly prepared isolated database.');return 2
 finally:
  if db:db.close()
if __name__=='__main__':raise SystemExit(main())
