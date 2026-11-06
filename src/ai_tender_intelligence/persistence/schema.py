"""Profiles, CPV reference and durable classification; no tenders or drafts."""

from sqlalchemy import (MetaData, Table, Column as C, String, Text, Integer, BigInteger,
                        DateTime, Uuid, ForeignKey, ForeignKeyConstraint, UniqueConstraint,
                        CheckConstraint as Check, Index, text, Numeric, Boolean)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(naming_convention={'ix': 'ix_%(table_name)s_%(column_0_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s', 'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s', 'ck': 'ck_%(table_name)s_%(constraint_name)s'})

# Administrative import takes exclusive; result publication takes shared.
# This is an application coordination key, not a schema/permission change.
CATALOGUE_LOCK = 0x5035435056

users = Table('telegram_users', metadata,
    C('id', Uuid, primary_key=True), C('scope', String(64), nullable=False),
    C('telegram_user_id', BigInteger, nullable=False), C('private_chat_id', BigInteger, nullable=False),
    C('status', String(16), nullable=False, server_default='ACTIVE'),
    C('revocation_generation', Integer, nullable=False, server_default='1'),
    C('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    UniqueConstraint('scope', 'telegram_user_id'),
    Check('telegram_user_id > 0 AND private_chat_id = telegram_user_id', name='private_identity'),
    Check("status IN ('ACTIVE','REVOKED') AND revocation_generation > 0", name='status'))

companies = Table('companies', metadata,
    C('id', Uuid, primary_key=True), C('owner_user_id', Uuid, ForeignKey('telegram_users.id'), nullable=False),
    C('name', String(120)), C('report_email', String(254)),
    C('profile_revision', Integer, nullable=False, server_default='1'),
    C('category_revision', Integer, nullable=False, server_default='1'),
    C('classification_status', String(20), nullable=False, server_default='PENDING_CPV'),
    C('revocation_generation', Integer, nullable=False, server_default='1'),
    C('deleted_at', DateTime(timezone=True)),
    C('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    C('updated_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    UniqueConstraint('id', 'owner_user_id'),
    Check('profile_revision > 0 AND category_revision > 0 AND revocation_generation > 0', name='revisions'),
    Check("classification_status IN ('PENDING_CPV','READY','FAILED','REVOKED')", name='classification'),
    Check("(deleted_at IS NULL AND name IS NOT NULL AND length(trim(name)) > 0 AND report_email IS NOT NULL AND length(trim(report_email)) > 0 AND classification_status <> 'REVOKED') OR (deleted_at IS NOT NULL AND name IS NULL AND report_email IS NULL AND classification_status = 'REVOKED')", name='complete_or_purged'))
Index('ix_companies_owner_live', companies.c.owner_user_id, postgresql_where=companies.c.deleted_at.is_(None))

categories = Table('business_categories', metadata,
    C('key', String(16), primary_key=True), C('label_bg', String(64), nullable=False, unique=True),
    C('display_order', Integer, nullable=False, unique=True))
oblasts = Table('oblasts', metadata,
    C('code', String(16), primary_key=True), C('name_bg', String(64), nullable=False, unique=True),
    C('display_order', Integer, nullable=False, unique=True))

company_categories = Table('company_categories', metadata,
    C('company_id', Uuid, primary_key=True), C('owner_user_id', Uuid, nullable=False),
    C('category_key', String(16), ForeignKey('business_categories.key'), primary_key=True),
    C('activities_text', Text, nullable=False),
    ForeignKeyConstraint(['company_id','owner_user_id'], ['companies.id','companies.owner_user_id']),
    Check('length(trim(activities_text)) BETWEEN 1 AND 2000', name='activity'))
regions = Table('company_regions', metadata,
    C('company_id', Uuid, primary_key=True), C('owner_user_id', Uuid, nullable=False),
    C('oblast_code', String(16), ForeignKey('oblasts.code'), primary_key=True),
    ForeignKeyConstraint(['company_id','owner_user_id'], ['companies.id','companies.owner_user_id']))
selections = Table('active_company_selections', metadata,
    C('user_id', Uuid, ForeignKey('telegram_users.id'), primary_key=True),
    C('company_id', Uuid, nullable=False),
    C('generation', Integer, nullable=False, server_default='1'),
    C('updated_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    ForeignKeyConstraint(['company_id','user_id'], ['companies.id','companies.owner_user_id']),
    Check('generation > 0', name='generation'))

catalogues = Table('cpv_catalogue_versions', metadata,
    C('id', String(64), primary_key=True), C('official_release', String(64), nullable=False),
    C('source_url', Text, nullable=False), C('sha256', String(64), nullable=False),
    C('status', String(16), nullable=False),
    C('loaded_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    Check("sha256 ~ '^[0-9a-f]{64}$' AND status IN ('STAGED','ACTIVE')", name='provenance'))
Index('uq_cpv_single_active_catalogue', catalogues.c.status, unique=True, postgresql_where=catalogues.c.status == 'ACTIVE')
codes = Table('cpv_codes', metadata,
    C('version_id', String(64), ForeignKey('cpv_catalogue_versions.id'), primary_key=True),
    C('code', String(8), primary_key=True), C('check_digit', String(1), nullable=False),
    C('label_bg', Text, nullable=False),
    Check("code ~ '^[0-9]{8}$' AND check_digit ~ '^[0-9]$' AND length(trim(label_bg)) > 0", name='official_code'))
assignments = Table('company_cpv_assignments', metadata,
    C('company_id', Uuid, primary_key=True), C('owner_user_id', Uuid, nullable=False),
    C('code', String(8), primary_key=True), C('catalogue_version_id', String(64), nullable=False),
    C('classifier_revision', String(64), nullable=False), C('assignment_set_id', Uuid, nullable=False),
    C('rationale_reference', String(128), nullable=False),
    ForeignKeyConstraint(['company_id','owner_user_id'], ['companies.id','companies.owner_user_id']),
    ForeignKeyConstraint(['catalogue_version_id','code'], ['cpv_codes.version_id','cpv_codes.code']))

intents = Table('classification_intents', metadata,
    C('id', Uuid, primary_key=True), C('company_id', Uuid, nullable=False), C('owner_user_id', Uuid, nullable=False),
    C('kind', String(20), nullable=False), C('status', String(16), nullable=False, server_default='PENDING'),
    C('request_key_hash', String(64), nullable=False), C('input_digest', String(64), nullable=False),
    C('base_profile_revision', Integer, nullable=False), C('base_category_revision', Integer, nullable=False),
    C('company_generation', Integer, nullable=False), C('candidate_categories', JSONB(none_as_null=True)),
    C('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    C('completed_at', DateTime(timezone=True)),
    C('processing_state', String(20), nullable=False, server_default='QUEUED'),
    C('lease_token', Uuid), C('lease_until', DateTime(timezone=True)),
    C('fence', Integer, nullable=False, server_default='0'),
    C('dispatch_count', Integer, nullable=False, server_default='0'),
    C('next_attempt_at', DateTime(timezone=True)), C('last_error', String(40)),
    C('result_checkpoint', JSONB(none_as_null=True)),
    ForeignKeyConstraint(['company_id','owner_user_id'], ['companies.id','companies.owner_user_id']),
    UniqueConstraint('owner_user_id','kind','request_key_hash'),
    Check("kind IN ('CPV_CLASSIFY','CPV_RECLASSIFY') AND status IN ('PENDING','SUCCEEDED','FAILED','CANCELLED')", name='state'),
    Check("(status = 'PENDING' AND candidate_categories IS NOT NULL AND jsonb_typeof(candidate_categories) = 'array' AND jsonb_array_length(candidate_categories) BETWEEN 1 AND 2 AND completed_at IS NULL) OR (status <> 'PENDING' AND candidate_categories IS NULL AND completed_at IS NOT NULL)", name='candidate_lifetime'),
    Check('base_profile_revision > 0 AND base_category_revision > 0 AND company_generation > 0', name='base'),
    Check("processing_state IN ('QUEUED','CLAIMED','DISPATCHED','CHECKPOINTED','RETRY_WAIT','BLOCKED','UNCERTAIN','FINISHED') AND fence >= 0 AND dispatch_count >= 0", name='processing'),
    Check("(lease_token IS NULL) = (lease_until IS NULL)", name='lease'),
    Check("status = 'PENDING' OR (processing_state = 'FINISHED' AND lease_token IS NULL AND result_checkpoint IS NULL)", name='terminal_processing'))
Index('uq_one_pending_classification', intents.c.company_id, unique=True, postgresql_where=intents.c.status == 'PENDING')
Index('ix_classification_intents_owner_company', intents.c.owner_user_id, intents.c.company_id)

# Administrative cost evidence is separate from runtime read/reserve privileges.
budget = Table('project_budget_state', metadata,
    C('id', Integer, primary_key=True), C('paid_authorized', Boolean, nullable=False, server_default=text('false')),
    C('prior_attributable_eur', Numeric(16,8)), C('other_reserved_eur', Numeric(16,8)),
    C('prepaid_coverage_eur', Numeric(16,8)),
    C('evidence_reference', String(128)), C('valid_until', DateTime(timezone=True)),
    Check('id = 1 AND (prior_attributable_eur IS NULL OR prior_attributable_eur >= 0) AND (other_reserved_eur IS NULL OR other_reserved_eur >= 0) AND (prepaid_coverage_eur IS NULL OR (prepaid_coverage_eur >= 0 AND prepaid_coverage_eur <= prior_attributable_eur))', name='accounting'))
attempts = Table('classification_attempts', metadata,
    C('id', Uuid, primary_key=True), C('intent_id', Uuid, ForeignKey('classification_intents.id'), nullable=False),
    C('number', Integer, nullable=False), C('model', String(32), nullable=False),
    C('reasoning_effort', String(16), nullable=False), C('is_mock', Boolean, nullable=False),
    C('status', String(24), nullable=False), C('request_digest', String(64), nullable=False),
    C('prompt_version', String(64)), C('prompt_sha256', String(64)),
    C('reserved_eur', Numeric(16,8), nullable=False), C('estimated_eur', Numeric(16,8)),
    C('actual_eur', Numeric(16,8)), C('cost_basis', String(128), nullable=False),
    C('input_tokens', Integer), C('output_tokens', Integer),
    C('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    UniqueConstraint('intent_id','number'),
    Check("(prompt_version IS NULL AND prompt_sha256 IS NULL) OR (prompt_version IS NOT NULL AND length(prompt_version) > 0 AND prompt_sha256 IS NOT NULL AND prompt_sha256 ~ '^[0-9a-f]{64}$')", name='prompt_evidence'),
    Check("status IN ('RESERVED','SUCCEEDED','FAILED_KNOWN','UNKNOWN') AND number > 0 AND reserved_eur >= 0 AND (estimated_eur IS NULL OR estimated_eur >= 0) AND (actual_eur IS NULL OR actual_eur >= 0) AND (input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)", name='accounting'))


# Private ranked search snapshots; source documents remain in public discovery storage.
searches = Table('private_tender_searches', metadata,
    C('id', Uuid, primary_key=True), C('company_id', Uuid, nullable=False),
    C('owner_user_id', Uuid, nullable=False), C('request_key_hash', String(64), nullable=False),
    C('parameters', JSONB, nullable=False), C('classification_identity', JSONB, nullable=False), C('profile_revision', Integer, nullable=False),
    C('company_generation', Integer, nullable=False), C('assignment_digest', String(64), nullable=False),
    C('catalogue_version', String(64), ForeignKey('cpv_catalogue_versions.id'), nullable=False),
    C('status', String(16), nullable=False), C('started_at', DateTime(timezone=True), nullable=False),
    C('completed_at', DateTime(timezone=True)), C('result', JSONB),
    C('result_sha256', String(64)),
    ForeignKeyConstraint(['company_id','owner_user_id'], ['companies.id','companies.owner_user_id']),
    UniqueConstraint('owner_user_id','company_id','request_key_hash'),
    Check("status IN ('RUNNING','COMPLETE','PARTIAL','FAILED')", name='state'),
    Check("profile_revision > 0 AND company_generation > 0 AND assignment_digest ~ '^[0-9a-f]{64}$' AND request_key_hash ~ '^[0-9a-f]{64}$'", name='identity'),
    Check("(status = 'RUNNING' AND completed_at IS NULL AND result IS NULL AND result_sha256 IS NULL) OR (status = 'FAILED' AND completed_at IS NOT NULL AND result IS NULL AND result_sha256 IS NULL) OR (status IN ('COMPLETE','PARTIAL') AND completed_at IS NOT NULL AND result IS NOT NULL AND result_sha256 IS NOT NULL AND result_sha256 ~ '^[0-9a-f]{64}$')", name='completion'),
    Check("result IS NULL OR (jsonb_typeof(result) = 'object' AND result ? 'matches' AND result->'matches' ? 'opportunities' AND jsonb_typeof(result->'matches'->'opportunities') = 'array' AND jsonb_array_length(result->'matches'->'opportunities') <= 15)", name='result_bound'))
Index('ix_private_tender_searches_company', searches.c.owner_user_id, searches.c.company_id, searches.c.started_at)
