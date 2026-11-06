from alembic import context
from ai_tender_intelligence.extraction.schema import metadata
connection=context.config.attributes.get('connection')
if connection is None:raise RuntimeError('Explicit isolated administrative connection required.')
context.configure(connection=connection,target_metadata=metadata,version_table='extraction_schema_version',
    include_name=lambda name,kind,parent:kind!='table' or name=='document_extractions',
    include_object=lambda obj,name,kind,reflected,compare:kind!='table' or name=='document_extractions')
with context.begin_transaction():context.run_migrations()
