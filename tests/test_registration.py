"""Actual task-owned disposable PostgreSQL; all AI/source/Gmail HTTP is mocked."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4
from email.parser import BytesParser
from email.policy import SMTP
import json,os
import httpx,pytest
from sqlalchemy import select,insert,update,text,func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from alembic import command
from ai_tender_intelligence.persistence.database import Database,DatabaseSettings,PersistenceError
from ai_tender_intelligence.persistence.migrate import migration_config
from ai_tender_intelligence.persistence.profiles import VerifiedAssignmentSet
from ai_tender_intelligence.persistence.schema import users,companies,intents,attempts
from ai_tender_intelligence.catalogue.importer import CatalogueImporter
from ai_tender_intelligence.catalogue.validation import CatalogueReader
from ai_tender_intelligence.discovery.migrate import upgrade as discovery_upgrade
from ai_tender_intelligence.beta67 import schema
from ai_tender_intelligence.beta67.store import Profiles,BetaStore,Requests,Access
from ai_tender_intelligence.beta67.service import Coordinator,STARTED,PROCESSING,COMPLETED,FAILED
from ai_tender_intelligence.beta67.telegram import BetaInteraction,Search
from ai_tender_intelligence.beta67.classification import Luna,LunaWorker
from ai_tender_intelligence.assets import root as asset_root
from ai_tender_intelligence.demo41 import mail
from ai_tender_intelligence.analysis.provider import RuntimeProvider
from ai_tender_intelligence.telegram.models import ProfileFields,ManualClock,InputEvent,InteractionError
from ai_tender_intelligence.telegram.models import canonical_label
from ai_tender_intelligence.telegram.interaction import Snapshot
from helpers import run as mocked_flow
from ai_tender_intelligence.search_results.service import SearchResults
from ai_tender_intelligence.classification.openai_provider import OpenAISettings
from ai_tender_intelligence.classification.contract import build_request
from ai_tender_intelligence.persistence.profiles import CATEGORY_KEYS
from helpers import classification_response as response
from helpers import envelope,http_response

from ai_tender_intelligence.bootstrap import setup as bootstrap
from ai_tender_intelligence.persistence.identity import RegisteredUsers
pytestmark=pytest.mark.postgres
FIELDS=ProfileFields('Synthetic business','a@example.invalid',('Строителство',),
    (('Строителство','Ремонт на покриви'),),frozenset())

@pytest.fixture(scope='module')
def database():
    url=os.environ.get('P5_TEST_DATABASE_URL')
    if not url:pytest.skip('Explicit disposable local PostgreSQL required')
    if not make_url(url).database.startswith('p5_test_'):pytest.fail('Refusing a non-disposable database')
    db=Database(DatabaseSettings(url,'beta-test'))
    bootstrap(db)
    yield db
    db.close()

@pytest.fixture
def setup(database):
    with database.engine.begin() as c:
        assert c.scalar(text('SELECT current_database()')).startswith('p5_test_')
        c.execute(text('TRUNCATE beta67_invites,telegram_users CASCADE'))
    policy=RegisteredUsers(database)
    return database,Profiles(database,policy),BetaStore(database,policy)

def company(profiles,user=101,*,ready=True,email=None,key='create'):
    profiles.register(user)
    fields=replace(FIELDS,email=email or ('a@example.invalid' if user==101 else 'b@example.invalid'))
    c=profiles.create_company(user,fields,confirmation_key=key+str(user))
    if ready:
        pending=profiles.pending_intent(user,c.id)
        snapshot=CatalogueReader(profiles.db).snapshot()
        profiles.apply_verified_classification(user,c.id,pending.id,
            VerifiedAssignmentSet(snapshot.version_id,('45261900',),'SYNTHETIC-TEST','synthetic-test'))
    return profiles.company(user,c.id)

class Immediate:
    def submit(self,fn,*a):fn(*a);return SimpleNamespace()
    def shutdown(self,**kw):pass

def coordinator(store,*,fault=None,executor=None):
    calls=[];emails=[]
    def factory(*a):
        flow,_,sources,requests=mocked_flow(fault,internal_beta=True)
        provider=flow.provider;flow.store=store;flow.provider=RuntimeProvider()
        calls.append(requests)
        return flow,provider
    def gmail(recipient):
        def handler(req):
            import base64
            raw=base64.urlsafe_b64decode(json.loads(req.content)['raw'])
            parsed=BytesParser(policy=SMTP).parsebytes(raw)
            emails.append((str(parsed['To']),raw))
            if fault=='gmail-timeout':raise httpx.ReadTimeout('mock uncertainty')
            return httpx.Response(200,json={'id':'mock-beta67-'+str(len(emails))})
        return mail.Gmail('mock-only',recipient=recipient,transport=httpx.MockTransport(handler))
    return Coordinator(store,factory,gmail,enabled=True,executor=executor or Immediate()),calls,emails

def test_profiles_jobs_requests_and_recipients_are_private(setup):
    db,p,store=setup;a=company(p);b=company(p,202)
    p.edit_fields(101,a.id,email='updated@example.invalid',expected_revision=a.revision)
    assert p.company(202,b.id).fields.email=='b@example.invalid'
    row,_=store.begin(101,a.id,'new-analysis-unique-key')
    ledger=Requests(store);ident=ledger.reserve(101,a.id,'one')
    for action in [lambda:p.company(202,a.id),lambda:p.edit_fields(202,a.id,email='stolen@example.invalid'),
        lambda:store.read(202,b.id,row['id']),lambda:store.read(202,a.id,row['id']),
        lambda:ledger.read(202,b.id,ident),lambda:ledger.reserve(202,a.id,'steal')]:
        with pytest.raises(InteractionError):action()
    assert store.read(101,a.id,row['id'])['recipient']=='updated@example.invalid'

@pytest.mark.parametrize('fault',['wrong_owner','wrong_company','changed_recipient'])
def test_database_owner_trigger_rejects_cross_links_and_identity_mutation(setup,fault):
    db,p,store=setup;a=company(p);b=company(p,202)
    row,_=store.begin(101,a.id,'database-boundary')
    with pytest.raises(PersistenceError):
        with db.transaction() as s:
            if fault=='changed_recipient':s.execute(update(schema.runs).where(schema.runs.c.id==row['id']).values(recipient='b@example.invalid'))
            else:s.execute(insert(schema.requests).values(id=uuid4(),scope=db.settings.scope,owner=202,
                owner_user_id=UUID(a.id) if fault=='wrong_owner' else row['owner_user_id'],
                company=UUID(a.id),kind='ANALYSIS',request_hash='bad',recipient='b@example.invalid',state='RUNNING'))

def test_new_analysis_after_failure_preserves_the_original_request_and_job(setup):
    db, profiles, store = setup
    profile = company(profiles)
    selection = {'parent': 1, 'lots': [2]}
    failing, failed_calls, failed_emails = coordinator(store, fault='http-failure')
    original = failing.submit(101, profile.id, selection, 'failed-confirmation')
    restarted = BetaStore(db, {101})
    ledger = Requests(restarted)
    previous = ledger.read(101, profile.id, original)
    failed_job = restarted.read(101, profile.id, previous['job'])
    assert previous['state'] == failed_job['state'] == 'FAILED'
    assert len(failed_calls[0]) == 1 and not failed_emails

    succeeding, calls, emails = coordinator(restarted)
    with pytest.raises(InteractionError, match='вече е приета'):
        succeeding.submit(101, profile.id, selection, 'failed-confirmation')
    new = succeeding.submit(101, profile.id, selection, 'new-confirmation')
    current = ledger.read(101, profile.id, new)
    assert current['state'] == 'DONE' and new != original and current['job'] != previous['job']
    assert len(calls) == len(emails) == 1
    assert ledger.read(101, profile.id, original) == previous
    assert restarted.read(101, profile.id, previous['job']) == failed_job


@pytest.mark.parametrize('same_account', [False, True])
def test_concurrent_requests_have_one_slot_no_silent_queue(setup, same_account):
    _, profiles, store = setup
    first = company(profiles)
    second = first if same_account else company(profiles, 202)
    start = Barrier(2)

    def reserve(pair):
        user, profile, key = pair
        start.wait(timeout=5)
        try:
            return Requests(store).reserve(user, profile.id, key)
        except InteractionError as error:
            assert 'обработва друга заявка' in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(reserve, [(101, first, 'first'), (second.owner, second, 'second')]))
    assert sum(value is not None for value in values) == 1

def test_completed_analyses_bind_independent_plans_and_correct_recipients(setup):
    db,p,store=setup;a=company(p);b=company(p,202)
    co,calls,emails=coordinator(store)
    jobs = set()
    ledger = Requests(store)
    for user,c,key in [(101,a,'owner-request'),(202,b,'owner-request'),(101,a,'separate-confirmation')]:
        request = co.submit(user,c.id,{'parent':1,'lots':[2]},key)
        events=co.drain()
        with db.transaction() as s:diagnostics=list(s.scalars(select(schema.requests.c.diagnostic)))
        assert events==[(user,'PROCESSING'),(user,'COMPLETED')],diagnostics
        saved = ledger.read(user,c.id,request)
        row = store.read(user,c.id,saved['job'])
        assert saved['state'] == 'DONE' and row['id'] not in jobs
        jobs.add(row['id'])
        assert row['state']=='MOCK_EMAIL_ACCEPTED' and row['send_attempts']==1
        assert row['evidence']['BETA67_AUTHORITY']['ai_approval']['actor']==user
        assert row['evidence']['RUN_STARTED46']['replay_allowed'] is False
        assert len([k for k in row['evidence'] if k.startswith('DISPATCH45_')])==9
        assert row['plain'].count('БЕТА ВЕРСИЯ — ДЕМОНСТРАЦИОНЕН АНАЛИЗ')==1
        # A completed send cannot be reserved again or redirected by another account.
        with pytest.raises(InteractionError):
            store.reserve_send(user,c.id,row['id'],row['mime_sha256'])
        other=202 if user==101 else 101
        with pytest.raises(InteractionError):
            store.reserve_send(other,c.id,row['id'],row['mime_sha256'])
    assert [r for r,_ in emails]==['a@example.invalid','b@example.invalid','a@example.invalid']
    assert all([c['model'] for c in batch]==['gpt-6.1-sol']*8+['gpt-6.1-terra'] for batch in calls)
    assert all(c['max_output_tokens']==100000 for batch in calls for c in batch)
    with pytest.raises(InteractionError):co.submit(101,a.id,{'parent':1,'lots':[2]},'owner-request')
    assert len(calls)==len(emails)==3


def test_legacy_index_migration_is_explicit_idempotent_and_preserves_history(setup):
    db, profiles, store = setup
    profile = company(profiles)
    migration = (asset_root() / 'migrations/beta/0001_remove_lifetime_analysis_limit.sql').read_text(encoding='utf-8')

    def apply_migration():
        with db.engine.begin() as connection:
            connection.exec_driver_sql(migration)

    def indexes():
        with db.transaction() as session:
            return set(session.scalars(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='beta67_requests'"
            )))

    legacy = 'uq_beta68_one_analysis_account'
    assert legacy not in indexes()
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_beta68_one_analysis_account "
            "ON public.beta67_requests (scope, owner) WHERE kind='ANALYSIS'"
        )
    ledger = Requests(store)
    try:
        original = ledger.reserve(101, profile.id, 'legacy-confirmation')
        with pytest.raises(DBAPIError, match='BETA_PROCESSING_SLOT_BUSY'):
            apply_migration()
        assert legacy in indexes()
        ledger.finish(101, profile.id, original, False, {'code': 'LEGACY_FAILURE'})
        before = ledger.read(101, profile.id, original)
        with pytest.raises(PersistenceError):
            ledger.reserve(101, profile.id, 'new-confirmation')
        schema.install(db)
        assert legacy in indexes()  # Existing installations use the explicit migration.

        apply_migration()
        apply_migration()
        schema.install(db)
        assert legacy not in indexes() and 'uq_beta67_capacity' in indexes()
        assert ledger.reserve(101, profile.id, 'new-confirmation') != original
        assert ledger.read(101, profile.id, original) == before
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql("DROP INDEX IF EXISTS public.uq_beta68_one_analysis_account")


@pytest.mark.parametrize('entrypoint', ['button', 'command'])
def test_telegram_requires_fresh_owner_confirmation_for_a_paid_analysis(setup, entrypoint):
    _, profiles, store = setup
    profile = company(profiles)
    co, calls, emails = coordinator(store)
    title = 'Синтетична поръчка'
    opportunity = SimpleNamespace(record_id='record', title=title, original_id='EOP1',
                                  lots=[SimpleNamespace(lot_id='LOT-2')])
    stored = SimpleNamespace(id='search-id',
        result=SimpleNamespace(matches=SimpleNamespace(opportunities=[opportunity])),
        source_payloads={'record': {'official_url':'https://app.eop.bg/today/1',
            'lots':[{'id':'LOT-2','source_fields':{'tenderId':2}}]}})

    def read(user, selected_company, reference):
        assert (user, selected_company, reference) == (101, profile.id, stored.id)
        return stored

    tenders = (SimpleNamespace(id='procedure'),)
    search = SimpleNamespace(results=SimpleNamespace(read=read),
        stored_results=lambda *args: tenders, snapshot_reference=lambda *args: stored.id)
    clock = ManualClock()
    ui = BetaInteraction(SimpleNamespace(profiles=profiles, classifications=profiles, search=search),
                         clock, {101}, coordinator=co)
    ui.handle(InputEvent(101,101,0,text='/start'))
    ui.snapshots['snapshot'] = Snapshot(101,profile.id,tenders,clock.now(),stored_reference=stored.id)

    def select_analysis(update_id):
        if entrypoint == 'command':
            return ui.handle(InputEvent(101,101,update_id,text='/analyze44 1'))[0]
        card = ui._button(101,'Подробен анализ','deep','snapshot/procedure',profile.id)
        return ui.handle(InputEvent(101,101,update_id,callback=card.callback_data))[0]

    review = select_analysis(1)
    confirm, cancel = review.keyboard[0]
    assert title in review.text and 'платения' in review.text
    assert not calls and not emails and not store.entries(101,profile.id)
    denied = ui.handle(InputEvent(202,202,2,callback=confirm.callback_data))
    assert 'невалидно' in denied[0].text and not calls
    ui.handle(InputEvent(101,101,3,callback=cancel.callback_data))
    assert not ui.handle(InputEvent(101,101,4,callback=confirm.callback_data))
    assert not calls

    review = select_analysis(5)
    confirm, _ = review.keyboard[0]
    started = ui.handle(InputEvent(101,101,6,callback=confirm.callback_data))
    assert any(STARTED in message.text for message in started)
    assert not ui.handle(InputEvent(101,101,7,callback=confirm.callback_data))
    assert len(calls)==len(emails)==1 and len(store.entries(101,profile.id))==1

@pytest.mark.parametrize('fault',['http-failure','all-invalid-beta','gmail-timeout'])
def test_failure_never_emits_false_success_or_replays(setup,fault):
    db,p,store=setup;a=company(p)
    co,calls,emails=coordinator(store,fault=fault)
    co.submit(101,a.id,{'parent':1,'lots':[2]},'failure')
    events=co.drain()
    assert (101,'FAILED') in events and (101,'COMPLETED') not in events
    row=store.entries(101,a.id)[0]
    if fault=='gmail-timeout':assert row['state']=='EMAIL_UNKNOWN' and len(emails)==1 and row['send_attempts']==1
    else:assert row['state']=='FAILED' and not emails
    with pytest.raises(InteractionError):co.submit(101,a.id,{'parent':1,'lots':[2]},'failure')

def test_profile_wizard_registers_two_users_and_confirms_email_edits(setup):
    db,p,store=setup;co,_,_=coordinator(store)
    ports=SimpleNamespace(profiles=p,classifications=p,search=SimpleNamespace())
    ui=BetaInteraction(ports,ManualClock(),{101,202},coordinator=co)
    sequence=0
    def event(user,*,text=None,callback=None):
        nonlocal sequence
        sequence+=1
        return ui.handle(InputEvent(user,user,sequence,text=text,callback=callback))
    def click(user,messages,label):
        button=next(b for m in messages for row in m.keyboard for b in row if canonical_label(b.text)==canonical_label(label))
        return event(user,callback=button.callback_data)
    for user,email in [(101,'a@example.invalid'),(202,'b@example.invalid')]:
        messages=event(user,text='/start')
        messages=click(user,messages,'Създай бизнес профил')
        messages=event(user,text='Фирма '+str(user))
        assert 'reports@example.invalid' not in messages[0].text
        messages=event(user,text=email)
        messages=click(user,messages,'Строителство')
        messages=click(user,messages,'Готово')
        messages=event(user,text='Ремонт на покриви')
        messages=click(user,messages,'Готово')
        assert email in ''.join(m.text for m in messages)
        messages=click(user,messages,'Потвърди')
        c=p.companies(user)[0]
        assert c.fields.email==email
        # Field edit remains uncommitted until the separate email confirmation.
        ui.selected[user]=c.id
        ui._edit(user,c.id,'1')
        confirmation=event(user,text='new'+str(user)+'@example.invalid')
        assert p.company(user,c.id).fields.email==email
        click(user,confirmation,'Потвърди имейл')
        assert p.company(user,c.id).fields.email=='new'+str(user)+'@example.invalid'
        event(user,text='/start')
        assert len(p.companies(user))==1
    assert ui.handle(InputEvent(999,999,999,text='/start'))
    assert p.companies(101)[0].id!=p.companies(202)[0].id

@pytest.mark.parametrize('outcome',['success','not_sent','uncertain','no_match','unknown_code'])
def test_existing_luna_contract_one_attempt_and_no_retry(setup,outcome):
    db,p,store=setup;c=company(p,ready=False)
    intent=p.pending_intent(101,c.id)
    ident=Requests(store).reserve(101,c.id,'intent:'+intent.id,intent=intent.id)
    snapshot=CatalogueReader(db).snapshot()
    request=build_request(snapshot,[{'category_key':CATEGORY_KEYS[k],'activity':a} for k,a in intent.categories])
    calls=[]
    def handle(req):
        calls.append(req)
        if outcome=='not_sent':return httpx.Response(401,json={'error':{'type':'invalid_api_key'}})
        if outcome=='uncertain':raise httpx.ReadTimeout('synthetic')
        body = response(request)
        if outcome == 'no_match':
            body['selections'] = []
            body['category_reviews'][0]['no_match_reason'] = 'Няма подкрепен кандидат.'
        elif outcome == 'unknown_code':
            body['selections'][0]['identifier'] = '99999999-9'
        return http_response(envelope(json.dumps(body,ensure_ascii=False)))
    provider=Luna(OpenAISettings(True,'mock-only'),transport=httpx.MockTransport(handle))
    worker=LunaWorker(store,Requests(store),101,c.id,ident,intent.id,provider)
    result=worker.run_once()
    assert len(calls)==1
    assert worker.run_once()=='idle' and len(calls)==1
    with db.transaction() as s:
        rows=s.execute(select(attempts).where(attempts.c.intent_id==UUID(intent.id))).mappings().all()
        pending=s.execute(select(intents).where(intents.c.id==UUID(intent.id))).mappings().one()
    assert len(rows)==1 and rows[0]['number']==1 and rows[0]['model']=='gpt-6-luna'
    assert rows[0]['reasoning_effort']=='high' and rows[0]['cost_basis']=='BETA67_PLATFORM_USAGE_ONLY_EUR_UNKNOWN'
    assert pending['processing_state']!='RETRY_WAIT'
    if outcome=='success':assert result=='succeeded' and p.company(101,c.id).classification=='ready'
    if outcome=='uncertain':assert pending['processing_state']=='UNCERTAIN'
    if outcome in {'no_match', 'unknown_code'}:
        expected = 'NO_SUPPORTED_MATCH' if outcome == 'no_match' else 'CODE_NOT_FOUND'
        assert result == 'failed' and rows[0]['status'] == 'SUCCEEDED'
        assert pending['status'] == 'FAILED' and pending['processing_state'] == 'FINISHED'
        assert pending['last_error'] == expected and pending['dispatch_count'] == 1
        assert pending['result_checkpoint'] is None
        failed = p.company(101, c.id)
        assert failed.classification == 'failed' and not failed.cpv
        assert p.pending_intent(101, c.id) is None

@pytest.mark.parametrize(('initial_failure', 'message'), [
    ('NO_SUPPORTED_MATCH', 'Не намерихме подходящи CPV кодове'),
    ('INVALID_RESPONSE', 'Възникна технически проблем'),
])
def test_failed_cpv_profile_recovers_only_after_telegram_confirmation(setup, initial_failure, message):
    db, profiles, store = setup
    profiles.register(101)
    profiles.register(202)
    profiles.register(303)
    fields = replace(FIELDS, regions=frozenset({'Варна'}))
    profile = profiles.create_company(101, fields, confirmation_key='initial')
    old_intent = profiles.pending_intent(101, profile.id)
    calls = []

    def handle(request):
        body = response(SimpleNamespace(**json.loads(json.loads(request.content)['input'])))
        calls.append(body)
        if len(calls) == 1:
            if initial_failure == 'INVALID_RESPONSE':
                return http_response(envelope('{'))
            body['selections'] = []
            for review in body['category_reviews']:
                review['no_match_reason'] = 'Не е намерен подходящ кандидат.'
        return http_response(envelope(json.dumps(body, ensure_ascii=False)))

    def luna(store, ledger, user, company_id, request_id, intent_id):
        provider = Luna(OpenAISettings(True, 'mock-only'), transport=httpx.MockTransport(handle))
        return LunaWorker(store, ledger, user, company_id, request_id, intent_id, provider)

    searches = []
    def execute(user, company_id, limits, *, request_key, before_fetch):
        searches.append((user, company_id))
        return SimpleNamespace(id='synthetic-search')

    search = Search(SimpleNamespace(execute=execute, read=lambda *args: SimpleNamespace(id='synthetic-search')),
                    profiles, profiles.allowed_users, lambda: None)
    co = Coordinator(store, None, None, enabled=True, luna_factory=luna, executor=Immediate())
    ui = BetaInteraction(SimpleNamespace(profiles=profiles, classifications=profiles, search=search),
                         ManualClock(), {101, 202, 303}, coordinator=co)
    sequence = 0
    def event(user=101, *, text=None, callback=None):
        nonlocal sequence
        sequence += 1
        return ui.handle(InputEvent(user, user, sequence, text=text, callback=callback))

    def button(messages, label):
        return next(b.callback_data for m in messages for row in m.keyboard for b in row
                    if canonical_label(b.text) == canonical_label(label))

    def history(intent):
        with db.transaction() as session:
            job = dict(session.execute(select(intents).where(intents.c.id == UUID(intent))).mappings().one())
            attempt = dict(session.execute(select(attempts).where(attempts.c.intent_id == UUID(intent))).mappings().one())
            reservation = dict(session.execute(select(schema.requests).where(
                schema.requests.c.intent == UUID(intent))).mappings().one())
            return job, attempt, reservation

    event(text='/start')
    assert not calls
    failed = ui.poll()
    assert len(failed) == 1 and message in failed[0].text
    assert ui.poll() == [] and len(calls) == 1
    old_history = history(old_intent.id)
    assert old_history[0]['status'] == 'FAILED' and old_history[0]['last_error'] == initial_failure
    assert old_history[2]['state'] == 'FAILED'
    with pytest.raises(InteractionError):
        search.start_search(101, profile.id, 'before-recovery')
    assert not searches
    menu = event(text='/start')
    assert message in menu[0].text
    assert all(canonical_label(b.text) != canonical_label('Търси поръчки')
               for m in menu for row in m.keyboard for b in row)
    edit_button = button(menu, 'Редактирай дейностите')
    for other in (202, 303):
        assert 'невалидно' in event(other, callback=edit_button)[0].text
        assert other not in ui.wizards
    edit = event(callback=edit_button)
    assert fields.activities[0][1] in ''.join(m.text for m in edit)
    changed = 'Ремонт на покривни конструкции'
    review = event(text=changed)
    confirm = button(review, 'Потвърди нова класификация')
    assert changed in ''.join(m.text for m in review)
    assert 'една нова платена CPV класификация' in review[-1].text
    assert profiles.company(101, profile.id).fields == fields
    assert profiles.pending_intent(101, profile.id) is None
    assert ui.poll() == [] and len(calls) == 1
    for other in (202, 303):
        assert 'невалидно' in event(other, callback=confirm)[0].text

    assert 'Дейностите са запазени' in ''.join(m.text for m in event(callback=confirm))
    pending = profiles.pending_intent(101, profile.id)
    assert pending.id != old_intent.id and pending.categories == (('Строителство', changed),)
    assert event(callback=confirm) == []
    assert len(calls) == 1
    with pytest.raises(InteractionError):
        search.start_search(101, profile.id, 'pending-recovery')
    # A delayed completion event from the original intent must not affect the new one.
    co.events.put((101, 'CPV_FAILED', profile.id, old_intent.id))
    assert ui.poll() == [] and len(calls) == 2
    ready = ui.poll()
    assert 'Профилът е готов' in ready[0].text and button(ready, 'Търси поръчки')
    assert ui.poll() == [] and not co.classify(101, profile.id) and len(calls) == 2
    recovered = profiles.company(101, profile.id)
    assert recovered.id == profile.id and recovered.owner == profile.owner
    assert recovered.classification == 'ready' and recovered.cpv
    assert recovered.fields == replace(fields, activities=(('Строителство', changed),))
    search.start_search(101, profile.id, 'after-recovery')
    assert searches == [(101, profile.id)]
    assert history(old_intent.id) == old_history
    job, attempt, reservation = history(pending.id)
    assert job['dispatch_count'] == 1 and attempt['number'] == 1
    assert (job['status'], attempt['status'], reservation['state']) == ('SUCCEEDED', 'SUCCEEDED', 'DONE')


def test_cpv_recovery_serializes_confirmations_across_three_users(setup):
    db, profiles, store = setup
    accounts = {user: company(profiles, user, ready=False) for user in (101, 202, 303)}
    for user, profile in accounts.items():
        pending = profiles.pending_intent(user, profile.id)
        profiles.fail_classification(user, profile.id, pending.id)
    activities = (('Строителство', 'Подмяна на керемиди'),)
    first = accounts[101]
    with pytest.raises(InteractionError):
        profiles.recover_classification(101, first.id, first.fields.activities,
                                        confirmation_key='unchanged', expected_revision=first.revision)
    with pytest.raises(InteractionError):
        profiles.recover_classification(202, first.id, activities, confirmation_key='foreign',
                                        expected_revision=first.revision)

    def confirm(user, key='confirmed'):
        profile = accounts[user]
        return profiles.recover_classification(user, profile.id, activities,
                                               confirmation_key=key, expected_revision=profile.revision)

    barrier = Barrier(3, timeout=10)
    def race(user):
        barrier.wait()
        try:
            return confirm(user)
        except InteractionError as exc:
            assert 'Изчакайте текущата CPV класификация' in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        winners = [(u, intent) for u, intent in zip(accounts, pool.map(race, accounts)) if intent]
    assert len(winners) == 1
    winner, intent = winners[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(confirm, (winner, winner))) == [intent, intent]
    waiting = next(user for user in accounts if user != winner)
    with pytest.raises(InteractionError, match='Изчакайте текущата CPV класификация'):
        confirm(waiting)
    assert profiles.pending_intent(waiting, accounts[waiting].id) is None
    # Cover the interval after the worker finalizes the intent, before releasing its Beta slot.
    ledger = Requests(store)
    reservation = ledger.reserve(winner, accounts[winner].id, 'intent:' + intent, intent=intent)
    profiles.fail_classification(winner, accounts[winner].id, intent)
    with pytest.raises(InteractionError, match='Изчакайте текущата CPV класификация'):
        confirm(waiting)
    ledger.finish(winner, accounts[winner].id, reservation, False, {'kind': 'CPV'})
    assert confirm(waiting) != intent
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(users)) == 3
        assert session.scalar(select(func.count()).select_from(schema.requests)
                              .where(schema.requests.c.kind == 'ANALYSIS')) == 0


def test_recipient_change_after_preparation_stops_send_without_rerouting(setup):
    db,p,store=setup;c=company(p)
    row,_=store.begin(101,c.id,'recipient-race')
    message=mail.prepare('<p>Бета</p>','Бета',row['id'],recipient='a@example.invalid')
    store.finish(101,c.id,row['id'],message)
    p.edit_fields(101,c.id,email='changed@example.invalid')
    with pytest.raises(InteractionError):store.recipient_for(101,c.id,row['id'])
    with pytest.raises(InteractionError):store.reserve_send(101,c.id,row['id'],message['mime_sha256'])
    assert store.read(101,c.id,row['id'])['send_attempts']==0

def test_partial_beta_findings_continue_and_exclusions_stay_internal(setup):
    db,p,store=setup;c=company(p)
    co,calls,emails=coordinator(store,fault='mixed-beta')
    co.submit(101,c.id,{'parent':1,'lots':[2]},'partial-beta')
    assert co.drain()==[(101,'PROCESSING'),(101,'COMPLETED')]
    row=store.entries(101,c.id)[0]
    beta=row['evidence']['TERRA']['canonical_report']['canonical_input']['beta59']
    assert beta['excluded_findings'] and beta['missing_categories']
    assert 'bad-number' not in row['plain'] and 'UNSUPPORTED_NUMBER' not in row['plain']
    assert len(calls[0])==9 and len(emails)==1

def test_background_analysis_does_not_hold_the_telegram_thread(setup):
    from threading import Event
    db,p,store=setup;a=company(p);b=company(p,202)
    waiting,release=Event(),Event()
    co,calls,emails=coordinator(store,executor=ThreadPoolExecutor(max_workers=1))
    factory=co.flow_factory
    def slow_factory(*a):
        waiting.set();assert release.wait(10)
        return factory(*a)
    co.flow_factory=slow_factory
    try:
        co.submit(101,a.id,{'parent':1,'lots':[2]},'background')
        assert waiting.wait(3)
        # Main/UI thread can read another user's profile while the worker waits.
        assert p.companies(202)[0].id==b.id
        with pytest.raises(InteractionError,match='обработва'):co.submit(202,b.id,{},'busy')
    finally:
        release.set();co.close()
    assert (101,'COMPLETED') in co.drain() and len(emails)==1

def test_completed_request_cannot_be_reset_in_database(setup):
    db,p,store=setup;c=company(p)
    ledger=Requests(store);ident=ledger.reserve(101,c.id,'one-shot')
    ledger.finish(101,c.id,ident,False,{'code':'MOCK_STOP'})
    with pytest.raises(PersistenceError):
        with db.transaction() as s:s.execute(update(schema.requests).where(schema.requests.c.id==UUID(ident)).values(state='RUNNING'))

def test_unvalidated_or_foreign_email_cannot_use_beta_send_authority(setup):
    from ai_tender_intelligence.beta67.delivery import SendPermit
    db,p,store=setup;a=company(p);b=company(p,202)
    ledger=Requests(store);ident=ledger.reserve(101,a.id,'unvalidated-envelope')
    row,_=store.begin(101,a.id,'unvalidated-envelope-job')
    ledger.bind(101,a.id,ident,row)
    message=mail.prepare('<p>Непроверено</p>','Непроверено',row['id'],recipient='a@example.invalid')
    store.finish(101,a.id,row['id'],message)
    permit=SendPermit(ident,101,a.id,str(row['id']),message['mime_sha256'],'a@example.invalid')
    calls=[]
    gmail=mail.Gmail('mock-only',recipient='a@example.invalid',transport=httpx.MockTransport(
        lambda r:(calls.append(r) or httpx.Response(200,json={'id':'should-not-send'}))))
    with pytest.raises(InteractionError):mail.send(store,101,a.id,row['id'],message['mime_sha256'],gmail,_beta_permit=permit)
    with pytest.raises(InteractionError):permit.verify(store,202,b.id,row['id'],message['mime_sha256'])
    assert not calls and store.read(101,a.id,row['id'])['send_attempts']==0


def test_paid_coordinator_defaults_closed_even_for_ready_registered_owner(setup):
    db,p,store=setup;c=company(p)
    calls=[]
    def forbidden(*args):
        calls.append(args)
        raise AssertionError('Disabled paid execution reached a provider')
    co=Coordinator(store,forbidden,forbidden,executor=Immediate())
    try:
        with pytest.raises(InteractionError):co.submit(101,c.id,{},'not-authorized')
        assert not store.entries(101,c.id) and not calls
    finally:co.close()
