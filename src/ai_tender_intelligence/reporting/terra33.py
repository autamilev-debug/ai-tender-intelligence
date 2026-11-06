"""Experimental paraphrase contract. Structural checks never certify entailment."""
from ai_tender_intelligence.assets import root as asset_root
import json,re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal
from pydantic import Field
from ..semantic.contract import Strict,SemanticError
from ..semantic.retrieval import digest
from ..semantic.provider import SemanticResponsesProvider
from ..classification.contract import NotSent
from ..classification.openai_provider import normalize_response,UncertainDispatch
from ..extraction.models import canonical
from .contract import organization_input

MODEL='gpt-6.1-terra';EFFORT='medium';VERSION='terra-report-paraphrase-v1'
PROMPT_FILE=asset_root()/'prompts/terra_report_v1.md'
PROMPT_SHA256='42a77d1a2b1360ce6d1cfe6decf348b90267d103439e1d01addf97f9bebc28eb'
SECTIONS=(('OVERVIEW','Кратък преглед'),('TECHNICAL','Технически изисквания'),
 ('SUBMISSION','Участие и документи'),('FINANCE_CONTRACT','Финансови и договорни условия'),
 ('EVALUATION_DATES','Оценяване и срокове'),('ATTENTION','Приоритети за проверка'),('SOURCES_LIMITATIONS','Източници и ограничения'))

class Paragraph(Strict):
    text_bg:str=Field(min_length=1,max_length=5000)
    finding_ids:list[str]=Field(min_length=1,max_length=96)

class Section(Strict):
    key:Literal[tuple(k for k,_ in SECTIONS)]
    paragraphs:list[Paragraph]=Field(max_length=96)

class Synthesis(Strict):
    schema_revision:Literal['terra-report-paraphrase-v1']
    input_digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    model:Literal['gpt-6.1-terra']
    reasoning_effort:Literal['medium']
    language:Literal['bg']
    sections:list[Section]=Field(min_length=7,max_length=7)

def numbers(text):
    # Lexical number preservation only: normalization does not establish meaning,
    # units, timing, negation or correct assignment to a fact.
    return {m.replace(',','.').lstrip('0') or '0' for m in re.findall(r'(?<!\d)\d+(?:[.,]\d+)*(?!\d)',text)}

QUALIFIERS=(
 (r'минимум|най-малко',r'минимум|най-малко|поне|≥','MINIMUM'),
 (r'не\s+повече|максимум|до\s+\d',r'не\s+повече|максимум|най-много|до\s+\d|≤','MAXIMUM'),
 (r'освен|изключени|изключения',r'освен|изключ','EXCEPTION'),
 (r'ако|в\s+случай',r'ако|случай|когато|при\s','CONDITIONAL'),
 (r'без\s+ДДС',r'без\s+ДДС','VAT_BASIS'),
 (r'еквивалент',r'еквивалент','EQUIVALENCE'),
 (r'регистриран',r'регист','REGISTRATION_ANCHOR'),
 (r'декларация\s+от\s+производителя',r'деклараци\w*[^.!?]{0,35}производител','MANUFACTURER_DECLARATION'),
)

def instructions():
    raw=PROMPT_FILE.read_bytes()
    if sha256(raw).hexdigest()!=PROMPT_SHA256:raise SemanticError('TERRA_PROMPT_CHANGED')
    return raw.decode('utf-8')

