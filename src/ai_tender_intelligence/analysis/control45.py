"""Owner/job-scoped finite execution. Spending is controlled by OpenAI Platform."""
from ai_tender_intelligence.assets import root as asset_root
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from sqlalchemy import select,text,update,func
from ..classification.contract import NotSent
from ..classification.openai_provider import _OneCallPermit
from ..demo41.store import RUNS
from ..semantic.retrieval import digest
from ..reporting import synthesis40
from ..assessment.candidates import LABELS

VERSION='fresh-sol-terra68-open-beta-v1'
OUTPUT_TOKENS=100000
SEQUENCE_LOCK=0x503541494A4F42


def recipe():
    root=asset_root()
    names=['analysis/runtime.py','analysis/provider.py','analysis/control45.py','analysis/sources.py',
           'analysis/admission45.py','semantic/shortrefs37.py','semantic/comparison32.py',
           'semantic/contract_v2.py','reporting/synthesis40.py','reporting/validation40_offline.py',
           'reporting/formats40.py','reporting/validation39.py','reporting/runtime44.py',
           'reporting/presentation52.py','demo41/mail.py','semantic/internal54.py',
           'semantic/validation.py','semantic/contract.py','semantic/references34.py',
           'semantic/completeness39.py','reporting/service.py','reporting/contract.py','semantic/processing58.py','semantic/beta59.py',
           'reporting/finalize62.py','reporting/beta65.py',
           'demo41/store.py','beta67/schema.py','beta67/store.py','beta67/service.py','beta67/delivery.py',
           'beta67/classification.py','beta67/telegram.py','beta67/__main__.py','persistence/identity.py','assets.py']
    result={n:sha256((Path(__file__).resolve().parents[1]/n).read_bytes()).hexdigest() for n in names}
    result['terra_prompt']=sha256(synthesis40.PROMPT.read_bytes()).hexdigest()
    for name in ('sol_internal54.md','terra_internal54.md','sol_processing58.md','terra_beta59.md'):
        result[name]=sha256((root/'prompts'/name).read_bytes()).hexdigest()
    return result


def plan(actor,company,job,collection,selection,manifest,packets,bodies,*,internal_beta=False):
    if type(internal_beta) is not bool:raise NotSent('BETA59_EXPLICIT_BOOLEAN_REQUIRED')
    entries=[]
    for packet,body in zip(packets,bodies):
        if body is None and (not internal_beta or packet['spans']):raise NotSent('BETA59_UNAPPROVED_EMPTY_SLOT')
        entry={'stage':'SOL_'+packet['category'],'model':'gpt-6.1-sol','request_digest':digest(body) if body is not None else None,'max_output_tokens':OUTPUT_TOKENS}
        if body is None:entry['skip_reason']='NO_RETRIEVABLE_EVIDENCE'
        entries.append(entry)
    entries.append({'stage':'TERRA','model':'gpt-6.1-terra','request_digest':None,'max_output_tokens':OUTPUT_TOKENS})
    return {'revision':VERSION,'internal_beta':internal_beta,'actor':actor,'company':str(company),'job':str(job),'collection':str(collection),
            'selection':deepcopy(selection),'manifest_digest':digest(manifest),'requests':entries,
            'maximum_requests':9,'automatic_retry':False,'fallback':False,'gmail_allowed':False,
            'recipe':recipe(),'authorization_status':'AWAITING_OWNER_JOB_AUTHORIZATION',
            'spending_control':'OPENAI_PLATFORM_ONLY',
            'conditional_terra':'ACTUAL_VALIDATED_SOL_FINDINGS_AND_PINNED_RECIPE'}


def validate_approval(p,approval):
    if (p.get('revision')!=VERSION or p.get('recipe')!=recipe() or type(p.get('internal_beta',False)) is not bool
        or type(p.get('maximum_requests')) is not int or p['maximum_requests']!=9
        or len(p.get('requests',[]))!=9 or approval.get('explicit_owner_approval') is not True
        or approval.get('actor')!=p['actor'] or approval.get('company')!=p['company'] or approval.get('job')!=p['job']
        or type(approval.get('maximum_requests')) is not int or approval['maximum_requests']!=9
        or approval.get('gmail_allowed') is not False
        or approval.get('plan_digest',digest(p))!=digest(p)):
        raise NotSent('RUNTIME46_EXACT_OWNER_JOB_AUTHORIZATION_REQUIRED')
    if ([e['stage'] for e in p['requests']]!=['SOL_'+c for c in LABELS]+['TERRA']
        or [e['model'] for e in p['requests']]!=['gpt-6.1-sol']*8+['gpt-6.1-terra']
        or any(type(e.get('max_output_tokens')) is not int or e['max_output_tokens']!=OUTPUT_TOKENS for e in p['requests'])):
        raise NotSent('RUNTIME46_SEQUENCE_OR_TOKEN_CONFIGURATION_CHANGED')
    try:
        expires=datetime.fromisoformat(approval['expires_at'])
        if expires.tzinfo is None or expires<=datetime.now(timezone.utc):raise ValueError()
    except Exception:raise NotSent('RUNTIME46_OWNER_AUTHORIZATION_EXPIRED') from None
    return expires


