"""Foreground integration, existing owner-bound store/checkpoints and validators.

No historical findings, replies, tender identities or experimental release imports.
"""
from copy import deepcopy
from ..classification.contract import Reply,NotSent
from ..extraction.models import canonical
from ..semantic import shortrefs37,comparison32,contract_v2
from ..semantic.validation import validate
from ..semantic.retrieval import digest
from ..semantic.completeness39 import require_complete
from ..assessment.candidates import LABELS
from ..reporting.service import build_input
from ..reporting import synthesis40
from .provider import RuntimeProvider
from ..semantic import internal54
from ..semantic import processing58
from ..semantic import beta59
from ..reporting.finalize62 import prepare_email


def sol_request(packet,documents=(),*,internal_beta=False):
    if internal_beta and not packet['spans']:return None
    body=processing58.request(packet,documents)
    return {**body,'max_output_tokens':100000}


def terra_request(inputs):
    body={**synthesis40.request(inputs),'max_output_tokens':100000}
    if inputs.get('internal_review54'):
        import json
        view=json.loads(body['input']);view['internal_review54']=inputs['internal_review54']
        if inputs.get('beta59'):view['beta59']=inputs['beta59']
        body['input']=canonical(view).decode()
        body['instructions']+='\n'+internal54.PROMPT_FILE.with_name('terra_internal54.md').read_text(encoding='utf-8')
        if inputs.get('beta59'):body['instructions']+='\n'+internal54.PROMPT_FILE.with_name('terra_beta59.md').read_text(encoding='utf-8')
        if len(canonical(body))>524288:raise NotSent('P40_REQUEST_RESOURCE_BOUND')
    return body


def sol_validate(reply,packet,mode):
    return processing58.validate(reply,packet,execution_mode=mode)

def require_evidence(assessment):
    if (assessment['outcome'] not in {'UNRESOLVED','SUFFICIENT_EVIDENCE'}
        or assessment.get('known_essential_missing_sources')
        or any(d.get('essentiality')=='ESSENTIAL' for d in assessment.get('unresolved_dependencies',[]))):
        raise NotSent('RUNTIME_ESSENTIAL_EVIDENCE_UNAVAILABLE')


