"""Additive Beta storage; historical demo table and migration streams unchanged."""
from sqlalchemy import (MetaData, Table, Column as C, Text, BigInteger, Integer,
    Boolean, Uuid, DateTime, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, text)
from sqlalchemy.dialects.postgresql import JSONB
from ..demo41.store import RUNS
from ..persistence.schema import companies, users, intents

metadata = MetaData()
invites = Table('beta67_invites', metadata,
    C('scope', Text, primary_key=True), C('telegram_id', BigInteger, primary_key=True),
    C('enabled', Boolean, nullable=False, server_default=text('true')),
    C('analysis_limit', Integer, nullable=False, server_default='1'),
    CheckConstraint('telegram_id > 0 AND analysis_limit > 0'))

runs = RUNS.to_metadata(metadata, name='beta67_runs')
# Copy the existing proven job/envelope columns, not its fixed-test-recipient policy.
for constraint in list(runs.constraints):
    if isinstance(constraint, ForeignKeyConstraint) or (
        isinstance(constraint, CheckConstraint) and 'reports@example.invalid' in str(constraint.sqltext)):
        runs.constraints.remove(constraint)
runs.append_column(C('owner_user_id', Uuid, nullable=False))
runs.append_constraint(ForeignKeyConstraint(['company', 'owner_user_id'], [companies.c.id, companies.c.owner_user_id]))
runs.append_constraint(CheckConstraint("NOT customer_publishable AND send_attempts BETWEEN 0 AND 1 AND length(recipient) BETWEEN 3 AND 254"))

requests = Table('beta67_requests', metadata,
    C('id', Uuid, primary_key=True), C('scope', Text, nullable=False),
    C('owner', BigInteger, nullable=False), C('owner_user_id', Uuid, nullable=False),
    C('company', Uuid, nullable=False), C('kind', Text, nullable=False),
    C('request_hash', Text, nullable=False), C('recipient', Text, nullable=False),
    C('job', Uuid), C('intent', Uuid), C('state', Text, nullable=False),
    C('started_at', DateTime(timezone=True), nullable=False, server_default=text('now()')),
    C('finished_at', DateTime(timezone=True)), C('diagnostic', JSONB),
    ForeignKeyConstraint(['company','owner_user_id'],[companies.c.id,companies.c.owner_user_id]),
    ForeignKeyConstraint(['scope','owner'],[invites.c.scope,invites.c.telegram_id]),
    ForeignKeyConstraint(['job'],[runs.c.id]), ForeignKeyConstraint(['intent'],[intents.c.id]),
    UniqueConstraint('scope','owner','request_hash'), UniqueConstraint('job'), UniqueConstraint('intent'),
    CheckConstraint("kind IN ('ANALYSIS','CPV') AND state IN ('RUNNING','DONE','FAILED')"))
Index('uq_beta67_capacity', requests.c.scope, unique=True, postgresql_where=requests.c.state=='RUNNING')

one_analysis=Index('uq_beta68_one_analysis_account',requests.c.scope,requests.c.owner,unique=True,postgresql_where=requests.c.kind=='ANALYSIS')
VERSION = 'b0002_open_beta'
registration_policy=Table('beta68_registration_policy',metadata,C('scope',Text,primary_key=True),C('account_limit',Integer,nullable=False),CheckConstraint('account_limit=3'))
version_table=Table('beta67_schema_version',metadata,C('version_num',Text,primary_key=True))