class Release45:
    """Name retained for existing adapters; new plans use the P54 internal contract."""
    def __init__(self,store,actor,company,job,p,approval):
        if p.get('actor')!=actor or p.get('company')!=str(company) or p.get('job')!=str(job):
            raise NotSent('RUNTIME46_OWNER_JOB_MISMATCH')
        self.store,self.actor,self.company,self.job=store,actor,company,job
        self.plan=deepcopy(p);self.expires=validate_approval(p,approval)
        self.next=0;self.closed=False;self.started=False

    def start(self,*,preflight_only=False):
        from ..classification import openai_provider
        if openai_provider.LIVE_DISPATCH_RELEASED:raise NotSent('RUNTIME46_GENERAL_PROVIDER_MUST_REMAIN_DISABLED')
        with self.store.access(self.actor,self.company) as (s,c):
            row=self.store._row(s,c,self.actor,self.job)
            if (row['evidence'].get('AI_PLAN45',{}).get('plan')!=self.plan or row['state']!='PREPARING'
                or any(k in row['evidence'] for k in ['RUN_STARTED45','RUN_STARTED46'])):
                raise NotSent('RUNTIME46_ATTEMPT_SPENT_OR_JOB_CHANGED')
            s.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':SEQUENCE_LOCK})
            table=getattr(self.store,'table',RUNS)
            for other in {table}:
                if s.scalar(select(func.count()).select_from(other).where(other.c.id!=row['id'],other.c.state=='PREPARING',
                    other.c.evidence.has_key('RUN_STARTED45') | other.c.evidence.has_key('RUN_STARTED46'))):
                    raise NotSent('RUNTIME46_OTHER_RUN_IN_FLIGHT_OR_UNCERTAIN')
            evidence={**row['evidence'],'RUN_STARTED46':{'job_digest':digest(self.plan),
                'started_at':datetime.now(timezone.utc).isoformat(),'replay_allowed':False,'maximum_requests':9}}
            if not preflight_only:s.execute(update(table).where(table.c.id==row['id']).values(evidence=evidence))
        if not preflight_only:self.started=True

    def request(self,body):
        if not self.started or self.closed or self.expires<=datetime.now(timezone.utc) or self.next>=9:
            raise NotSent('RUNTIME46_RELEASE_CLOSED')
        entry=self.plan['requests'][self.next]
        if body.get('model')!=entry['model'] or body.get('reasoning')!={'effort':'medium'} or body.get('max_output_tokens')!=OUTPUT_TOKENS:
            raise NotSent('RUNTIME46_MODEL_ORDER_OR_TOKEN_MISMATCH')
        if self.next<8 and digest(body)!=entry['request_digest']:raise NotSent('RUNTIME46_REQUEST_NOT_AUTHORIZED')
        if self.next==8:
            from .runtime import terra_request
            inputs=self.store.read(self.actor,self.company,self.job)['evidence'].get('TERRA',{}).get('canonical_input')
            if self.plan['recipe']!=recipe() or not inputs or terra_request(inputs)!=body:
                raise NotSent('RUNTIME46_CONDITIONAL_INPUT_NOT_CANONICAL')
            self.store.stage(self.actor,self.company,self.job,'CONDITIONAL_TERRA_PIN45',{
                'request_digest':digest(body),'input_digest':__import__('json').loads(body['input'])['input_digest']})
        self.store.stage(self.actor,self.company,self.job,'DISPATCH45_'+str(self.next),{
            'state':'RUN_STARTED_NO_REPLAY','request_digest':digest(body),'max_output_tokens':OUTPUT_TOKENS,'request':deepcopy(body)})
        self.next+=1
        return body,_OneCallPermit(digest(body),expires_at=self.expires.timestamp())

    def skip_empty_category(self,category):
        if not self.started or self.closed or self.expires<=datetime.now(timezone.utc) or self.next>=8:
            raise NotSent('RUNTIME46_RELEASE_CLOSED')
        entry=self.plan['requests'][self.next]
        if (self.plan.get('internal_beta') is not True or entry['stage']!='SOL_'+category
            or entry.get('skip_reason')!='NO_RETRIEVABLE_EVIDENCE' or entry['request_digest'] is not None):
            raise NotSent('BETA59_EMPTY_SLOT_NOT_AUTHORIZED')
        self.store.stage(self.actor,self.company,self.job,'SKIP59_'+str(self.next),{'stage':entry['stage'],
            'reason':entry['skip_reason'],'paid_dispatch':False})
        self.next+=1  # Advance only the reviewed slot; never grant another call.

    def observe(self,reply,observation):
        entry=self.plan['requests'][self.next-1];i,o=reply.input_tokens,reply.output_tokens;estimate=None
        if type(i) is int and type(o) is int:
            estimate=str((Decimal(i)*Decimal('2.50' if entry['model']=='gpt-6.1-sol' else '1.25')+
                          Decimal(o)*Decimal('10' if entry['model']=='gpt-6.1-sol' else '5'))/1000000)
        self.store.stage(self.actor,self.company,self.job,'USAGE45_'+str(self.next-1),{
            'input_tokens':i,'output_tokens':o,'observation':deepcopy(observation),'estimated_usd':estimate,
            'cost_status':'OBSERVATION_ONLY_NOT_INVOICE_OR_EXECUTION_GATE','invoice_cost':None})

    def close(self):self.closed=True
