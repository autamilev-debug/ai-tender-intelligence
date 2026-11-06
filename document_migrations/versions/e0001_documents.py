"""Add public document occurrences and private collection grants; reuse existing blobs."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import UUID,JSONB
revision='e0001_documents'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
 op.create_table('document_collections',s.Column('id',UUID(as_uuid=True),primary_key=True),
  s.Column('owner_user_id',UUID(as_uuid=True),s.ForeignKey('telegram_users.id'),nullable=False),s.Column('company_id',UUID(as_uuid=True),nullable=False),
  s.Column('generation',s.Integer,nullable=False),s.Column('request_hash',s.Text,nullable=False),s.Column('parameters',JSONB,nullable=False),
  s.Column('status',s.Text,nullable=False),s.Column('started_at',s.DateTime(timezone=True),nullable=False),s.Column('finished_at',s.DateTime(timezone=True)),s.Column('summary',JSONB),
  s.ForeignKeyConstraint(['company_id','owner_user_id'],['companies.id','companies.owner_user_id']),s.UniqueConstraint('company_id','owner_user_id','request_hash'),
  s.CheckConstraint("status IN ('COLLECTING','ACQUIRED','PARTIAL','FAILED','INTERRUPTED')"),s.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'"))
 op.create_table('document_occurrences',s.Column('id',UUID(as_uuid=True),primary_key=True),s.Column('identity_hash',s.Text,nullable=False,unique=True),
  s.Column('parent_tender',s.Integer,nullable=False),s.Column('reference',JSONB,nullable=False),s.Column('blob_sha256',s.Text,s.ForeignKey('discovery_evidence.sha256')),
  s.Column('byte_size',s.Integer),s.Column('validation',JSONB,nullable=False),s.Column('observed_at',s.DateTime(timezone=True),nullable=False),
  s.Column('container_occurrence',UUID(as_uuid=True),s.ForeignKey('document_occurrences.id')),s.Column('member_path',s.Text),s.CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'"))
 op.create_table('document_collection_items',s.Column('collection_id',UUID(as_uuid=True),s.ForeignKey('document_collections.id'),primary_key=True),
  s.Column('occurrence_id',UUID(as_uuid=True),s.ForeignKey('document_occurrences.id'),primary_key=True),s.Column('observed_at',s.DateTime(timezone=True),nullable=False))
 # Immutable public evidence; collections close exactly once. No silent byte/source replacement.
 op.execute("""CREATE FUNCTION document_occurrence_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Document occurrences are immutable'; END $$""")
 op.execute('CREATE TRIGGER immutable_document_occurrence BEFORE UPDATE ON document_occurrences FOR EACH ROW EXECUTE FUNCTION document_occurrence_immutable()')

def downgrade():
 raise RuntimeError('No automatic destructive document-evidence downgrade; review references first.')
