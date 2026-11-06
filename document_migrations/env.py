from alembic import context
from ai_tender_intelligence.acquisition.schema import metadata,TABLES
connection=context.config.attributes.get('connection')
if connection is None:raise RuntimeError('Explicit isolated administrative connection required.')
names={t.name for t in TABLES}
context.configure(connection=connection,target_metadata=metadata,version_table='document_schema_version',
 include_name=lambda name,kind,parent:kind!='table' or name in names,
 include_object=lambda obj,name,kind,reflected,compare:kind!='table' or name in names)
with context.begin_transaction():context.run_migrations()
