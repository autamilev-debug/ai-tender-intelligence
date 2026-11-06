"""Generic Beta semantic trade-off; technical boundaries still fail closed."""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import SMTP
from hashlib import sha256
import json
from pathlib import Path
import pytest
from ai_tender_intelligence.classification.contract import Reply
from ai_tender_intelligence.extraction.models import canonical
from ai_tender_intelligence.reporting import beta65,finalize62,runtime44
from ai_tender_intelligence.semantic.contract import SemanticError
from ai_tender_intelligence.demo41 import mail

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def generic():
    from helpers import run
    flow,store,_,_=run(internal_beta=True)
    flow.run(101,'owned',{'parent':1,'lots':[2]},'beta65-generic-original')
    report=store.stages['TERRA']['canonical_report']
    return report['canonical_input'],{k:report[k] for k in beta65.strict.Synthesis.model_fields}


@pytest.mark.parametrize('fault',['number','unit','vat','timing','condition','coverage','applicability','completeness','wording','known_reference'])
def test_generic_semantic_errors_are_nonblocking(generic,fault):
    inputs,original=generic;data=deepcopy(original)
    c=next(c for s in data['sections'] for p in s['paragraphs'] for c in p['contributions'])
    additions={'number':' Стойността е 999999 евро.','unit':' Срокът е 40 месеца вместо дни.',
        'vat':' Цената е с ДДС.','timing':' Плащането е преди подписване на договора.',
        'condition':' Условията са безусловни.','applicability':' Компанията отговаря на условията. Приложимостта е потвърдена.',
        'completeness':' Докладът е пълен. Всички съществени изисквания са проверени.',
        'wording':' This is an inaccurate Beta analysis.'}
    if fault=='coverage':next(s for s in data['sections'] if s['paragraphs'])['paragraphs'].clear()
    elif fault=='known_reference':
        c['finding_ids']=[next(f['qualified_finding_id'] for f in inputs['findings'] if f['qualified_finding_id'] not in c['finding_ids'])]
        c['text_bg']+=' Сумата е 999999 евро.'
    else:c['text_bg']+=additions[fault]
    report,message,audit=finalize62.prepare_email(Reply(canonical(data)),inputs,'offline65-generic',
        execution_mode='MOCK_ONLY',internal_beta=True,fence=lambda:None)
    assert {k:report[k] for k in beta65.strict.Synthesis.model_fields}==data
    assert audit['semantic_policy']=='DIAGNOSTIC_ONLY_NONBLOCKING' and message['mime']


def test_renderer_never_runs_blocking_semantic_validation_for_beta(generic,monkeypatch):
    inputs,data=generic
    monkeypatch.setattr(beta65.strict,'validate',lambda *a:pytest.fail('Beta must not call strict validate'))
    def unavailable(*a):raise SemanticError('SYNTHETIC_SEMANTIC_DIAGNOSTIC')
    monkeypatch.setattr(beta65.strict,'audit',unavailable)
    monkeypatch.setattr(beta65,'report_guard',unavailable)
    _,message,audit=finalize62.prepare_email(Reply(canonical(data)),inputs,'offline65-check',
        execution_mode='MOCK_ONLY',internal_beta=True,fence=lambda:None)
    assert len(audit['diagnostics'])==2 and message['mime']


@pytest.mark.parametrize('fault',['malformed','duplicate_json','unknown_reference','wrong_input','changed_corpus','empty','incomplete','invalid_beta'])
def test_technical_failures_remain_blocking(generic,fault):
    inputs,data=deepcopy(generic);raw=canonical(data);state='complete'
    if fault=='malformed':raw=b'not JSON'
    if fault=='duplicate_json':raw=b'{"model":"wrong",'+raw[1:]
    if fault=='unknown_reference':
        next(c for s in data['sections'] for p in s['paragraphs'] for c in p['contributions'])['finding_ids']=['unbound-source']
        raw=canonical(data)
    if fault=='wrong_input':data['input_digest']='0'*64;raw=canonical(data)
    if fault=='changed_corpus':inputs['identity']['parent_tender']=999
    if fault=='empty':
        for s in data['sections']:s['paragraphs']=[]
        raw=canonical(data)
    if fault=='incomplete':state='incomplete'
    if fault=='invalid_beta':inputs['beta59']['customer_publication_allowed']=True
    with pytest.raises(SemanticError):finalize62.validate_report(Reply(raw,state),inputs,internal_beta=True)


def test_strict_mode_still_blocks_semantic_errors(generic):
    inputs,data=deepcopy(generic)
    next(c for s in data['sections'] for p in s['paragraphs'] for c in p['contributions'])['text_bg']+=' Стойността е 999999 евро.'
    with pytest.raises(SemanticError):finalize62.validate_report(Reply(canonical(data)),inputs,internal_beta=False)


def test_source_fence_and_actual_preparation_failures_still_block(generic,monkeypatch):
    inputs,data=generic
    def denied():raise ValueError('SOURCE_ACCESS_OR_INTEGRITY_FAILED')
    with pytest.raises(ValueError,match='SOURCE_ACCESS_OR_INTEGRITY_FAILED'):
        finalize62.prepare_email(Reply(canonical(data)),inputs,'offline65-fence',execution_mode='MOCK_ONLY',internal_beta=True,fence=denied)
    def broken(*a,**kw):raise ValueError('MIME_PREPARATION_FAILED')
    monkeypatch.setattr(mail,'prepare',broken)
    with pytest.raises(ValueError,match='MIME_PREPARATION_FAILED'):
        finalize62.prepare_email(Reply(canonical(data)),inputs,'offline65-mime',execution_mode='MOCK_ONLY',internal_beta=True,fence=lambda:None)

