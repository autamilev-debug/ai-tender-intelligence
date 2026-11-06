"""Private owner-bound ranked snapshots, independent of public discovery migration."""
from alembic import op
from sqlalchemy import (MetaData, Table, Column as C, String, Integer, DateTime, Uuid,
                        ForeignKeyConstraint, UniqueConstraint, CheckConstraint as Check, Index)
from sqlalchemy.dialects.postgresql import JSONB

# Frozen local definition: future application metadata must not rewrite this revision.
searches = Table('private_tender_searches', MetaData(),
    C('id', Uuid, primary_key=True), C('company_id', Uuid, nullable=False),
    C('owner_user_id', Uuid, nullable=False), C('request_key_hash', String(64), nullable=False),
    C('parameters', JSONB, nullable=False), C('classification_identity', JSONB, nullable=False), C('profile_revision', Integer, nullable=False),
    C('company_generation', Integer, nullable=False), C('assignment_digest', String(64), nullable=False),
    C('catalogue_version', String(64), nullable=False),
    C('status', String(16), nullable=False), C('started_at', DateTime(timezone=True), nullable=False),
    C('completed_at', DateTime(timezone=True)), C('result', JSONB), C('result_sha256', String(64)),
    UniqueConstraint('owner_user_id','company_id','request_key_hash', name='uq_private_tender_searches_owner_user_id'),
    Check("status IN ('RUNNING','COMPLETE','PARTIAL','FAILED')", name='ck_private_tender_searches_state'),
    Check("profile_revision > 0 AND company_generation > 0 AND assignment_digest ~ '^[0-9a-f]{64}$' AND request_key_hash ~ '^[0-9a-f]{64}$'", name='ck_private_tender_searches_identity'),
    Check("(status = 'RUNNING' AND completed_at IS NULL AND result IS NULL AND result_sha256 IS NULL) OR (status = 'FAILED' AND completed_at IS NOT NULL AND result IS NULL AND result_sha256 IS NULL) OR (status IN ('COMPLETE','PARTIAL') AND completed_at IS NOT NULL AND result IS NOT NULL AND result_sha256 IS NOT NULL AND result_sha256 ~ '^[0-9a-f]{64}$')", name='ck_private_tender_searches_completion'),
    Check("result IS NULL OR (jsonb_typeof(result) = 'object' AND result ? 'matches' AND result->'matches' ? 'opportunities' AND jsonb_typeof(result->'matches'->'opportunities') = 'array' AND jsonb_array_length(result->'matches'->'opportunities') <= 15)", name='ck_private_tender_searches_result_bound'))
Index('ix_private_tender_searches_company', searches.c.owner_user_id, searches.c.company_id, searches.c.started_at)

revision = '0004_searches'
down_revision = '0003_prompts'
branch_labels = depends_on = None


def upgrade():
    searches.create(op.get_bind())
    op.create_foreign_key(op.f('fk_private_tender_searches_company_id_companies'), 'private_tender_searches', 'companies',
        ['company_id','owner_user_id'], ['id','owner_user_id'])
    op.create_foreign_key(op.f('fk_private_tender_searches_catalogue_version_cpv_catalogue_versions'), 'private_tender_searches',
        'cpv_catalogue_versions', ['catalogue_version'], ['id'])


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM private_tender_searches) THEN RAISE EXCEPTION 'Preserve private search evidence before downgrade'; END IF; END $$")
    searches.drop(op.get_bind())
