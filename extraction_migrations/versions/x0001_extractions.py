"""Immutable public extraction cache; no private grants or original-byte replacement."""
from alembic import op
import sqlalchemy as s
from sqlalchemy.dialects.postgresql import JSONB
revision='x0001_extractions'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('document_extractions',
        s.Column('source_sha256',s.Text,s.ForeignKey('discovery_evidence.sha256'),primary_key=True),
        s.Column('extractor_sha256',s.Text,primary_key=True),s.Column('format',s.Text,nullable=False),
        s.Column('identity',JSONB,nullable=False),s.Column('result',JSONB,nullable=False),
        s.Column('result_sha256',s.Text,nullable=False),s.Column('created_at',s.DateTime(timezone=True),nullable=False),
        s.CheckConstraint("extractor_sha256 ~ '^[0-9a-f]{64}$' AND result_sha256 ~ '^[0-9a-f]{64}$'"))
    op.execute("CREATE FUNCTION document_extraction_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Extraction derivatives are immutable'; END $$")
    op.execute('CREATE TRIGGER immutable_document_extraction BEFORE UPDATE ON document_extractions FOR EACH ROW EXECUTE FUNCTION document_extraction_immutable()')


def downgrade():
    raise RuntimeError('No automatic destructive derivative downgrade; review references first.')
