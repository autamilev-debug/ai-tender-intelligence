"""Open ingress, atomic three-account capacity and private snapshots on PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select,insert,func
from ai_tender_intelligence.persistence.schema import users
from ai_tender_intelligence.persistence.database import PersistenceError
from ai_tender_intelligence.persistence.identity import RegisteredUsers,PublicTelegramUsers
from ai_tender_intelligence.beta67.store import Access,BetaStore,Requests
from ai_tender_intelligence.beta67.telegram import BetaInteraction
from ai_tender_intelligence.telegram.models import InputEvent,ManualClock,InteractionError
from ai_tender_intelligence.telegram.transport import normalize_update
from ai_tender_intelligence.search_results.service import SearchResults
from ai_tender_intelligence.discovery.fresh_models import FreshResult,SearchLimits
from ai_tender_intelligence.matching.models import MatchResult
from test_registration import database,setup,company,coordinator

pytestmark=pytest.mark.postgres


def test_any_private_telegram_identity_can_start_without_invitation(setup):
    db,p,store=setup
    ui=BetaInteraction(SimpleNamespace(profiles=p,classifications=p),ManualClock(),{1},coordinator=None)
    incoming=normalize_update({'update_id':1,'message':{'message_id':1,'from':{'id':909,'is_bot':False},
        'chat':{'id':909,'type':'private'},'text':'/start'}},PublicTelegramUsers())
    assert incoming is not None and ui.handle(incoming.event)
    assert 909 in RegisteredUsers(db) and p.companies(909)==()
    assert normalize_update({'update_id':2,'message':{'message_id':2,'from':{'id':910,'is_bot':False},
        'chat':{'id':-1,'type':'group'},'text':'/start'}},PublicTelegramUsers()) is None


def test_existing_owner_counts_and_concurrent_new_accounts_cannot_exceed_three(setup):
    db,p,store=setup
    owner=p.register(101)
    def register(user):
        try:return Access(db).register(user)
        except InteractionError:return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(register,[202,303,404,505,606,707]))
    assert sum(x is not None for x in results)==2
    assert p.register(101)==owner  # Returning account still works at capacity.
    with db.transaction() as s:assert s.scalar(select(func.count()).select_from(users))==3


def test_duplicate_registration_is_idempotent_under_concurrency(setup):
    db,p,store=setup
    with ThreadPoolExecutor(max_workers=5) as pool:values=list(pool.map(p.register,[101]*10))
    assert len(set(values))==1
    with db.transaction() as s:assert s.scalar(select(func.count()).select_from(users))==1


def test_database_guard_blocks_direct_fourth_account_insert(setup):
    db,p,store=setup
    for user in (101,202,303):p.register(user)
    with pytest.raises(PersistenceError):
        with db.transaction() as s:s.execute(insert(users).values(id=uuid4(),scope=db.settings.scope,
            telegram_user_id=404,private_chat_id=404))


def test_unregistered_and_pending_profiles_cannot_initiate_paid_analysis(setup):
    db,p,store=setup
    with pytest.raises(InteractionError):Requests(store).reserve(404,uuid4(),'unregistered')
    pending=company(p,101,ready=False)
    co,models,emails=coordinator(store)
    with pytest.raises(InteractionError):co.submit(101,pending.id,{'parent':1,'lots':[]},'pending')
    assert not models and not emails


def test_services_created_before_registration_accept_new_accounts_and_isolate_searches(setup):
    db,p,store=setup
    policy=RegisteredUsers(db)
    class EmptyOfficialResult:
        def run(self,actor,cid,limits):
            c=p.company(actor,cid)
            from ai_tender_intelligence.catalogue.validation import CatalogueReader
            matches=MatchResult(c.id,c.revision,'2026-01-01T00:00:00+00:00','synthetic',
                CatalogueReader(db).snapshot().version_id,'synthetic',(),())
            return FreshResult('2026-01-01T00:00:00+00:00','2026-01-01T00:00:01+00:00',
                '2025-12-01','2025-12-31',(),(),'COMPLETE_WITHIN_BOUNDS',(),(),(),matches)
    search=SearchResults(db,policy,EmptyOfficialResult())
    a=company(p,101);b=company(p,202)
    limits=SearchLimits(date(2025,12,1),date(2025,12,31))
    first=search.execute(101,a.id,limits,request_key='independent-search-a')
    second=search.execute(202,b.id,limits,request_key='independent-search-b')
    assert first.id!=second.id
    for user,cid,sid in ((202,b.id,first.id),(101,a.id,second.id),(202,a.id,first.id)):
        with pytest.raises(InteractionError):search.read(user,cid,sid)
