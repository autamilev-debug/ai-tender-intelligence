"""Explicit local database setup; never starts Telegram or external integrations."""
import argparse
from sqlalchemy import text
from alembic import command
from .persistence.database import Database,DatabaseSettings
from .persistence.migrate import migration_config
from .discovery.migrate import upgrade as discovery_upgrade
from .acquisition.migrate import upgrade as document_upgrade
from .extraction.migrate import upgrade as extraction_upgrade
from .catalogue.importer import CatalogueImporter
from .beta67.schema import install


def setup(db):
    with db.engine.begin() as c:
        if not c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='p5_search_runtime'")):
            raise ValueError('Create the restricted p5_search_runtime login before setup.')
        if c.scalar(text('SELECT current_database()')) in {'p5_luna_eval','p5_luna_v3_batch','p5_semantic_eval'}:
            raise ValueError('Protected evaluation database.')
        cfg=migration_config();cfg.attributes['connection']=c;command.upgrade(cfg,'head')
    for table,upgrade in [('discovery_schema_version',discovery_upgrade),
                          ('document_schema_version',document_upgrade),('extraction_schema_version',extraction_upgrade)]:
        with db.transaction() as s:installed=s.scalar(text('SELECT to_regclass(:name)'),{'name':'public.'+table})
        if installed is None:upgrade(db)
    CatalogueImporter(db).run()
    install(db)
    with db.engine.begin() as c:
        for table in ('alembic_version','discovery_schema_version','document_schema_version','extraction_schema_version'):
            c.execute(text('GRANT SELECT ON '+table+' TO p5_search_runtime'))
        for table in ('private_tender_searches','discovery_evidence','discovery_procedures',
                      'discovery_notices','discovery_lots','discovery_identities'):
            c.execute(text('GRANT SELECT,INSERT,UPDATE ON '+table+' TO p5_search_runtime'))
        for table in ('document_collections','document_occurrences','document_collection_items','document_extractions'):
            c.execute(text('GRANT SELECT,INSERT ON '+table+' TO p5_search_runtime'))
        c.execute(text('GRANT UPDATE(status,finished_at,summary) ON document_collections TO p5_search_runtime'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args(argv)
    if not a.apply:p.print_help();return 0
    db=None
    try:
        db=Database(DatabaseSettings.from_environment());setup(db)
        print('Database/reference data ready. No external integrations started.');return 0
    except Exception as error:
        print('Setup stopped safely ('+type(error).__name__+'). No credentials logged.');return 2
    finally:
        if db:db.close()


if __name__=='__main__':raise SystemExit(main())