def attention(inputs):
    items=[]
    labels={'TECHNICAL_PROPOSAL':'техническо предложение','SUPPORTING_MATERIALS':'придружаващи технически материали',
        'PAGE_REFERENCES':'препратки към страници','LANGUAGE':'езикови условия','PRICE_RESTRICTION':'ограничения за цени',
        'ENVIRONMENTAL_EVIDENCE':'екологични доказателства','DELIVERY_DECLARATION':'срок и образец за доставка',
        'DECLARATIONS':'декларации','SUBMISSION_DATES':'подаване на оферти','OPENING_DATES':'отваряне на оферти',
        'OFFICIAL_TIMESTAMPS':'официални времеви данни','NOTIFICATION_PROCESS':'уведомяване и връчване'}
    for check in inputs.get('submission_coverage_reviews',[]):
        for gap in check['gaps']:
            items.append({'kind':'INTERPRETATION_GAP','priority':1,'text_bg':'Наличният откъс не е достатъчно отразен в констатациите: '+labels[gap['topic']],
                'source_reference':gap,'action':'TARGETED_SOURCE_CLAUSE_REVIEW_NOT_FULL_DOCUMENT_REREAD'})
    if any(f['applicability']=='UNRESOLVED' for f in inputs['findings']):
        items.append({'kind':'APPLICABILITY','priority':1,'text_bg':'Правната приложимост към избраната позиция не е установена. Документалните наблюдения не са потвърдени задължения за нея.',
            'action':'RESOLVE_SELECTED_LOT_APPLICABILITY'})
    forms=[f['qualified_finding_id'] for f in inputs['findings'] if f['category'] in {'PARTICIPATION_QUALIFICATION','SUBMISSION_DEADLINES'} and re.search(r'формуляр|ЕЕДОП|деклараци|образец|предложение',f['statement_bg'],re.I)]
    if forms:items.append({'kind':'BID_PREPARATION','priority':2,'text_bg':'Проверете относимите формуляри, посочени в констатациите, след установяване на приложимостта; наличен формуляр не доказва анализ на всички негови условия.',
        'finding_ids':forms,'action':'CONDITIONAL_FORM_PREPARATION_NOT_NEW_OBLIGATION'})
    missing=[d for d in inputs.get('document_disclosures',[]) if d.get('kind')=='NOT_ACQUIRED']
    if missing:items.append({'kind':'EVIDENCE_UNKNOWN','priority':2,'text_bg':'Има неполучени източници с неустановена същественост. Не е установен брой задължителни пълни клиентски препрочитания.',
        'action':'ESTABLISH_ESSENTIALITY_NOT_ASSUMED_MISSING_REQUIREMENT'})
    items.append({'kind':'PROCESSING_LIMITATION','priority':3,'text_bg':'Оценени са избрани откъси. Подробният обхват и ограниченията са запазени в приложението; редовете за преглед не са списък със задължителни препрочитания.',
        'action':'INFORMATIONAL_NOT_ROUTINE_CUSTOMER_TASK'})
    return sorted(items,key=lambda i:i['priority'])

def compact(inputs):
    base=organization_input(inputs)
    identifiers={str(inputs['identity']['parent_tender']),*(str(i) for i in inputs['scope']['selected_lot_ids'])}
    for f in base['findings']:
        f['numeric_tokens']=sorted(numbers(f['statement_bg'])-identifiers)
        f['qualifier_checks']=[name for source,_,name in QUALIFIERS if re.search(source,f['statement_bg'],re.I)]
    base.update(schema_revision=VERSION,attention_records=attention(inputs),
        paraphrase_validation='STRUCTURAL_AND_LEXICAL_ONLY_REQUIRES_SEMANTIC_REVIEW')
    return base

def request(inputs):
    body={'model':MODEL,'reasoning':{'effort':EFFORT},'store':False,'service_tier':'default',
        'instructions':instructions(),'input':canonical(compact(inputs)).decode('utf-8'),
        'text':{'format':{'type':'json_schema','name':'terra_report_v1','strict':True,'schema':Synthesis.model_json_schema()}}}
    if len(canonical(body))>524288:raise SemanticError('TERRA_REQUEST_RESOURCE_BOUND')
    return body

