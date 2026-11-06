"""Strict, source-preserving initial implementation of the reporting v2 contract."""
from ai_tender_intelligence.assets import root as asset_root
import json
from hashlib import sha256
from pathlib import Path
from typing import Literal
from pydantic import Field
from ..semantic.contract import Strict,SemanticError
from ..extraction.models import canonical

MODEL='gpt-6.1-sol'; EFFORT='medium'; VERSION='tender-report-synthesis-v2'
PROMPT_VERSION='sol-tender-report-v1'
PROMPT_FILE=asset_root()/'prompts/sol_tender_report.md'
PROMPT_SHA256='47d00c893846f7a7eb449dcddf7e60f739e52bffd1c3d3416839a03dc13302fa'
SECTION_TITLES=(
 ('GENERAL_PROCUREMENT','Обща информация за поръчката'),
 ('TECHNICAL_SPECIFICATIONS','Предмет и технически изисквания'),
 ('PARTICIPATION_QUALIFICATION','Изисквания към участниците'),
 ('SUBMISSION_DEADLINES','Срокове и подаване на оферта'),
 ('EVALUATION_METHODOLOGY','Критерии за оценяване'),
 ('FINANCIAL_CONDITIONS','Финансови условия и гаранции'),
 ('CONTRACT_OBLIGATIONS','Договорни условия'),
 ('CLARIFICATIONS_AMENDMENTS','Разяснения и изменения'))

class Item(Strict):
    finding_id:str=Field(min_length=1,max_length=100)
    statement_bg:str=Field(min_length=1,max_length=1200)

class Section(Strict):
    category:Literal[tuple(k for k,_ in SECTION_TITLES)]
    items:list[Item]=Field(max_length=96)

class Synthesis(Strict):
    schema_revision:Literal['tender-report-synthesis-v2']
    input_digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    language:Literal['bg']
    model:Literal['gpt-6.1-sol']
    reasoning_effort:Literal['medium']
    sections:list[Section]=Field(min_length=8,max_length=8)

RESPONSE_SCHEMA=Synthesis.model_json_schema()

def instructions():
    raw=PROMPT_FILE.read_bytes()
    if len(raw)>16384 or sha256(raw).hexdigest()!=PROMPT_SHA256:raise SemanticError('SOL_PROMPT_HASH_MISMATCH')
    return raw.decode('utf-8')

def organization_input(inputs):
    """Lossless statement/uncertainty view bound to the full local evidence input.

    Quotes, source inventories and provenance are reattached from the original
    canonical record after exact-copy validation, never reconstructed by Sol.
    """
    from copy import deepcopy
    from ..semantic.retrieval import digest
    if inputs.get('input_digest')!=digest({k:v for k,v in inputs.items() if k!='input_digest'}):
        raise SemanticError('REPORT_INPUT_CHANGED')
    keys=('category','statement_bg','verification_state','interpretation_state',
          'applicability','applicable_lot_ids','requires_further_verification','uncertainty',
          'missing_evidence','contradictions','luna_execution_mode')
    findings=[{'finding_id':f['qualified_finding_id'],**{k:deepcopy(f[k]) for k in keys}} for f in inputs['findings']]
    ids=[f['finding_id'] for f in findings]
    if len(ids)!=len(set(ids)):raise SemanticError('SOL_DUPLICATE_INPUT_FINDING')
    if any(f['category'] not in dict(SECTION_TITLES) for f in findings):raise SemanticError('SOL_UNKNOWN_INPUT_CATEGORY')
    return {'organization_input_revision':'sol-organization-view-v1','schema_revision':inputs['schema_revision'],
        'input_digest':inputs['input_digest'],'language':inputs['language'],'purpose':inputs['purpose'],
        'identity':deepcopy(inputs['identity']),'scope':deepcopy(inputs['scope']),
        'findings':findings,'limitations':deepcopy(inputs['limitations']),
        'evidence_attachment':'FULL_ORIGINAL_EVIDENCE_RETAINED_LOCALLY_REATTACHED_AFTER_EXACT_COPY_VALIDATION',
        'customer_publication_allowed':False,'ai_ready':False,'corpus_frozen':False}

def request(inputs):
    body={'model':MODEL,'reasoning':{'effort':EFFORT},'store':False,'service_tier':'default',
          'instructions':instructions(),'input':canonical(organization_input(inputs)).decode('utf-8'),
          'text':{'format':{'type':'json_schema','name':'tender_report_synthesis_v2','strict':True,'schema':RESPONSE_SCHEMA}}}
    if len(canonical(body))>524288:raise SemanticError('SOL_REQUEST_RESOURCE_BOUND')
    return body

def validate(reply,inputs):
    if reply.state!='complete':raise SemanticError('SOL_'+reply.state.upper())
    if not isinstance(reply.body,bytes) or len(reply.body)>262144:raise SemanticError('SOL_RESPONSE_RESOURCE_BOUND')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate')
            result[k]=v
        return result
    try:data=Synthesis.model_validate(json.loads(reply.body,object_pairs_hook=pairs),strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError):raise SemanticError('INVALID_SOL_SCHEMA') from None
    if data['input_digest']!=inputs['input_digest']:raise SemanticError('SOL_STALE_INPUT')
    if [s['category'] for s in data['sections']]!=[k for k,_ in SECTION_TITLES]:raise SemanticError('SOL_SECTION_STRUCTURE')
    expected={f['qualified_finding_id']:f for f in inputs['findings']};seen=set()
    for section in data['sections']:
        for item in section['items']:
            f=expected.get(item['finding_id'])
            if f is None or item['finding_id'] in seen:raise SemanticError('SOL_UNKNOWN_OR_DUPLICATE_FINDING')
            if f['category']!=section['category'] or item['statement_bg']!=f['statement_bg']:
                raise SemanticError('SOL_UNSUPPORTED_REWRITE_OR_SCOPE')
            seen.add(item['finding_id'])
    if seen!=set(expected):raise SemanticError('SOL_FINDINGS_OMITTED')
    return data

def mock_reply(inputs):
    """Explicit mocked organization of actual validated statements, not Sol performance."""
    from ..classification.contract import Reply
    return Reply(canonical({'schema_revision':VERSION,'language':'bg','input_digest':inputs['input_digest'],
                'model':MODEL,'reasoning_effort':EFFORT,'sections':[
                    {'category':key,'items':[{'finding_id':f.get('qualified_finding_id') or f['finding_id'],'statement_bg':f['statement_bg']}
                     for f in inputs['findings'] if f['category']==key]} for key,_ in SECTION_TITLES]}))
