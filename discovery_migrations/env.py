"""Additive opt-in discovery migrations; leave classification Alembic head intact."""
from alembic import context
from ai_tender_intelligence.discovery.storage import metadata

connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('Explicit administrative connection required.')
context.configure(connection=connection, target_metadata=metadata,
                  version_table='discovery_schema_version', include_name=lambda name, kind, parent:
                  kind != 'table' or name in metadata.tables, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
