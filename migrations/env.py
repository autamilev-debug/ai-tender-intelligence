"""No logging configuration/secret interpolation; callers supply the engine."""

from alembic import context
from sqlalchemy.exc import SQLAlchemyError
from ai_tender_intelligence.persistence.database import Database, DatabaseSettings, PersistenceError
from ai_tender_intelligence.persistence.schema import metadata

config = context.config


def run(connection):
    try:
        # Discovery is an explicitly opted-in additive migration stream. Keep
        # its tables out of profile autogeneration; do not propose their deletion.
        discovery_tables = {'discovery_schema_version', 'discovery_procedures', 'discovery_identities',
                            'discovery_evidence', 'discovery_notices', 'discovery_lots',
                            'document_schema_version', 'document_collections',
                            'document_occurrences', 'document_collection_items',
                            'extraction_schema_version', 'document_extractions',
                            'semantic_schema_version', 'semantic_evaluation_attempts'}
        context.configure(connection=connection, target_metadata=metadata, compare_type=True,
                          compare_server_default=True,
                          include_name=lambda name, kind, parent: kind != 'table' or name not in discovery_tables)
        with context.begin_transaction():
            context.run_migrations()
    except SQLAlchemyError:
        raise PersistenceError('Local database migration failed; SQL and credentials suppressed.') from None


if context.is_offline_mode():
    context.configure(dialect_name='postgresql', target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get('connection')
    if supplied is not None:
        run(supplied)
    else:
        db = Database(DatabaseSettings.from_environment())
        try:
            with db.engine.connect() as connection:
                run(connection)
        except SQLAlchemyError:
            raise PersistenceError('Local database connection failed; credentials suppressed.') from None
        finally:
            db.close()
