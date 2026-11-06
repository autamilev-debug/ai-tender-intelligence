"""Independent evidence relations; shared bytes reuse discovery_evidence."""
from sqlalchemy import MetaData,Table,Column as C,Uuid,Text,Integer,JSON,DateTime,ForeignKey,ForeignKeyConstraint,UniqueConstraint,CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
metadata=MetaData()
# FK targets only. No recreation or alteration of existing profile/discovery tables.
Table('telegram_users',metadata,C('id',Uuid,primary_key=True))
Table('companies',metadata,C('id',Uuid,primary_key=True),C('owner_user_id',Uuid))
Table('discovery_evidence',metadata,C('sha256',Text,primary_key=True))
collections=Table('document_collections',metadata,C('id',Uuid,primary_key=True),
 C('owner_user_id',Uuid,ForeignKey('telegram_users.id'),nullable=False),C('company_id',Uuid,nullable=False),
 C('generation',Integer,nullable=False),C('request_hash',Text,nullable=False),C('parameters',JSONB,nullable=False),
 C('status',Text,nullable=False),C('started_at',DateTime(timezone=True),nullable=False),C('finished_at',DateTime(timezone=True)),C('summary',JSONB),
 ForeignKeyConstraint(['company_id','owner_user_id'],['companies.id','companies.owner_user_id']),
 UniqueConstraint('company_id','owner_user_id','request_hash'),
 CheckConstraint("status IN ('COLLECTING','ACQUIRED','PARTIAL','FAILED','INTERRUPTED')"),
 CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'"))
occurrences=Table('document_occurrences',metadata,C('id',Uuid,primary_key=True),C('identity_hash',Text,nullable=False,unique=True),
 C('parent_tender',Integer,nullable=False),C('reference',JSONB,nullable=False),C('blob_sha256',Text,ForeignKey('discovery_evidence.sha256')),
 C('byte_size',Integer),C('validation',JSONB,nullable=False),C('observed_at',DateTime(timezone=True),nullable=False),
 C('container_occurrence',Uuid,ForeignKey('document_occurrences.id')),C('member_path',Text),
 CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'"))
items=Table('document_collection_items',metadata,C('collection_id',Uuid,ForeignKey('document_collections.id'),primary_key=True),
 C('occurrence_id',Uuid,ForeignKey('document_occurrences.id'),primary_key=True),C('observed_at',DateTime(timezone=True),nullable=False))
TABLES=[collections,occurrences,items]