def validate(reply,inputs):
    if reply.state!='complete':raise SemanticError('TERRA_RESPONSE_NOT_COMPLETE')
    if not isinstance(reply.body,bytes) or len(reply.body)>262144:raise SemanticError('TERRA_RESPONSE_RESOURCE_BOUND')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('duplicate')
            result[k]=v
        return result
    try:data=Synthesis.model_validate(json.loads(reply.body,object_pairs_hook=pairs),strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError):raise SemanticError('TERRA_SCHEMA_INVALID') from None
    compact(inputs)  # Reverify canonical local record, not a mutable caller flag.
    if data['input_digest']!=inputs['input_digest']:raise SemanticError('TERRA_STALE_INPUT')
    if [s['key'] for s in data['sections']]!=[s[0] for s in SECTIONS]:raise SemanticError('TERRA_SECTION_STRUCTURE')
    by_id={f['qualified_finding_id']:f for f in inputs['findings']};represented={k:[] for k in by_id}
    identifiers={str(inputs['identity']['parent_tender']),*(str(i) for i in inputs['scope']['selected_lot_ids'])}
    for s in data['sections']:
        for p in s['paragraphs']:
            ids=p['finding_ids'];text=p['text_bg']
            if len(ids)!=len(set(ids)) or not set(ids)<=set(by_id):raise SemanticError('TERRA_UNKNOWN_OR_DUPLICATE_FINDING')
            if not re.search(r'[\u0400-\u04ff]',text):raise SemanticError('TERRA_BULGARIAN_REQUIRED')
            if re.search(r'https?://|<[^>]+>|\bSHA-?256\b',text,re.I):raise SemanticError('TERRA_GENERATED_SOURCE_OR_MARKUP')
            if re.search(r'гарантира\w*\s+(?:участие|допускане|пълнота)|компанията\s+отговаря|потвърдена\s+приложимост|готов\s+за\s+публикуване|потвърдено\s+задължение|за\s+избраната\s+позиция\s+се\s+изисква',text,re.I):
                raise SemanticError('TERRA_UNSUPPORTED_CUSTOMER_CLAIM')
            allowed=set().union(*(numbers(by_id[i]['statement_bg']) for i in ids))
            if not numbers(text)<=allowed:raise SemanticError('TERRA_UNSUPPORTED_NUMERICAL_VALUE')
            for i in ids:represented[i].append(text)
    if any(not value for value in represented.values()):raise SemanticError('TERRA_FINDING_OMITTED')
    for fid,texts in represented.items():
        if not numbers(by_id[fid]['statement_bg'])-identifiers<=numbers(' '.join(texts)):raise SemanticError('TERRA_NUMERICAL_VALUE_OMITTED')
        source=by_id[fid]['statement_bg']
        for trigger,required,name in QUALIFIERS:
            if re.search(trigger,source,re.I) and not re.search(required,' '.join(texts),re.I):
                raise SemanticError('TERRA_MATERIAL_QUALIFIER_OMITTED_'+name)
    return data

def assemble(inputs,reply,*,mode,observation=None):
    if mode not in {'MOCK_ONLY','LIVE'}:raise SemanticError('TERRA_EXECUTION_MODE')
    data=validate(reply,inputs);original={f['qualified_finding_id']:f for f in inputs['findings']}
    return {**data,'execution_mode':mode,'observation':observation or {},'canonical_findings':deepcopy(original),
        'attention_records':attention(inputs),'source_provenance':deepcopy(inputs['content_provenance']),
        'document_coverage':deepcopy(inputs.get('document_coverage')),'limitations':deepcopy(inputs['limitations']),
        'publishable':False,'ai_ready':False,'corpus_frozen':False,'semantic_review':'REQUIRED_NOT_DETERMINISTICALLY_CERTIFIED'}

@dataclass(frozen=True)
class ReviewedTerraRelease:
    plan_digest:str
    request_digest:str
    input_digest:str
    owner_reference:str
    expires_at:datetime

REVIEWED_TERRA_RELEASE=None

def require_release(plan,grant,inputs):
    if grant is None or type(grant) is not ReviewedTerraRelease or grant is not REVIEWED_TERRA_RELEASE:raise NotSent('TERRA_RELEASE_CLOSED')
    if (grant.plan_digest!=digest(plan) or grant.request_digest!=digest(request(inputs)) or grant.input_digest!=inputs['input_digest']
        or not grant.owner_reference or grant.expires_at.tzinfo is None or grant.expires_at<=datetime.now(timezone.utc)
        or len(plan.get('requests',[]))!=1 or plan['requests'][0]['request_digest']!=grant.request_digest
        or any(f['luna_execution_mode']!='LIVE' for f in inputs['findings'])):raise NotSent('TERRA_RELEASE_SCOPE_INVALID')

