"""Profile foundation only. SQL snapshot is immutable, independent of live models."""

from pathlib import Path
from alembic import op

revision = '0001_profiles'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')
    op.execute(sql)


def downgrade():
    # Destructive: intended only for explicit disposable-database rehearsal.
    for name in ('classification_intents','company_cpv_assignments','cpv_codes',
                 'cpv_catalogue_versions','active_company_selections','company_regions',
                 'company_categories','oblasts','business_categories','companies','telegram_users'):
        op.drop_table(name)
    op.execute('DROP FUNCTION IF EXISTS p5_profile_shape()')
    op.execute('DROP FUNCTION IF EXISTS p5_pending_candidate_shape()')
