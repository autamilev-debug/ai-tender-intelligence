from ..persistence.identity import freeze_users
"""One additive internal-demo table in existing PostgreSQL; short owner-bound writes."""
from contextlib import contextmanager
from hashlib import sha256
from uuid import UUID,uuid4
from sqlalchemy import MetaData,Table,Column as C,Text,Uuid,BigInteger,Integer,Boolean,LargeBinary,DateTime,ForeignKey,UniqueConstraint,CheckConstraint,select,insert,update,text
from sqlalchemy.dialects.postgresql import JSONB,insert as pg_insert
from ..persistence.schema import companies
from ..persistence.profiles import PostgresProfiles
from ..telegram.models import InteractionError,TEST_RECIPIENT

RUNS=Table('internal_demo41_runs',MetaData(),
    C('id',Uuid,primary_key=True),C('scope',Text,nullable=False),C('owner',BigInteger,nullable=False),
    C('company',Uuid,ForeignKey(companies.c.id),nullable=False),C('company_generation',Integer,nullable=False),
    C('request_hash',Text,nullable=False),C('state',Text,nullable=False),C('stage',Text,nullable=False),
    C('evidence',JSONB,nullable=False),C('recipient',Text,nullable=False),C('subject',Text),
    C('html',Text),C('plain',Text),C('mime',LargeBinary),C('mime_sha256',Text),
    C('send_attempts',Integer,nullable=False,server_default='0'),C('gmail_id',Text),
    C('customer_publishable',Boolean,nullable=False,server_default='false'),
    C('created_at',DateTime(timezone=True),nullable=False,server_default=text('now()')),
    UniqueConstraint('scope','owner','request_hash'),
    CheckConstraint("state IN ('PREPARING','AWAITING_OWNER_APPROVAL','FAILED','SENDING','EMAIL_ACCEPTED','EMAIL_UNKNOWN','EMAIL_REJECTED','MOCK_EMAIL_ACCEPTED')"),
    CheckConstraint("recipient='reports@example.invalid' AND NOT customer_publishable AND send_attempts BETWEEN 0 AND 1"))


def install(db):
    """Explicit additive migration; startup never invokes it."""
    with db.engine.begin() as c:
        actual=c.scalar(text('SELECT current_database()'))
        if (actual,db.settings.scope) not in {('p5_search_dev','p5-search-dev'),('p5_imp04_test23','p5-document-extraction-test')}:
            raise InteractionError('Миграцията е само за развойната/изолирана тестова база.')
        if c.scalar(text('SELECT version_num FROM alembic_version'))!='0004_searches':
            raise InteractionError('Необходима е съществуващата развойна схема.')
        RUNS.create(c,checkfirst=True)
        if actual=='p5_search_dev':
            # Existing restricted development runtime, no source/evaluation grants.
            c.execute(text('GRANT SELECT,INSERT,UPDATE ON internal_demo41_runs TO p5_search_runtime'))


class DemoStore:
    table=RUNS
    def initial_values(self,company):
        return {'recipient':TEST_RECIPIENT}

    def __init__(self,db,allowed):
        self.db=db;self.profiles=PostgresProfiles(db,freeze_users(allowed))
    @contextmanager
    def access(self,user,company):
        with self.db.transaction() as s:
            owner=self.profiles._owner(s,user)
            row=self.profiles._company(s,owner,company)
            yield s,row
    def begin(self,user,company,key):
        if not key or len(key)>150:raise InteractionError('Невалиден идентификатор на теста.')
        with self.access(user,company) as (s,c):
            ident=uuid4();hashed=sha256(key.encode()).hexdigest()
            made=s.execute(pg_insert(self.table).values(id=ident,scope=self.db.settings.scope,owner=user,company=c['id'],
                company_generation=c['revocation_generation'],request_hash=hashed,state='PREPARING',stage='REQUEST',
                evidence={},**self.initial_values(c)).on_conflict_do_nothing().returning(self.table.c.id)).scalar()
            row=s.execute(select(self.table).where(self.table.c.scope==self.db.settings.scope,self.table.c.owner==user,self.table.c.request_hash==hashed)).mappings().one()
            if row['company']!=c['id']:raise InteractionError('Тестовата заявка принадлежи на друг профил.')
            return dict(row),bool(made)
    def _row(self,s,c,user,ident):
        row=s.execute(select(self.table).where(self.table.c.id==UUID(str(ident)),self.table.c.scope==self.db.settings.scope,self.table.c.owner==user,self.table.c.company==c['id']).with_for_update()).mappings().first()
        if row is None or row['company_generation']!=c['revocation_generation']:raise InteractionError('Тестът не е достъпен.')
        return dict(row)
    def read(self,user,company,ident):
        with self.access(user,company) as (s,c):return self._row(s,c,user,ident)
    def stage(self,user,company,ident,stage,evidence):
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,ident)
            if row['state']!='PREPARING':raise InteractionError('Тестовият опит е затворен.')
            s.execute(update(self.table).where(self.table.c.id==row['id']).values(stage=stage,evidence={**row['evidence'],stage:evidence}))
    def finish(self,user,company,ident,mail):
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,ident)
            if row['state']!='PREPARING':raise InteractionError('Тестовият опит е затворен.')
            s.execute(update(self.table).where(self.table.c.id==row['id']).values(state='AWAITING_OWNER_APPROVAL',stage='EMAIL_PREPARED',**mail))
        return self.read(user,company,ident)
    def failed(self,user,company,ident):
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,ident)
            s.execute(update(self.table).where(self.table.c.id==row['id'],self.table.c.state=='PREPARING').values(state='FAILED'))
    def reserve_send(self,user,company,ident,expected_hash):
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,ident)
            if (row['state']!='AWAITING_OWNER_APPROVAL' or row['send_attempts'] or row['mime_sha256']!=expected_hash
                or sha256(bytes(row['mime'])).hexdigest()!=expected_hash):raise InteractionError('Изпращането е блокирано или опитът е използван.')
            s.execute(update(self.table).where(self.table.c.id==row['id']).values(state='SENDING',send_attempts=1))
            return row
    def sent(self,user,company,ident,state,gmail_id=None):
        if state not in {'EMAIL_ACCEPTED','MOCK_EMAIL_ACCEPTED','EMAIL_UNKNOWN','EMAIL_REJECTED'}:raise ValueError('Invalid mail state')
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,ident)
            if row['state']!='SENDING':raise InteractionError('Няма активен тестов опит за изпращане.')
            s.execute(update(self.table).where(self.table.c.id==row['id']).values(state=state,gmail_id=gmail_id))
