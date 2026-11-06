"""Task-specific Responses boundary reusing the existing bounded HTTP implementation."""
import asyncio,re
from ..classification.openai_provider import OpenAIResponsesProvider,_OneCallPermit,normalize_response
from ..classification.contract import NotSent
from .contract import request
from .retrieval import digest
from ..extraction.models import canonical


class SemanticResponsesProvider(OpenAIResponsesProvider):
    READ_SECONDS=300
    TOTAL_SECONDS=600

    def __init__(self,*args,contract_revision=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.contract_revision=contract_revision

    def _request(self,packet):
        return request(packet,revision=self.contract_revision)

    def _transport_limits(self):
        return self.READ_SECONDS,self.TOTAL_SECONDS,262144

    def _normalize_task_response(self,data):
        return normalize_response(data,max_bytes=262144)

    def _record_task_observation(self,data):
        if not isinstance(data,dict):return
        details=data.get('incomplete_details')
        reason=details.get('reason') if isinstance(details,dict) else None
        self.observation['incomplete_reason']=reason if isinstance(reason,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',reason) else None
        usage=data.get('usage')
        if not isinstance(usage,dict):return
        for section,keys in [('input_tokens_details',('cached_tokens','cache_write_tokens')),
                             ('output_tokens_details',('reasoning_tokens',))]:
            source=usage.get(section)
            if isinstance(source,dict):
                self.observation[section]={k:source[k] for k in keys if type(source.get(k)) is int and 0<=source[k]<=2**31-1}

    def respond(self,packet):
        try:
            if not self.is_mock and self._permit is None:raise NotSent('SEMANTIC_RELEASE_REQUIRED')
            if not self.enabled:raise NotSent('PROVIDER_DISABLED')
            if self.cancel.is_set():raise NotSent('CANCELLED_BEFORE_DISPATCH')
            body=self._request(packet)  # Rechecks the exact task prompt/schema.
            try:asyncio.get_running_loop()
            except RuntimeError:pass
            else:raise NotSent('SYNCHRONOUS_WORKER_REQUIRED')
            if self._permit is not None:self._permit.consume(digest(body))
            headers={'Authorization':'Bearer '+self.settings.api_key,'Content-Type':'application/json',
                     'Accept':'application/json','Accept-Encoding':'identity'}
            # No CPV request/prompt loader, HTTP retry, new client framework or tools.
            return asyncio.run(self._dispatch(canonical(body),headers))
        finally:
            if self._permit is not None:self._permit.close()
