"""Record runtime prompt identity; do not invent provenance for older attempts."""
from alembic import op
import sqlalchemy as sa

revision='0003_prompts'
down_revision='0002_classification'
branch_labels=depends_on=None


def upgrade():
    op.add_column('classification_attempts',sa.Column('prompt_version',sa.String(64)))
    op.add_column('classification_attempts',sa.Column('prompt_sha256',sa.String(64)))
    op.create_check_constraint('ck_classification_attempts_prompt_evidence','classification_attempts',
        "(prompt_version IS NULL AND prompt_sha256 IS NULL) OR (prompt_version IS NOT NULL AND length(prompt_version) > 0 AND prompt_sha256 IS NOT NULL AND prompt_sha256 ~ '^[0-9a-f]{64}$')")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM classification_attempts WHERE prompt_sha256 IS NOT NULL) THEN RAISE EXCEPTION 'Preserve prompt audit evidence before downgrade'; END IF; END $$")
    op.drop_constraint('ck_classification_attempts_prompt_evidence','classification_attempts',type_='check')
    op.drop_column('classification_attempts','prompt_sha256')
    op.drop_column('classification_attempts','prompt_version')