class RuntimeWorkflow:
    def __init__(self,store,sources,provider,*,internal_beta=False):
        if type(provider) is not RuntimeProvider:raise NotSent('RUNTIME_PROVIDER_BOUNDARY')
        self.store,self.sources,self.provider=store,sources,provider
        if type(internal_beta) is not bool:raise NotSent('BETA59_EXPLICIT_BOOLEAN_REQUIRED')
        self.internal_beta=internal_beta

    def run(self,actor,company,selection,key):
        # No paid request, source collection or reservation before owned admission.
        accepted=self.sources.admit(actor,company,selection)
        if not isinstance(key,str) or not 16<=len(key)<=150:raise NotSent('RUNTIME_REQUEST_KEY')
        row,new=self.store.begin(actor,company,'analysis44:'+digest({'key':key,'selection':selection}))
        if not new:return row  # Failed/preparing/spent jobs never silently replay.
        job=row['id'];mode='MOCK_ONLY' if self.provider.is_mock else 'LIVE'
        try:
            self.store.stage(actor,company,job,'ADMITTED',accepted)
            collection=self.sources.collect(actor,company,selection,key)
            assessment,index,manifest=self.sources.prepare(actor,company,collection)
            require_evidence(assessment)
            packets=index.packets()
            if ([p['category'] for p in packets]!=list(LABELS)
                or not self.internal_beta and any(not p['spans'] for p in packets if p['category'] in shortrefs37.CATEGORIES)):
                raise NotSent('RUNTIME_REQUIRED_SECTION_EVIDENCE_UNAVAILABLE')
            self.sources.fence(actor,company,collection,assessment,packets,manifest)
            self.store.stage(actor,company,job,'CORPUS_COLLECTED',{
                'collection':str(collection),'assessment_id':assessment['assessment_id'],
                'packet_ids':[p['packet_id'] for p in packets],'collection_period':[
                    manifest['collection_started'],manifest['collection_finished']],
                'source_manifest_digest':digest(manifest),'publication_allowed':False})
            if not self.provider.is_mock:
                from .control45 import plan
                p=plan(actor,company,job,collection,selection,manifest,packets,[sol_request(packet,assessment['documents'],internal_beta=self.internal_beta) for packet in packets],internal_beta=self.internal_beta)
                self.store.stage(actor,company,job,'AI_PLAN45',{'state':'AWAITING_EXACT_OWNER_APPROVAL','plan':p,'plan_digest':digest(p)})
                return self.store.read(actor,company,job)
            return self._interpret(actor,company,job,collection,assessment,manifest,packets,mode)
        except BaseException:
            self.store.failed(actor,company,job)
            raise

    def execute_prepared(self,actor,company,job,approval):
        from .control45 import Release45
        row=self.store.read(actor,company,job);p=row['evidence'].get('AI_PLAN45',{}).get('plan')
        if not p:raise NotSent('RUNTIME45_PREPARED_PLAN_REQUIRED')
        if p.get('internal_beta',False)!=self.internal_beta:raise NotSent('BETA59_PLAN_MODE_MISMATCH')
        release=Release45(self.store,actor,company,job,p,approval)
        collection=p['collection'];assessment,index,manifest=self.sources.prepare(actor,company,collection);packets=index.packets()
        require_evidence(assessment)
        current_bodies=[sol_request(packet,assessment['documents'],internal_beta=self.internal_beta) for packet in packets]
        if (digest(manifest)!=p['manifest_digest'] or [digest(b) if b is not None else None for b in current_bodies]!=[e['request_digest'] for e in p['requests'][:-1]]):
            raise NotSent('RUNTIME45_FROZEN_INPUT_CHANGED')
        self.sources.fence(actor,company,collection,assessment,packets,manifest)
        release.start();self.provider.bind_release(release)
        try:return self._interpret(actor,company,job,collection,assessment,manifest,packets,'MOCK_ONLY' if self.provider.is_mock else 'LIVE')
        except BaseException:
            self.store.failed(actor,company,job);raise
        finally:
            release.close();self.provider.release45=None

    def _interpret(self,actor,company,job,collection,assessment,manifest,packets,mode):
        try:
            batches=[];beta_reviews=[]
            for packet in packets:
                body=sol_request(packet,assessment['documents'],internal_beta=self.internal_beta)
                if body is None:
                    if self.provider.release45:self.provider.release45.skip_empty_category(packet['category'])
                    beta_reviews.append({'category':packet['category'],'missing_category':True,'excluded_findings':[],
                        'warnings':[{'finding_id':None,'reason':'NO_RETRIEVABLE_EVIDENCE'}]})
                    self.sources.fence(actor,company,collection,assessment,packets,manifest)
                    self.store.stage(actor,company,job,'SOL_'+packet['category'],{'state':'INCOMPLETE_BETA_NO_EVIDENCE','paid_dispatch':False})
                    continue
                self.store.stage(actor,company,job,'SOL_'+packet['category'],
                    {'state':'REQUEST_RESERVED_NO_REPLAY','request_digest':digest(body),'model':body['model']})
                reply=self.provider.respond(body)
                self.store.stage(actor,company,job,'SOL_'+packet['category'],{
                    'state':'RESPONSE_RECEIVED_NOT_VALIDATED','request_digest':digest(body),
                    'response_state':reply.state,'raw_response':reply.body.decode('utf-8',errors='replace') if reply.body else None})
                if self.internal_beta:
                    review=beta59.review(reply,packet,execution_mode=mode)
                    batch=review['batch'];beta_reviews.append({k:v for k,v in review.items() if k!='batch'})
                else:
                    batch=sol_validate(reply,packet,mode)
                    require_complete(batch.data['findings'])
                self.sources.fence(actor,company,collection,assessment,packets,manifest)
                self.store.stage(actor,company,job,'SOL_'+packet['category'],{
                    'state':('INCOMPLETE_BETA' if self.internal_beta and (review['excluded_findings'] or review['missing_category']) else 'VALIDATED'),
                    'request_digest':digest(body),'result':batch.data if batch else None,
                    'beta_review':beta_reviews[-1] if self.internal_beta else None,
                    'raw_response':reply.body.decode('utf-8'),'observation':deepcopy(self.provider.observation)})
                if batch:batches.append(batch)
            if self.internal_beta and not any(f['validated_evidence'] for b in batches for f in b.data['findings']):
                raise NotSent('BETA59_NO_USABLE_SOURCE_GROUNDED_FINDINGS')
            provenance={'collection_intervals':[{'start':manifest['collection_started'],
                'end':manifest['collection_finished'],'basis':'ACTUAL_OFFICIAL_COLLECTION_PERIOD'}]}
            inputs=build_input(batches,assessment,provenance,packets,
                selected_sources={s['source']['occurrence'] for p in packets for s in p['spans']},
                read_original=lambda occurrence:self.sources.original(actor,company,collection,occurrence))
            inputs.update(analysis_provenance={'model':'gpt-6.1-sol','reasoning_effort':'medium','execution_mode':mode},
                          finding_reuse39={'reused':False,'reason':'FRESH_VALIDATED_FINDINGS_ONLY'})
            inputs['internal_review54']={'contract':processing58.VERSION,
                'trusted_processing':[b.data['trusted_processing'] for b in batches],
                'excluded_findings':[{'category':b.data['category'],**{k:v for k,v in e.items() if k!='statement_bg'}}
                    for b in batches for e in b.data.get('excluded_internal_findings',[])],
                'document_inventory':[{'filename':d.get('reference',{}).get('filename'),'status':d['status']} for d in assessment['documents']],
                'completeness':'NOT_ESTABLISHED','customer_publication_allowed':False}
            if self.internal_beta:
                exclusions=[{'category':r['category'],**e} for r in beta_reviews for e in r['excluded_findings']]
                inputs['beta59']={'profile':beta59.VERSION,'internal_only':True,'customer_publication_allowed':False,
                    'excluded_findings':exclusions,'missing_categories':[r['category'] for r in beta_reviews if r['missing_category']],
                    'incomplete_categories':[r['category'] for r in beta_reviews if r['excluded_findings'] or r['missing_category']],
                    'warnings':[{'category':r['category'],'finding_id':w['finding_id'],'reason':w['reason']} for r in beta_reviews for w in r['warnings']]}
                inputs['internal_review54']['excluded_findings']+=exclusions
            inputs['input_digest']=digest({k:v for k,v in inputs.items() if k!='input_digest'})
            body=terra_request(inputs)  # Actual validated Sol output, never a precomputed Terra hash.
            self.store.stage(actor,company,job,'TERRA',{'state':'REQUEST_RESERVED_NO_REPLAY',
                'request_digest':digest(body),'input_digest':inputs['input_digest'],'canonical_input':inputs})
            reply=self.provider.respond(body)
            self.store.stage(actor,company,job,'TERRA',{'state':'RESPONSE_RECEIVED_NOT_VALIDATED',
                'request_digest':digest(body),'input_digest':inputs['input_digest'],'canonical_input':inputs,
                'response_state':reply.state,'raw_response':reply.body.decode('utf-8',errors='replace') if reply.body else None})
            envelope={'recipient':self.store.recipient_for(actor,company,job)} if hasattr(self.store,'recipient_for') else {}
            report,message,audit=prepare_email(reply,inputs,job,execution_mode=mode,**envelope,
                internal_beta=self.internal_beta,
                fence=lambda:self.sources.fence(actor,company,collection,assessment,packets,manifest),
                record_review=lambda review:self.store.stage(actor,company,job,'TERRA_REVIEW62',review))
            self.store.stage(actor,company,job,'TERRA',{'state':'VALIDATED','request_digest':digest(body),
                'canonical_report':report,'raw_response':reply.body.decode('utf-8'),
                'observation':deepcopy(self.provider.observation),'customer_publishable':False})
            return self.store.finish(actor,company,job,message)
        except BaseException:
            self.store.failed(actor,company,job)
            raise
