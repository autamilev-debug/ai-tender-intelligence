"""Public metadata only. Independent additive revision, no profile schema changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'd0001_discovery'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('discovery_procedures', sa.Column('id', UUID(as_uuid=True), primary_key=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('discovery_identities', sa.Column('identity', sa.Text(), primary_key=True),
                    sa.Column('procedure_id', UUID(as_uuid=True), sa.ForeignKey('discovery_procedures.id'), nullable=False))
    op.create_table('discovery_evidence', sa.Column('sha256', sa.Text(), primary_key=True),
                    sa.Column('raw_payload', sa.LargeBinary(), nullable=False),
                    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"))
    op.create_table('discovery_notices', sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('procedure_id', UUID(as_uuid=True), sa.ForeignKey('discovery_procedures.id'), nullable=False),
        sa.Column('source', sa.Text(), nullable=False), sa.Column('original_id', sa.Text(), nullable=False),
        sa.Column('version_key', sa.Text(), nullable=False), sa.Column('sha256', sa.Text(), sa.ForeignKey('discovery_evidence.sha256'), nullable=False),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source', 'original_id', 'version_key', 'sha256'),
        sa.CheckConstraint("source IN ('TED','EOP')"), sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"))
    op.create_table('discovery_lots',
        sa.Column('notice_id', UUID(as_uuid=True), sa.ForeignKey('discovery_notices.id'), primary_key=True),
        sa.Column('source_lot_id', sa.Text(), primary_key=True), sa.Column('payload', JSONB(), nullable=False))


def downgrade():
    raise RuntimeError('Discovery evidence deletion requires a separately reviewed retention operation.')
