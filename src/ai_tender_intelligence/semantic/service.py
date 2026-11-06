"""Mock-only orchestration, local revision fencing, and existing sufficiency integration."""
from ..assessment.service import Assessor
from ..extraction.service import Extractor
from ..extraction.models import canonical
from ..classification.contract import NotSent
from .retrieval import RetrievalIndex
from .retrieval import digest
from .contract import MockProvider,SemanticError,request,MODEL,EFFORT
from .validation import validate,integrate


class Workflow:
    def __init__(self,evidence,*,extractor=None):
        self.evidence=evidence;self.extractor=extractor or Extractor(evidence)
        self.assessor=Assessor(evidence,extractor=self.extractor)

    def prepare(self,actor,company,collection):
        assessment=self.assessor.assess(actor,company,collection)
        docs=[]
        for item in assessment['documents']:
            if item['status'] in {'EXTRACTED','PARTIAL'}:
                docs.append(self.extractor.document(actor,company,collection,item['occurrence']))
        self.evidence.authorize(actor,company,collection)
        return assessment,RetrievalIndex(assessment,docs)

    def validate_saved(self,actor,company,collection,packet,reply):
        fresh,index=self.prepare(actor,company,collection)
        current=index.packet(packet['category'])
        if current!=packet:raise SemanticError('STALE_RETRIEVAL_OR_VERIFICATION_INPUT')
        result=validate(reply,current)
        self.evidence.authorize(actor,company,collection)
        return result

    def run_mock(self,actor,company,collection,provider):
        if type(provider) is not MockProvider or provider.paid or provider.model!=MODEL or provider.reasoning_effort!=EFFORT:
            raise NotSent('MOCK_PROVIDER_REQUIRED_NO_LIVE_DISPATCH')
        assessment,index=self.prepare(actor,company,collection);packets=index.packets()
        batches=[];outcomes=[]
        for packet in packets:
            self.evidence.authorize(actor,company,collection)
            try:
                body=request(packet)
                batch=validate(provider.respond(body),packet)
                batches.append(batch);outcomes.append({'packet_id':packet['packet_id'],'category':packet['category'],
                    'request_sha256':digest(body),'state':'MECHANICAL_PROVENANCE_VALIDATED','result':batch.data})
            except SemanticError as error:
                outcomes.append({'packet_id':packet['packet_id'],'category':packet['category'],
                                 'state':'VERIFICATION_FAILED','reason':str(error)})
        fresh,current=self.prepare(actor,company,collection)
        if fresh['assessment_id']!=assessment['assessment_id'] or current.packets()!=packets:
            raise SemanticError('STALE_RETRIEVAL_OR_VERIFICATION_INPUT')
        final=integrate(fresh,batches)
        self.evidence.authorize(actor,company,collection)
        return {'assessment':final,'packets':packets,'retrieval_omissions':index.omissions,'verification':outcomes,
                'execution_mode':'MOCK_ONLY','model':MODEL,'reasoning_effort':EFFORT,
                'live_dispatches':0,'usage':None,'measured_cost':None,'ai_ready':False,'corpus_frozen':False}
