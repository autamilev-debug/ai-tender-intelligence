"""Fresh runtime wiring with real validators, mocked HTTP and source adapters."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from ai_tender_intelligence.analysis import runtime as r
from ai_tender_intelligence.analysis.provider import RuntimeProvider
from ai_tender_intelligence.classification.openai_provider import OpenAISettings
from ai_tender_intelligence.classification.contract import NotSent
from ai_tender_intelligence.semantic.retrieval import RetrievalIndex
from ai_tender_intelligence.semantic import shortrefs37,references34
from ai_tender_intelligence.reporting import synthesis36,validation39
from ai_tender_intelligence.reporting.runtime44 import render_mail


from ai_tender_intelligence.assessment.candidates import LABELS
from ai_tender_intelligence.assessment.rules import DIMENSIONS
from ai_tender_intelligence.semantic import contract_v2 as v2
from ai_tender_intelligence.classification.contract import Reply,encoded,MODEL,VERSION
from ai_tender_intelligence.extraction.models import canonical

def assessment():
    return {'assessment_id':'assessment-fixture-1','inputs':{'selection':{'parent_tender':1,'selected_eop_lots':[2]},'source_collection_status':'PARTIAL'},
        'categories':{c:{'candidates':[]} for c in LABELS},'counts':{'EXTRACTED':1,'NOT_ACQUIRED':1},
        'unresolved_dependencies':[{'occurrence':'missing','reason':'NOT_ACQUIRED','essentiality':'UNKNOWN'}],
        'dimensions':{d:{'state':'UNRESOLVED','positive_evidence':[],'reasons':['SYNTHETIC_UNVERIFIED']} for d in DIMENSIONS},
        'outcome':'UNRESOLVED','semantic_sufficiency':'NOT_ESTABLISHED','ai_ready':False,'corpus_frozen':False}

def document(text='За доказване на опит се представя списък с аналогични изпълнени обекти.',*,lot=None,kind='TEXT'):
    source={'occurrence':'source-fixture-1','sha256':'a'*64,'extractor_sha256':'b'*64,'filename':'genuine-shape-synthetic.docx',
        'format':'DOCX','parent_tender':1,'container_occurrence':None,'member_path':None,
        'reference':{'lot':lot,'official_reference':'https://app.eop.bg/today/1','kind':'REQUIREMENT','metadata':{},'document_id':1}}
    block={'kind':kind,'locator':{'part':'word/document.xml','xml_child':1},'text':text}
    return {'source':source,'status':'EXTRACTED','flags':['SYNTHETIC_FIXTURE'],'blocks':[block]}

def sample(p,*,state='SUPPORTED',statement='Документът посочва изискване; приложимостта е неустановена.'):
    s=p['spans'][0];row=column=None
    if s['kind']=='TABLE':row=s['rows'][0]['row'];column=s['rows'][0]['cells'][0].get('column',1)
    return Reply(canonical({**json.loads(v2.request(p)['input'])['verification_metadata'],
        'packet_id':p['packet_id'],'category':p['category'],'parent_tender':p['scope']['parent_tender'],'selected_lot_ids':p['scope']['lot_ids'],
        'findings':[{'finding_id':'source-observation','statement_bg':statement,'verification_state':state,
            'applicability':'UNRESOLVED','applicable_lot_ids':[],
            'evidence':[{'citation_id':s['citation_id'],**{k:s['source'][k] for k in ['occurrence','source_sha256','extractor_sha256']},'table_row':row,'table_column':column}],
            'source_limitations':[],'missing_evidence':['Приложимостта не е установена.'],
            'contradictions':['Изрично противоречие с посоченото твърдение.'] if state=='CONTRADICTED' else [],
            'requires_further_verification':True,'uncertainty':'UNRESOLVED'}],'limitations':['Синтетичен тест, не доказателство за качество.']}))

def envelope(body=None,*,state='completed',usage=True):
    value={'id':'resp_SYNTHETIC','model':MODEL,'status':state,'error':None,
        'output':[{'type':'reasoning','summary':[]},{'type':'message','role':'assistant','status':'completed',
                   'content':[{'type':'output_text','text':body if body is not None else '{}'}]}]}
    if usage:value['usage']={'input_tokens':100,'output_tokens':100,'output_tokens_details':{'reasoning_tokens':50}}
    return value

def http_response(value,status=200,headers=None):
    return httpx.Response(status,headers=headers,stream=httpx.ByteStream(encoded(value)))

def classification_response(request):
    selected={}; reviews=[]
    for category in request.categories:
        key=category['category_key']; candidates=request.candidates[key]
        chosen=next((e for e in candidates if e['description_bg'] in category['activity']), candidates[0] if candidates else None)
        reviews.append({'category_key':key,'considered_codes':[chosen['identifier']] if chosen else [],
                        'no_match_reason':None if chosen else 'Няма подкрепен кандидат.'})
        if chosen:
            value=selected.setdefault(chosen['identifier'],{'identifier':chosen['identifier'],'supports':[]})
            value['supports'].append({'category_key':key,'activity_quote':category['activity'],
                                     'description_quote':chosen['description_bg'],'relevance':'supported',
                                     'relationship_explanation':'Синтетичният отговор упражнява договора и автентичността; не доказва семантична точност.'})
    return {'schema_version':VERSION,'catalogue_version':request.catalogue_version,
            'category_reviews':reviews,'selections':list(selected.values())}

TEXT='Възложителят посочва предмет, техническа спецификация, участие, подаване на оферта, оценяване, финансова гаранция, договор и разяснения.'


class Store:
    def __init__(self):self.row=None;self.stages={};self.finished=False
    def begin(self,*a):
        if self.row:return self.row,False
        self.row={'id':uuid4(),'state':'PREPARING'};return self.row,True
    def stage(self,*a):self.stages[a[-2]]=deepcopy(a[-1])
    def failed(self,*a):self.row['state']='FAILED'
    def finish(self,*a):
        self.finished=True;self.row.update(a[-1],state='AWAITING_OWNER_APPROVAL');return self.row


class Sources:
    def __init__(self,fault=None):
        self.fault=fault;self.calls=[];self.a=assessment();self.doc=document(TEXT)
        source=self.doc['source'];self.a.update(unresolved_dependencies=[],documents=[{
            'occurrence':source['occurrence'],'source_sha256':source['sha256'],'status':'EXTRACTED','format':'DOCX',
            'reference':{'kind':'REQUIREMENT','identity':source['occurrence'],'lot':None,'tender':1,
                         'filename':source['filename'],'official_reference':source['reference']['official_reference']}}])
        self.index=RetrievalIndex(self.a,[self.doc])
        self.manifest={'collection_started':'2026-11-04T08:00:00+00:00','collection_finished':'2026-11-04T08:00:10+00:00'}
    def admit(self,*a):
        self.calls.append('admit')
        if self.fault=='admission':raise NotSent('RUNTIME_OPEN_ADMISSION_NOT_VERIFIED')
        return {'fixture':'MOCKED_OPEN_ADMISSION_ONLY'}
    def collect(self,*a):self.calls.append('collect');return 'fresh-collection'
    def prepare(self,*a):
        self.calls.append('extract/retrieve')
        if self.fault=='essential':self.a['outcome']='BLOCKED'
        return self.a,self.index,self.manifest
    def fence(self,*a):
        if self.fault=='changed':raise NotSent('RUNTIME_COLLECTED_CORPUS_OR_ACCESS_CHANGED')
    def original(self,*a):return b'not-used-for-copy-binding'


def run(fault=None,*,internal_beta=False):
    sources=Sources(fault);store=Store();calls=[];packets={p['category']:p for p in sources.index.packets()}
    def handle(req):
        body=json.loads(req.content);calls.append(body);value=json.loads(body['input'])
        if fault=='http-failure':return http_response({'error':'synthetic'},status=500)
        if body['model']=='gpt-6.1-sol':
            packet=packets[value['packet']['category']]
            data=json.loads(sample(packet,statement=TEXT).body)
            data.update(value['verification_metadata'])
            if body['text']['format']['name'].startswith('internal_') or packet['category'] in shortrefs37.CATEGORIES:
                lookup={e['citation_id']:e['alias'] for e in references34.registry(packet)['entries']}
                for f in data['findings']:
                    for e in f['evidence']:e['citation_id']=lookup[e['citation_id']]
            if fault=='bad-citation':data['findings'][0]['evidence'][0]['citation_id']='fabricated'
            if fault=='mixed-beta':
                if packet['category']=='TECHNICAL_SPECIFICATIONS':
                    bad=deepcopy(data['findings'][0]);bad.update(finding_id='bad-number',statement_bg='Източникът изисква 99999999 броя.')
                    data['findings'].append(bad)
                if packet['category']=='SUBMISSION_DEADLINES':data['findings']=[]
            if fault=='all-invalid-beta':
                for f in data['findings']:f['statement_bg']='Източникът изисква 99999999 броя.'
            if fault=='bad-source-hash':data['findings'][0]['evidence'][0]['source_sha256']='f'*64
            if fault=='unfinished-finding':
                broken=deepcopy(data['findings'][0]);broken.update(finding_id='unfinished',statement_bg='Условие за участие при')
                data['findings'].append(broken)
            if body['text']['format']['name']=='internal_processing58_v1':
                for f in data['findings']:f.pop('source_limitations',None)
            state='incomplete' if fault=='incomplete' else 'completed'
        else:
            data={'schema_revision':validation39.VERSION,'input_digest':value['input_digest'],
                  'model':'gpt-6.1-terra','reasoning_effort':'medium','language':'bg','sections':[]}
            for key,_ in synthesis36.SECTIONS:
                data['sections'].append({'key':key,'paragraphs':[{'contributions':[{
                    'text_bg':f['statement_bg'],'finding_ids':[f['finding_id']]}]} for f in value['findings']
                    if key==sorted(synthesis36.ALLOWED_SECTIONS[f['category']])[0]] if key!='SOURCES' else []})
            if fault=='terra-omission':
                next(s for s in data['sections'] if s['paragraphs'])['paragraphs'].clear()
            if fault in {'terra-number','terra-certainty'}:
                c=next(s for s in data['sections'] if s['paragraphs'])['paragraphs'][0]['contributions'][0]
                c['text_bg']+=(' Стойността е 9876543 EUR.' if fault=='terra-number' else ' Компанията отговаря на условията.')
            state='completed'
        result=envelope(json.dumps(data,ensure_ascii=False),state=state)
        result.update(model=body['model'],service_tier='default')
        return http_response(result)
    provider=RuntimeProvider(transport=httpx.MockTransport(handle),settings=OpenAISettings(True,'synthetic-only'))
    flow=r.RuntimeWorkflow(store,sources,provider,internal_beta=internal_beta)
    return flow,store,sources,calls

