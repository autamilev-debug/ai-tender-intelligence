"""Durable classification claims, checkpoints and cost evidence. No provider enablement."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0002_classification'
down_revision = '0001_profiles'
branch_labels = depends_on = None


def upgrade():
    for column in (
        sa.Column('processing_state', sa.String(20), nullable=False, server_default='QUEUED'),
        sa.Column('lease_token', sa.Uuid()), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('fence', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dispatch_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)), sa.Column('last_error', sa.String(40)),
        sa.Column('result_checkpoint', JSONB(none_as_null=True)),
    ):
        op.add_column('classification_intents', column)
    op.execute("UPDATE classification_intents SET processing_state='FINISHED' WHERE status <> 'PENDING'")
    op.create_check_constraint('ck_classification_intents_processing','classification_intents',
        "processing_state IN ('QUEUED','CLAIMED','DISPATCHED','CHECKPOINTED','RETRY_WAIT','BLOCKED','UNCERTAIN','FINISHED') AND fence >= 0 AND dispatch_count >= 0")
    op.create_check_constraint('ck_classification_intents_lease','classification_intents',
        '(lease_token IS NULL) = (lease_until IS NULL)')
    op.create_check_constraint('ck_classification_intents_terminal_processing','classification_intents',
        "status = 'PENDING' OR (processing_state = 'FINISHED' AND lease_token IS NULL AND result_checkpoint IS NULL)")
    op.create_table('project_budget_state',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('paid_authorized',sa.Boolean(),nullable=False,server_default=sa.text('false')),
        sa.Column('prior_attributable_eur',sa.Numeric(16,8)), sa.Column('other_reserved_eur',sa.Numeric(16,8)),
        sa.Column('prepaid_coverage_eur',sa.Numeric(16,8)),
        sa.Column('evidence_reference',sa.String(128)), sa.Column('valid_until',sa.DateTime(timezone=True)),
        sa.CheckConstraint('id = 1 AND (prior_attributable_eur IS NULL OR prior_attributable_eur >= 0) AND (other_reserved_eur IS NULL OR other_reserved_eur >= 0) AND (prepaid_coverage_eur IS NULL OR (prepaid_coverage_eur >= 0 AND prepaid_coverage_eur <= prior_attributable_eur))',name='ck_project_budget_state_accounting'))
    op.execute('INSERT INTO project_budget_state(id) VALUES (1)')
    op.create_table('classification_attempts',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('intent_id',sa.Uuid(),sa.ForeignKey('classification_intents.id'),nullable=False),
        sa.Column('number',sa.Integer(),nullable=False), sa.Column('model',sa.String(32),nullable=False),
        sa.Column('reasoning_effort',sa.String(16),nullable=False), sa.Column('is_mock',sa.Boolean(),nullable=False),
        sa.Column('status',sa.String(24),nullable=False), sa.Column('request_digest',sa.String(64),nullable=False),
        sa.Column('reserved_eur',sa.Numeric(16,8),nullable=False), sa.Column('estimated_eur',sa.Numeric(16,8)),
        sa.Column('actual_eur',sa.Numeric(16,8)), sa.Column('cost_basis',sa.String(128),nullable=False),
        sa.Column('input_tokens',sa.Integer()), sa.Column('output_tokens',sa.Integer()),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.text('now()')),
        sa.UniqueConstraint('intent_id','number',name='uq_classification_attempts_intent_id'),
        sa.CheckConstraint("status IN ('RESERVED','SUCCEEDED','FAILED_KNOWN','UNKNOWN') AND number > 0 AND reserved_eur >= 0 AND (estimated_eur IS NULL OR estimated_eur >= 0) AND (actual_eur IS NULL OR actual_eur >= 0) AND (input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)",name='ck_classification_attempts_accounting'))


def downgrade():
    # Refuse to discard unresolved dispatch liability or private checkpoints.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM classification_intents WHERE processing_state NOT IN ('QUEUED','FINISHED')) OR EXISTS (SELECT 1 FROM classification_attempts) THEN RAISE EXCEPTION 'Classification evidence must be resolved before downgrade'; END IF; END $$")
    op.drop_table('classification_attempts')
    op.drop_table('project_budget_state')
    for name in ('terminal_processing','lease','processing'):
        op.drop_constraint('ck_classification_intents_'+name,'classification_intents',type_='check')
    for name in ('result_checkpoint','last_error','next_attempt_at','dispatch_count','fence','lease_until','lease_token','processing_state'):
        op.drop_column('classification_intents',name)