def install(db):
    """Explicit administrator operation, never called by application startup.

    Only new Beta objects and necessary existing service-role grants. No ALTER,
    UPDATE or migration of historical profiles/jobs/evidence.
    """
    from sqlalchemy.dialects.postgresql import insert
    with db.engine.begin() as c:
        name=c.scalar(text('SELECT current_database()'))
        if name in {'p5_luna_eval','p5_luna_v3_batch','p5_semantic_eval'}:
            raise ValueError('PROTECTED_EVALUATION_DATABASE')
        if c.scalar(text('SELECT version_num FROM alembic_version'))!='0004_searches':
            raise ValueError('BETA67_EXISTING_SCHEMA_REQUIRED')
        version_table.create(c,checkfirst=True)
        versions=list(c.scalars(version_table.select()))
        if versions and versions not in ([VERSION],['b0001_invited_beta']):raise ValueError('BETA67_UNKNOWN_SCHEMA_VERSION')
        for table in (invites,runs,requests):table.create(c,checkfirst=True)
        if not versions:c.execute(version_table.insert().values(version_num=VERSION))
        elif versions!=[VERSION]:c.execute(version_table.update().values(version_num=VERSION))
        c.execute(text('''CREATE OR REPLACE FUNCTION beta67_owner_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM telegram_users u JOIN companies co ON co.owner_user_id=u.id
            JOIN beta67_invites i ON i.scope=u.scope AND i.telegram_id=u.telegram_user_id
            WHERE u.id=NEW.owner_user_id AND u.telegram_user_id=NEW.owner AND u.scope=NEW.scope
              AND co.id=NEW.company AND u.status='ACTIVE' AND co.deleted_at IS NULL
              AND (TG_OP<>'INSERT' OR co.report_email=NEW.recipient))
          THEN RAISE EXCEPTION 'BETA67_OWNER_MISMATCH'; END IF;
          IF TG_OP='UPDATE' AND (NEW.owner,NEW.owner_user_id,NEW.company,NEW.scope,NEW.recipient)
              IS DISTINCT FROM (OLD.owner,OLD.owner_user_id,OLD.company,OLD.scope,OLD.recipient)
          THEN RAISE EXCEPTION 'BETA67_IDENTITY_IMMUTABLE'; END IF;
          IF TG_TABLE_NAME='beta67_requests' THEN
          IF TG_OP='UPDATE' THEN
            IF OLD.job IS NOT NULL AND NEW.job IS DISTINCT FROM OLD.job
              THEN RAISE EXCEPTION 'BETA67_JOB_IMMUTABLE'; END IF;
            IF OLD.state<>'RUNNING' AND NEW.state IS DISTINCT FROM OLD.state
              THEN RAISE EXCEPTION 'BETA67_REQUEST_SPENT'; END IF;
          END IF;
          IF NEW.job IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM beta67_runs r WHERE r.id=NEW.job AND r.owner=NEW.owner
              AND r.company=NEW.company AND r.scope=NEW.scope AND r.recipient=NEW.recipient)
          THEN RAISE EXCEPTION 'BETA67_JOB_MISMATCH'; END IF;
          IF NEW.intent IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM classification_intents ci WHERE ci.id=NEW.intent
              AND ci.owner_user_id=NEW.owner_user_id AND ci.company_id=NEW.company)
          THEN RAISE EXCEPTION 'BETA67_INTENT_MISMATCH'; END IF;
          END IF;
          RETURN NEW; END $$'''))
        for table in (runs,requests):
            # Independent additive migration: triggers only on the new tables.
            exists=c.scalar(text('SELECT 1 FROM pg_trigger WHERE tgname=:name'),{'name':table.name+'_owner'})
            if not exists:c.execute(text(f'CREATE TRIGGER {table.name}_owner BEFORE INSERT OR UPDATE ON {table.name} FOR EACH ROW EXECUTE FUNCTION beta67_owner_guard()'))
        registration_policy.create(c,checkfirst=True)
        one_analysis.create(c,checkfirst=True)
        if c.scalar(text('SELECT count(*) FROM telegram_users WHERE scope=:scope'),{'scope':db.settings.scope})>3:
            raise ValueError('EXISTING_ACCOUNTS_EXCEED_BETA_CAPACITY')
        c.execute(insert(registration_policy).values(scope=db.settings.scope,account_limit=3).on_conflict_do_nothing())
        c.execute(text("""CREATE OR REPLACE FUNCTION beta68_registration_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE lim integer; BEGIN
          SELECT account_limit INTO lim FROM beta68_registration_policy WHERE scope=NEW.scope;
          IF lim IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(344642143559);
            IF NOT EXISTS (SELECT 1 FROM telegram_users WHERE scope=NEW.scope AND telegram_user_id=NEW.telegram_user_id)
              AND (SELECT count(*) FROM telegram_users WHERE scope=NEW.scope)>=lim
            THEN RAISE EXCEPTION 'BETA_ACCOUNT_CAPACITY'; END IF;
          END IF; RETURN NEW; END $$"""))
        if not c.scalar(text("SELECT 1 FROM pg_trigger WHERE tgname='beta68_account_capacity'")):
            c.execute(text('CREATE TRIGGER beta68_account_capacity BEFORE INSERT ON telegram_users FOR EACH ROW EXECUTE FUNCTION beta68_registration_guard()'))
        for user in c.scalars(text('SELECT telegram_user_id FROM telegram_users WHERE scope=:scope'),{'scope':db.settings.scope}):
            c.execute(insert(invites).values(scope=db.settings.scope,telegram_id=user,analysis_limit=1).on_conflict_do_nothing())
        if c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='p5_search_runtime'")):grant_runtime(c)



def grant_runtime(c):
    # Trusted server process owns profile/classification operations; catalogue
    # administration and DDL stay separate. No DELETE on users/jobs/evidence.
    for name in ('telegram_users','companies','classification_intents','classification_attempts','beta67_runs','beta67_requests'):
        c.execute(text('GRANT SELECT,INSERT,UPDATE ON '+name+' TO p5_search_runtime'))
    for name in ('company_categories','company_regions','company_cpv_assignments','active_company_selections'):
        c.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON '+name+' TO p5_search_runtime'))
    c.execute(text('GRANT SELECT ON beta67_invites TO p5_search_runtime'))
    c.execute(text('GRANT SELECT ON beta67_schema_version,beta68_registration_policy TO p5_search_runtime'))
    c.execute(text('GRANT INSERT ON beta67_invites TO p5_search_runtime'))
    c.execute(text('GRANT SELECT ON cpv_catalogue_versions,cpv_codes,business_categories,oblasts TO p5_search_runtime'))
