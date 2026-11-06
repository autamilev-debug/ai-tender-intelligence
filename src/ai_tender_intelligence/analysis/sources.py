"""Production-shaped adapters over owned search snapshots and existing evidence.

Only local corpus integrity/authorization is rechecked after acceptance. No live
freshness check, silent amendment replacement or deadline restart after admission.
"""
from datetime import datetime,timezone
import re
from ..classification.contract import NotSent
from ..acquisition.service import Collector
from ..semantic.service import Workflow as EvidenceWorkflow
from ..semantic.retrieval import digest


def procedure_mapping(payload,opportunity):
    """Exact official document link, never title similarity or a guessed child ID."""
    match=re.fullmatch(r'https://app\.eop\.bg/today/([1-9][0-9]*)',payload.get('official_url',''))
    if match:return int(match[1]),False
    ids=[i for i in payload.get('identities',[]) if isinstance(i,str) and i.startswith('eop:tender:')]
    lots=payload.get('lots',[])
    if (payload.get('source')=='TED' and len(ids)==1 and re.fullmatch(r'eop:tender:[1-9][0-9]*',ids[0])
        and len(lots)==len(opportunity.lots)==1 and lots[0]['id']==opportunity.lots[0].lot_id
        and lots[0].get('source_fields',{}).get('internal_id')==ids[0].rsplit(':',1)[1]):
        return int(ids[0].rsplit(':',1)[1]),True
    raise NotSent('RUNTIME_EOP_PARENT_MAPPING_NOT_ESTABLISHED')


class FreshSources:
    def __init__(self,results,evidence,http,*,clock=lambda:datetime.now(timezone.utc),admission=None,http_factory=None):
        self.results,self.evidence=results,evidence
        self.collector=Collector(evidence,http);self.flow=EvidenceWorkflow(evidence);self.clock=clock
        self.admission=admission
        self.http_factory=http_factory;self.owned_http=None

    def close(self):
        if self.owned_http is not None:
            self.owned_http.close();self.owned_http=None

    def admit(self,actor,company,selection):
        stored=self.results.read(actor,company,selection['search_id'])
        opportunity=next((o for o in stored.result.matches.opportunities if o.record_id==selection['record_id']),None)
        if opportunity is None:raise NotSent('RUNTIME_OWNED_NOTICE_UNAVAILABLE')
        payload=stored.source_payloads.get(opportunity.record_id,{})
        parent,ted_single=procedure_mapping(payload,opportunity)
        if parent!=selection['parent'] or (ted_single and self.admission is None):
            raise NotSent('RUNTIME_EOP_PARENT_MAPPING_NOT_ESTABLISHED')
        lots=selection['lots']
        if not isinstance(lots,list) or not 0<=len(lots)<=10 or len(set(lots))!=len(lots) or any(type(i) is not int or i<=0 for i in lots):
            raise NotSent('RUNTIME_EXACT_SELECTED_LOTS_REQUIRED')
        if not lots:
            if len(opportunity.lots)!=1 or (opportunity.lots[0].lot_id!='PROCEDURE' and not ted_single):raise NotSent('RUNTIME_EXACT_SELECTED_LOTS_REQUIRED')
        else:
            if ted_single:raise NotSent('RUNTIME_RELEVANT_LOT_MAPPING_NOT_ESTABLISHED')
            mapped={l.get('source_fields',{}).get('tenderId') for l in payload.get('lots',[]) if l['id'] in {m.lot_id for m in opportunity.lots}}
            if not set(lots)<=mapped:raise NotSent('RUNTIME_RELEVANT_LOT_MAPPING_NOT_ESTABLISHED')
        if self.admission is not None:
            if self.http_factory is not None:
                # A new admitted operation gets its own bounded transport. Idle
                # Telegram time must not consume the collection's HTTP budget.
                from .admission45 import EOPAdmission
                self.close();self.owned_http=self.http_factory()
                self.collector=Collector(self.evidence,self.owned_http)
                self.admission=EOPAdmission(self.owned_http,clock=self.clock)
            try:receipt=self.admission.verify(selection['parent'],lots)
            except BaseException:
                self.close();raise
            # The owner/profile/snapshot must still be valid after public HTTP.
            self.results.read(actor,company,selection['search_id'])
            return {'accepted_at':self.clock().isoformat(),'search_id':stored.id,'record_id':opportunity.record_id,
                    'parent':selection['parent'],'lots':lots,'official_open_evidence':receipt,
                    'identity_mapping':'TED_DOCUMENT_LINK_AND_SINGLE_INTERNAL_ID' if ted_single else 'EOP_PROCEDURE_URL',
                    'customer_publication':False}
        source_lots={l.get('source_fields',{}).get('tenderId'):l['id'] for l in payload.get('lots',[])}
        selected_matches={l.lot_id:l for l in opportunity.lots}
        checks=[]
        for lot in lots:
            lid=source_lots.get(lot)
            lm=selected_matches.get(lid)
            assessments=[a for a in stored.result.assessments if a.procedure_id==opportunity.procedure_id and a.source_lot_id==lid]
            if lm is None or len(assessments)!=1:raise NotSent('RUNTIME_RELEVANT_LOT_MAPPING_NOT_ESTABLISHED')
            a=assessments[0]
            # Current discovery deliberately sets UNVERIFIED. Do not promote
            # ACTIVE_AS_PUBLISHED, future deadlines or absence of cancellation.
            if a.current_openness!='OPEN' or a.exclude or not a.facts:
                raise NotSent('RUNTIME_OPEN_ADMISSION_NOT_VERIFIED')
            if not lm.deadline_instant:raise NotSent('RUNTIME_OFFICIAL_DEADLINE_UNVERIFIED')
            deadline=datetime.fromisoformat(lm.deadline_instant)
            if deadline.tzinfo is None or deadline<=self.clock():raise NotSent('RUNTIME_LOT_EXPIRED')
            checks.append({'lot':lot,'deadline':deadline.isoformat(),'source_fact_hashes':[f.sha256 for f in a.facts]})
        return {'accepted_at':self.clock().isoformat(),'search_id':stored.id,'record_id':opportunity.record_id,
                'parent':selection['parent'],'lots':lots,'official_open_evidence':checks,'customer_publication':False}

    def collect(self,actor,company,selection,key):
        try:
            manifest=self.collector.run(actor,company,selection['parent'],tuple(selection['lots']),
                key='analysis44-collection:'+digest({'key':key,'selection':selection}),max_downloads=100)
        finally:self.close()
        if manifest['status'] not in {'ACQUIRED','PARTIAL'}:raise NotSent('RUNTIME_DOCUMENT_COLLECTION_FAILED')
        return manifest['collection']

    def prepare(self,actor,company,collection):
        assessment,index=self.flow.prepare(actor,company,collection)
        return assessment,index,self.evidence.manifest(actor,company,collection)

    def fence(self,actor,company,collection,assessment,packets,manifest):
        current,index,now=self.prepare(actor,company,collection)
        if now!=manifest or current['assessment_id']!=assessment['assessment_id'] or index.packets()!=packets:
            raise NotSent('RUNTIME_COLLECTED_CORPUS_OR_ACCESS_CHANGED')
        self.evidence.authorize(actor,company,collection)

    def original(self,*args):return self.evidence.retrieve(*args)
