"""Shared derivatives reuse the existing original-byte store and private grants."""
from sqlalchemy import MetaData,Table,Column,Text,DateTime,ForeignKey,CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
metadata=MetaData()
Table('discovery_evidence',metadata,Column('sha256',Text,primary_key=True))
derivatives=Table('document_extractions',metadata,
    Column('source_sha256',Text,ForeignKey('discovery_evidence.sha256'),primary_key=True),
    Column('extractor_sha256',Text,primary_key=True),Column('format',Text,nullable=False),
    Column('identity',JSONB,nullable=False),Column('result',JSONB,nullable=False),
    Column('result_sha256',Text,nullable=False),Column('created_at',DateTime(timezone=True),nullable=False),
    CheckConstraint("extractor_sha256 ~ '^[0-9a-f]{64}$' AND result_sha256 ~ '^[0-9a-f]{64}$'"))
