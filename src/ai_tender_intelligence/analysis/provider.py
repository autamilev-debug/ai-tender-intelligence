"""Existing bounded Responses HTTP, exact task body, no retries or fallback."""
import httpx
from ..semantic.provider import SemanticResponsesProvider
from ..classification.openai_provider import OpenAISettings,normalize_response,UncertainDispatch
from ..classification.contract import NotSent
from ..semantic.retrieval import digest

# Separate from all consumed experimental approvals. No environment activation.
REVIEWED_RUNTIME_REQUEST=None


class RuntimeProvider(SemanticResponsesProvider):
    def __init__(self,*,transport=None,settings=None,_permit=None):
        if transport is not None and not isinstance(transport,httpx.MockTransport):
            raise NotSent('RUNTIME_ONLY_MOCK_TRANSPORT_INJECTION')
        super().__init__(settings or OpenAISettings(),transport=transport,_permit=_permit)
        self.expected_model=None
        self.release45=None

    def bind_release(self,release):
        from .control45 import Release45
        if type(release) is not Release45 or not release.started or release.closed:raise NotSent('RUNTIME45_INVALID_RELEASE')
        self.release45=release

    @property
    def enabled(self):
        return self.settings.enabled and (self.is_mock or self._permit is not None and self._permit.active)

    def _request(self,body):return body

    def _normalize_task_response(self,data):
        return normalize_response(data,max_bytes=262144,expected_model=self.expected_model)

    def respond(self,body):
        model=body.get('model')
        if (model not in {'gpt-6.1-sol','gpt-6.1-terra'} or body.get('reasoning')!={'effort':'medium'}
            or body.get('max_output_tokens')!=100000 or 'tools' in body):raise NotSent('RUNTIME_MODEL_CONTRACT')
        if not self.is_mock and self.release45 is None:
            raise NotSent('RUNTIME_PAID_EXECUTION_DISABLED_OWNER_RELEASE_REQUIRED')
        if self.release45 is not None:
            body,self._permit=self.release45.request(body)
        self.expected_model=model
        reply=super().respond(body)
        if self.observation.get('actual_service_tier')!='default':raise UncertainDispatch('RUNTIME_UNEXPECTED_SERVICE_TIER')
        if self.release45 is not None:self.release45.observe(reply,self.observation)
        return reply
