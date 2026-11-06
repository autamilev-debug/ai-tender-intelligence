"""Synthetic offline pipeline regressions; no live services."""
import pytest
from helpers import *

def test_fresh_full_chain_model_order_and_email_without_history_or_send():
    flow,store,sources,calls=run();selection={'parent':1,'lots':[2]}
    row=flow.run(101,'owned',selection,'unique-fresh-request')
    assert sources.calls[:3]==['admit','collect','extract/retrieve']
    assert [b['model'] for b in calls]==['gpt-6.1-sol']*8+['gpt-6.1-terra']
    assert all(b['reasoning']=={'effort':'medium'} and b['max_output_tokens']==100000 for b in calls)
    assert row['state']=='AWAITING_OWNER_APPROVAL' and store.finished
    assert 'max-width:720px' in row['html'] and TEXT in row['plain']
    assert '1' in row['subject'] and store.stages['TERRA']['customer_publishable'] is False
    value=store.stages['TERRA']['canonical_report']['canonical_input']
    assert len(value['findings'])==8 and all(f['validated_evidence'] for f in value['findings'])
    assert all(f['luna_execution_mode']=='MOCK_ONLY' for f in value['findings'])
    assert store.stages['CORPUS_COLLECTED']['collection_period']==['2026-11-04T08:00:00+00:00','2026-11-04T08:00:10+00:00']
    assert flow.run(101,'owned',selection,'unique-fresh-request')==row and len(calls)==9

@pytest.mark.parametrize('fault,count',[('admission',0),('essential',0),('changed',0),('http-failure',1),('bad-citation',1),('incomplete',1),('terra-omission',9)])
def test_failures_never_prepare_email_or_fallback(fault,count):
    flow,store,sources,calls=run(fault)
    with pytest.raises(Exception):flow.run(101,'owned',{'parent':1,'lots':[2]},'unique-fresh-request')
    assert len(calls)==count and not store.finished
    if store.row:assert store.row['state']=='FAILED'
    if fault in {'bad-citation','incomplete'}:assert store.stages['SOL_GENERAL_PROCUREMENT']['state']=='RESPONSE_RECEIVED_NOT_VALIDATED'
    if fault=='terra-omission':assert store.stages['TERRA']['state']=='RESPONSE_RECEIVED_NOT_VALIDATED'

def test_paid_path_remains_closed_without_environment_key_read(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-be-read')
    provider=RuntimeProvider()
    p=Sources().index.packet('TECHNICAL_SPECIFICATIONS')
    with pytest.raises(NotSent,match='DISABLED'):provider.respond(r.sol_request(p))
    assert provider.settings.api_key is None

@pytest.mark.parametrize('state',['UNVERIFIED','ACTIVE_AS_PUBLISHED','SUSPENDED','EXPIRED'])
def test_real_snapshot_adapter_never_promotes_published_status_to_open(state):
    from ai_tender_intelligence.analysis.sources import FreshSources
    from datetime import datetime,timezone
    lm=SimpleNamespace(lot_id='LOT-2',deadline_instant='2030-01-01T00:00:00+00:00')
    op=SimpleNamespace(record_id='record',procedure_id='procedure',lots=[lm])
    stored=SimpleNamespace(id='snapshot',result=SimpleNamespace(matches=SimpleNamespace(opportunities=[op]),
        assessments=[SimpleNamespace(procedure_id='procedure',source_lot_id='LOT-2',current_openness=state,exclude=False,facts=[SimpleNamespace(sha256='a'*64)])]),
        source_payloads={'record':{'official_url':'https://app.eop.bg/today/1','lots':[{'id':'LOT-2','source_fields':{'tenderId':2}}]}})
    results=SimpleNamespace(read=lambda *a:stored)
    source=FreshSources(results,SimpleNamespace(db=None),None,clock=lambda:datetime(2026,11,4,tzinfo=timezone.utc))
    with pytest.raises(NotSent,match='OPEN_ADMISSION_NOT_VERIFIED'):
        source.admit(101,'owned',{'search_id':'snapshot','record_id':'record','parent':1,'lots':[2]})

def test_source_adapter_uses_existing_collector_and_local_only_fence(monkeypatch):
    from ai_tender_intelligence.analysis.sources import FreshSources
    fake=Sources();evidence=SimpleNamespace(db=None,authorize=lambda *a:None,manifest=lambda *a:fake.manifest)
    source=FreshSources(None,evidence,None)
    calls=[]
    source.collector=SimpleNamespace(run=lambda *a,**kw:(calls.append((a,kw)) or {'status':'ACQUIRED','collection':'new-id'}))
    assert source.collect(101,'owned',{'parent':1,'lots':[2]},'unique-request')=='new-id'
    assert calls[0][0]==(101,'owned',1,(2,)) and calls[0][1]['max_downloads']==100
    source.prepare=lambda *a:(fake.a,fake.index,fake.manifest)
    source.fence(101,'owned','new-id',fake.a,fake.index.packets(),fake.manifest)
    # There is no source status/deadline check after acceptance.
    assert not hasattr(source,'refresh_deadline')

def test_luna_classification_and_gmail_release_are_unchanged():
    from ai_tender_intelligence.classification.contract import MODEL,EFFORT
    from ai_tender_intelligence.classification.openai_provider import LIVE_DISPATCH_RELEASED
    from ai_tender_intelligence.demo41 import mail
    from ai_tender_intelligence.analysis import provider
    assert (MODEL,EFFORT)==('gpt-6-luna','high')
    assert not LIVE_DISPATCH_RELEASED and mail.REVIEWED_SEND_DIGEST is None
    assert provider.REVIEWED_RUNTIME_REQUEST is None