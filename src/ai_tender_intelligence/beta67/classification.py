"""One-shot invited-user adapter around the existing Luna worker and contract."""
from decimal import Decimal
from uuid import uuid4, UUID
from sqlalchemy import insert
from ..classification.worker import Worker
from ..classification.contract import MODEL, EFFORT, NotSent, encoded
from ..classification.openai_provider import OpenAIResponsesProvider, _OneCallPermit
from ..persistence.schema import attempts


class Luna:
    model, reasoning_effort, paid = MODEL, EFFORT, True
    def __init__(self,settings,*,transport=None):
        self.settings,self.transport=settings,transport
        self.is_mock=transport is not None
        self.permit=None;self.observation={}
    @property
    def enabled(self):return self.settings.enabled
    def classify(self,request):
        if self.permit is None:raise NotSent('BETA67_LUNA_RESERVATION_REQUIRED')
        provider=OpenAIResponsesProvider(self.settings,transport=self.transport,_permit=self.permit)
        try:return provider.classify(request)
        finally:self.observation=provider.observation;self.permit.close();self.permit=None


class LunaWorker(Worker):
    def __init__(self,store,requests,user,company,request_id,intent,provider):
        super().__init__(store.db,frozenset({user}),provider,intent_id=UUID(str(intent)))
        self.profiles=store.profiles
        self.beta_requests,self.beta_request=requests,request_id
        self.beta_user,self.beta_company=user,company
    def _reserve(self,claim,request):
        with self._fenced(claim) as (s,job):
            if job is None:return None
            permission=self.beta_requests.read(self.beta_user,self.beta_company,self.beta_request,session=s)
            if (permission['state']!='RUNNING' or permission['kind']!='CPV'
                or permission['intent']!=claim.intent_id or claim.user!=self.beta_user
                or str(claim.company_id)!=str(self.beta_company) or job['dispatch_count']!=0):
                raise NotSent('BETA67_LUNA_AUTHORITY_SPENT')
            ident=uuid4()
            s.execute(insert(attempts).values(id=ident,intent_id=claim.intent_id,number=1,model=MODEL,
                reasoning_effort=EFFORT,is_mock=self.provider.is_mock,status='RESERVED',request_digest=request.digest,
                prompt_version=request.prompts.version,prompt_sha256=request.prompts.sha256,
                reserved_eur=Decimal(0),cost_basis='BETA67_PLATFORM_USAGE_ONLY_EUR_UNKNOWN'))
            self._patch(s,job['id'],processing_state='DISPATCHED',dispatch_count=1)
            self.provider.permit=_OneCallPermit(request.digest,expires_at=job['lease_until'].timestamp())
            return ident,len(encoded(request.responses_payload()))+4096
    def _fail(self,claim,reason,*,retry=False,unknown=False):
        return super()._fail(claim,reason,retry=False,unknown=unknown)