class TerraProvider(SemanticResponsesProvider):
    model=MODEL;reasoning_effort=EFFORT
    def __init__(self,*args,plan=None,grant=None,**kwargs):
        super().__init__(*args,**kwargs);self.plan=plan;self.grant=grant
    def _request(self,inputs):return request(inputs)
    def respond(self,inputs):
        if not self.is_mock:
            require_release(self.plan,self.grant,inputs)
            if self._permit is None or not self._permit.active:raise NotSent('TERRA_DURABLE_PERMIT_REQUIRED')
        return super().respond(inputs)
    def _normalize_task_response(self,data):
        reply=normalize_response(data,max_bytes=262144,expected_model=MODEL)
        if data.get('service_tier')!='default':raise UncertainDispatch('UNEXPECTED_SERVICE_TIER')
        i,o=reply.input_tokens,reply.output_tokens;details=self.observation.get('input_tokens_details',{})
        cache,writes=details.get('cached_tokens'),details.get('cache_write_tokens')
        if i is not None and o is not None:
            complete=type(cache) is int and type(writes) is int and 0<=cache and 0<=writes and cache+writes<=i
            price=(Decimal(i-cache-writes)+Decimal(cache)*Decimal('.05')+Decimal(writes)*Decimal('1.25') if complete else Decimal(i)*Decimal('1.25'))+Decimal(o)*5
            self.observation['usage_cost']={'currency':'USD','estimated_cost':str(price/1000000),'actual_invoice_charge':None,
                'status':'SYNTHETIC_USAGE_NOT_ACTUAL_COST' if self.is_mock else 'ESTIMATE_NOT_INVOICE','pricing_checked':'2026-11-02'}
        return reply

def html(report):
    from html import escape
    from .render import DISCLAIMER,safe_url,stamp
    if report['publishable'] is not False:raise SemanticError('TERRA_PREVIEW_ONLY')
    h=['<!doctype html><html lang="bg"><meta charset="utf-8"><title>Развоен доклад</title><style>body{font:17px/1.6 system-ui;max-width:1050px;margin:30px auto;padding:20px;color:#172338}aside{background:#fff0ce;padding:20px}blockquote{white-space:pre-wrap}details{margin:12px 0}small{overflow-wrap:anywhere}</style>',
       '<h1>Анализ на поръчка — развоен преглед</h1><aside>НЕ Е ГОТОВ КЛИЕНТСКИ ДОКЛАД. '+escape(report['execution_mode'])+'. Правната приложимост и достатъчността не са установени. Перифразите изискват съдържателна проверка.</aside>']
    originals=report['canonical_findings']
    for s,(_,title) in zip(report['sections'],SECTIONS):
        h.append('<h2>'+title+'</h2>')
        for p in s['paragraphs']:
            h.append('<p>'+escape(p['text_bg'])+'</p><small>Основание: '+' '.join('<a href="#'+escape(i,quote=True)+'">['+str(list(originals).index(i)+1)+']</a>' for i in p['finding_ids'])+'</small>')
        if s['key']=='ATTENTION':
            for a in report['attention_records']:h.append('<p><strong>'+str(a['priority'])+'.</strong> '+escape(a['text_bg'])+'</p>')
    h.append('<h2>Подробни доказателства — приложение</h2><p>Цитатите са приложени от проверените оригинални записи; не са генерирани от Terra.</p>')
    for fid,f in originals.items():
        h.append('<details id="'+escape(fid,quote=True)+'"><summary>['+str(list(originals).index(fid)+1)+'] '+escape(f['statement_bg'])+'</summary>')
        for e in f['validated_evidence']:
            url=safe_url(e['source'].get('official_reference'));name=escape(e['source']['filename'])
            if url:name='<a href="'+escape(url,quote=True)+'">'+name+'</a>'
            h.append('<blockquote>'+escape(e['quote'])+'</blockquote><small>'+name+';блок '+str(e['block_index'])+';ред '+str(e['table_row'])+';колона '+str(e['table_column'])+';SHA-256:'+escape(e['source_sha256'])+'</small>')
        h.append('</details>')
    h.append('<details><summary>Подробен обхват и ограничения на документите</summary><pre>'+escape(json.dumps(report['document_coverage'],ensure_ascii=False,indent=2))+'</pre></details>')
    for v in report['source_provenance'].get('collection_intervals',[]):h.append('<p>Събиране:'+escape(stamp(v.get('start')))+' — '+escape(stamp(v.get('end')))+'</p>')
    h.append('<p>'+escape(DISCLAIMER)+'</p></html>');return '\n'.join(h)
