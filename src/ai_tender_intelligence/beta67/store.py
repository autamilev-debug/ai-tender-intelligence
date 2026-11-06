from ..persistence.identity import freeze_users
"""Invited identity, private jobs and atomic one-request-per-tester allowances."""
from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4, UUID
from sqlalchemy import select, insert, update, func, text
from ..persistence.profiles import PostgresProfiles, _email
from ..demo41.store import DemoStore
from ..telegram.models import InteractionError
from .schema import invites, runs, requests
from ..persistence.schema import users

DENIED='Първо се регистрирайте чрез /start.'
CAPACITY='Бета версията е ограничена до три регистрирани акаунта. В момента няма свободни места.'
REGISTRATION_LOCK=0x5035524547
BUSY='В момента се обработва друга заявка. Моля, опитайте отново по-късно.'
LIMIT='Разрешеният брой Бета анализи е използван. Няма да бъде стартиран нов анализ.'
LOCK=0x503542455441


class Access:
    def __init__(self,db):self.db=db
    def registered(self):
        from ..persistence.identity import RegisteredUsers
        return RegisteredUsers(self.db)
    def register(self,user):
        if type(user) is not int or not 0<user<2**63:raise InteractionError(DENIED)
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        with self.db.transaction() as s:
            s.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':REGISTRATION_LOCK})
            existing=s.execute(select(users).where(users.c.scope==self.db.settings.scope,
                users.c.telegram_user_id==user)).mappings().first()
            if existing is not None:
                if existing['status']!='ACTIVE':raise InteractionError(DENIED)
                ident=existing['id']
            else:
                count=s.scalar(select(func.count()).select_from(users).where(users.c.scope==self.db.settings.scope))
                if count>=3:raise InteractionError(CAPACITY)
                ident=uuid4()
                s.execute(insert(users).values(id=ident,scope=self.db.settings.scope,
                    telegram_user_id=user,private_chat_id=user))
            # Existing storage foreign keys retain this automatically populated
            # membership row. It is not an invitation or administrator gate.
            s.execute(pg_insert(invites).values(scope=self.db.settings.scope,telegram_id=user,analysis_limit=1)
                      .on_conflict_do_nothing())
            return str(ident)
    def guard(self,user,*,session=None):
        if type(user) is not int or not 0<user<2**63:raise InteractionError(DENIED)
        if session is None:
            with self.db.transaction() as s:return self.guard(user,session=s)
        row=session.execute(select(invites).where(invites.c.scope==self.db.settings.scope,
            invites.c.telegram_id==user)).mappings().first()
        if row is None:raise InteractionError(DENIED)
        return dict(row)


class Profiles(PostgresProfiles):
    def _actor(self,user):
        super()._actor(user);Access(self.db).guard(user)
    def register(self,user):
        return Access(self.db).register(user)
    def delete_company(self,*a,**kw):
        raise InteractionError('Изтриването не е включено в ограничената Бета версия.')


class BetaStore(DemoStore):
    table=runs
    def __init__(self,db,allowed):
        super().__init__(db,allowed);self.profiles=Profiles(db,freeze_users(allowed))
    def initial_values(self,company):
        return {'recipient':_email(company['report_email']),'owner_user_id':company['owner_user_id']}
    def recipient_for(self,user,company,job):
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,job)
            if row['recipient']!=_email(c['report_email']):
                raise InteractionError('Имейлът е променен след заявката. Изпращането е спряно.')
            return row['recipient']
    def reserve_send(self,user,company,job,expected_hash):
        # The owner's row lock is shared with profile edits. Check inside the
        # same transaction that durably consumes the one-use send allowance.
        with self.access(user,company) as (s,c):
            row=self._row(s,c,user,job)
            from hashlib import sha256
            if (row['recipient']!=_email(c['report_email']) or row['state']!='AWAITING_OWNER_APPROVAL'
                or row['send_attempts'] or row['mime_sha256']!=expected_hash
                or sha256(bytes(row['mime'])).hexdigest()!=expected_hash):
                raise InteractionError('Изпращането е блокирано или вече е заявено.')
            s.execute(update(runs).where(runs.c.id==row['id']).values(state='SENDING',send_attempts=1))
            return row
    def entries(self,user,company):
        with self.access(user,company) as (s,c):
            return [dict(r) for r in s.execute(select(runs).where(runs.c.scope==self.db.settings.scope,
                runs.c.owner==user,runs.c.company==c['id']).order_by(runs.c.created_at.desc()).limit(20)).mappings()]


class Requests:
    def __init__(self,store):self.store=store;self.db=store.db;self.access=Access(self.db)
    def reserve(self,user,company,key,*,intent=None):
        kind='CPV' if intent is not None else 'ANALYSIS'
        hashed=sha256((kind+':'+key).encode()).hexdigest()
        with self.store.access(user,company) as (s,c):
            invite=self.access.guard(user,session=s)
            s.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':LOCK})
            old=s.execute(select(requests).where(requests.c.scope==self.db.settings.scope,
                requests.c.owner==user,requests.c.request_hash==hashed)).mappings().first()
            if old is not None:raise InteractionError('Заявката вече е приета. Няма да бъде повторена.')
            if s.scalar(select(func.count()).select_from(requests).where(
                requests.c.scope==self.db.settings.scope,requests.c.state=='RUNNING')):
                raise InteractionError(BUSY)
            if kind=='ANALYSIS':
                count=s.scalar(select(func.count()).select_from(requests).where(
                    requests.c.scope==self.db.settings.scope,requests.c.owner==user,requests.c.kind=='ANALYSIS'))
                if count>=1:raise InteractionError(LIMIT)
                if c['classification_status']!='READY' or self.store.profiles._pending(s,c['id']):
                    raise InteractionError('Изчакайте подготовката на CPV кодовете за профила.')
            ident=uuid4()
            s.execute(insert(requests).values(id=ident,scope=self.db.settings.scope,owner=user,
                owner_user_id=c['owner_user_id'],company=c['id'],kind=kind,request_hash=hashed,
                recipient=_email(c['report_email']),intent=UUID(str(intent)) if intent else None,state='RUNNING'))
            return str(ident)
    def read(self,user,company,ident,*,session=None):
        if session is None:
            with self.store.access(user,company) as (s,c):return self.read(user,company,ident,session=s)
        self.access.guard(user,session=session)
        row=session.execute(select(requests).where(requests.c.id==UUID(str(ident)),
            requests.c.scope==self.db.settings.scope,requests.c.owner==user,
            requests.c.company==UUID(str(company)))).mappings().first()
        if row is None:raise InteractionError(DENIED)
        return dict(row)
    def bind(self,user,company,ident,row):
        with self.store.access(user,company) as (s,c):
            request=self.read(user,company,ident,session=s)
            job=self.store._row(s,c,user,row['id'])
            if (request['state']!='RUNNING' or request['job'] is not None or job['state']!='PREPARING'
                or request['recipient']!=job['recipient'] or 'RUN_STARTED46' in job['evidence']):
                raise InteractionError('Заявката не може да бъде изпълнена повторно.')
            s.execute(update(requests).where(requests.c.id==request['id']).values(job=job['id']))
    def finish(self,user,company,ident,ok,diagnostic):
        with self.store.access(user,company) as (s,c):
            request=self.read(user,company,ident,session=s)
            if request['state']!='RUNNING':return
            s.execute(update(requests).where(requests.c.id==request['id']).values(
                state='DONE' if ok else 'FAILED',finished_at=func.now(),diagnostic=diagnostic))
