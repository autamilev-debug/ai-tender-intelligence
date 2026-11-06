"""Synchronous Responses transport; shipped live-dispatch release is closed.

No OpenAI SDK, SDK retry loop, arbitrary endpoint, proxy environment or tools.
"""
from dataclasses import dataclass
import asyncio
import logging
import os
import re
from threading import Event, Lock
from time import monotonic, time as wall_time

import httpx

from .contract import (MODEL,EFFORT,VERSION,MAX_BYTES,MAX_REQUEST_BYTES,MAX_OUTPUT_TOKENS,RESPONSE_SCHEMA,
                       Reply,NotSent,encoded,_pairs)
from .prompts import load_prompts,PromptError

ENDPOINT='https://api.openai.com/v1/responses'
MAX_RESPONSE_BYTES=262144
TOTAL_SECONDS=90
READ_SECONDS=75
LIVE_DISPATCH_RELEASED=False  # Owner authorization requires a later reviewed release.


class _OneCallPermit:
    """Trusted internal, request-bound release; never an environment setting."""
    def __init__(self,digest,*,expires_at=None):
        self.digest=digest
        self.expires_at=expires_at
        self._used=False;self._closed=False;self._lock=Lock()

    @property
    def active(self):
        with self._lock:return not self._used and not self._closed and not self._expired()

    def _expired(self):
        return self.expires_at is not None and wall_time()>=self.expires_at

    def consume(self,digest):
        with self._lock:
            if self._used or self._closed or self._expired() or digest!=self.digest:
                raise NotSent('ONE_CALL_RELEASE_CLOSED_OR_MISMATCH')
            self._used=True

    def close(self):
        with self._lock:self._closed=True


class ProviderConfigurationError(ValueError):
    pass


class UncertainDispatch(Exception):
    """Sanitized exception: request may have reached the provider; no replay."""


@dataclass(frozen=True,repr=False)
class OpenAISettings:
    enabled: bool=False
    api_key: str | None=None

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ProviderConfigurationError('INVALID_PROVIDER_CONFIGURATION')
        if self.enabled and (not isinstance(self.api_key,str) or not 1<=len(self.api_key)<=1024
                or any(ord(c)<33 or ord(c)>126 for c in self.api_key)):
            raise ProviderConfigurationError('OPENAI_CREDENTIAL_MISSING_OR_INVALID')

    @classmethod
    def from_environment(cls,environ=None):
        source=os.environ if environ is None else environ
        flag=source.get('OPENAI_CLASSIFICATION_ENABLED','false').casefold()
        if flag not in {'false','0','true','1'}:
            raise ProviderConfigurationError('INVALID_PROVIDER_CONFIGURATION')
        if flag in {'false','0'}:return cls()  # Does not read a key when disabled.
        return cls(True,source.get('OPENAI_API_KEY'))


def _usage(data):
    usage=data.get('usage')
    if not isinstance(usage,dict):return None,None
    i,o=usage.get('input_tokens'),usage.get('output_tokens')
    if type(i) is int and type(o) is int and 0<=i<=2**31-1 and 0<=o<=2**31-1:return i,o
    return None,None  # Keep the worker's conservative reservation.


def normalize_response(data,*,max_bytes=MAX_BYTES,expected_model=MODEL):
    if not isinstance(data,dict) or data.get('model')!=expected_model:
        raise UncertainDispatch('INVALID_PROVIDER_ENVELOPE')
    i,o=_usage(data)
    status=data.get('status')
    if status=='incomplete':return Reply(state='incomplete',input_tokens=i,output_tokens=o)
    if status in {'failed','cancelled'} or data.get('error') is not None:
        return Reply(state='provider_error',input_tokens=i,output_tokens=o)
    if status!='completed' or not isinstance(data.get('output'),list):
        raise UncertainDispatch('INVALID_PROVIDER_ENVELOPE')
    texts=[];refused=False;messages=0
    for item in data['output']:
        if not isinstance(item,dict):raise UncertainDispatch('INVALID_PROVIDER_ENVELOPE')
        if item.get('type')=='reasoning':continue  # Never log/retain reasoning content.
        if (item.get('type')!='message' or item.get('role')!='assistant'
                or item.get('status') not in {None,'completed'} or not isinstance(item.get('content'),list)):
            raise UncertainDispatch('UNEXPECTED_PROVIDER_OUTPUT')
        messages+=1
        for part in item['content']:
            if not isinstance(part,dict):raise UncertainDispatch('INVALID_PROVIDER_ENVELOPE')
            if part.get('type')=='refusal':refused=True
            elif part.get('type')=='output_text' and isinstance(part.get('text'),str):texts.append(part['text'])
            else:raise UncertainDispatch('UNEXPECTED_PROVIDER_OUTPUT')
    if refused:return Reply(state='refused',input_tokens=i,output_tokens=o)
    if messages!=1 or len(texts)!=1:raise UncertainDispatch('AMBIGUOUS_PROVIDER_OUTPUT')
    try:body=texts[0].encode('utf-8')
    except UnicodeError:raise UncertainDispatch('INVALID_PROVIDER_ENCODING') from None
    if len(body)>max_bytes:return Reply(state='unsupported',input_tokens=i,output_tokens=o)
    return Reply(body,input_tokens=i,output_tokens=o)


class OpenAIResponsesProvider:
    model,reasoning_effort,paid=MODEL,EFFORT,True

    def __init__(self,settings=None,*,transport=None,cancel=None,clock=monotonic,_permit=None):
        self.settings=settings if settings is not None else OpenAISettings()
        if _permit is not None and type(_permit) is not _OneCallPermit:
            raise ProviderConfigurationError('INVALID_ONE_CALL_RELEASE')
        self._permit=_permit
        if transport is not None and not isinstance(transport,httpx.MockTransport):
            raise ProviderConfigurationError('ONLY_MOCK_TRANSPORT_INJECTION_ALLOWED')
        self.is_mock=isinstance(transport,httpx.MockTransport)
        self.transport=transport
        self.cancel=cancel if cancel is not None else Event()
        self.clock=clock
        self.observation={}
        for name in ('httpx','httpcore'):
            logging.getLogger(name).setLevel(logging.WARNING)

    @property
    def enabled(self):
        if self._permit is not None:return self.settings.enabled and self._permit.active
        return self.settings.enabled and (self.is_mock or LIVE_DISPATCH_RELEASED)

    def classify(self,request):
        try:return self._classify(request)
        finally:
            if self._permit is not None:self._permit.close()

    def _classify(self,request):
        self.observation={}
        if not self.enabled:raise NotSent('PROVIDER_DISABLED')
        if self.cancel.is_set():raise NotSent('CANCELLED_BEFORE_DISPATCH')
        current=load_prompts(RESPONSE_SCHEMA,VERSION,request.prompts.root)
        if current.sha256!=request.prompts.sha256:raise PromptError('PROMPT_HASH_MISMATCH')
        body=encoded(request.responses_payload())
        if len(body)>MAX_REQUEST_BYTES:raise NotSent('REQUEST_LIMIT')
        headers={'Authorization':'Bearer '+self.settings.api_key,'Content-Type':'application/json',
                 'Accept':'application/json','Accept-Encoding':'identity'}
        # The durable worker is synchronous. Refuse nested event-loop use
        # before dispatch, rather than creating a background request thread.
        try:asyncio.get_running_loop()
        except RuntimeError:pass
        else:raise NotSent('SYNCHRONOUS_WORKER_REQUIRED')
        if self._permit is not None:self._permit.consume(request.digest)
        try:return asyncio.run(self._dispatch(body,headers))
        except (NotSent,UncertainDispatch):raise
        except (Exception,KeyboardInterrupt):
            raise UncertainDispatch('PROVIDER_OUTCOME_UNKNOWN') from None

    def _transport_limits(self):
        return READ_SECONDS,TOTAL_SECONDS,MAX_RESPONSE_BYTES

    def _record_task_observation(self,data):
        pass  # CPV behavior unchanged; task adapters may retain safe metadata.

    def _normalize_task_response(self,data):
        return normalize_response(data)

    async def _dispatch(self,body,headers):
        read_seconds,total_seconds,response_bytes=self._transport_limits()
        start=self.clock()
        timeout=httpx.Timeout(connect=5,read=read_seconds,write=10,pool=5)
        transport=self.transport if self.is_mock else httpx.AsyncHTTPTransport(retries=0)
        try:
            # Covers header wait and stalled/trickling bodies, not only checks
            # made after data arrives. No background job or retry is spawned.
            async with asyncio.timeout(total_seconds), httpx.AsyncClient(transport=transport,timeout=timeout,trust_env=False,follow_redirects=False) as client:
                if self.cancel.is_set():raise NotSent('CANCELLED_BEFORE_DISPATCH')
                async with client.stream('POST',ENDPOINT,content=body,headers=headers) as response:
                    self.observation={'http_status':response.status_code}
                    if response.status_code!=200:
                        # Never infer "free retry" from an HTTP error alone.
                        raise UncertainDispatch('PROVIDER_HTTP_ERROR')
                    if response.headers.get('content-encoding','identity').casefold()!='identity':
                        raise UncertainDispatch('UNSUPPORTED_PROVIDER_ENCODING')
                    length=response.headers.get('content-length')
                    if length and (not length.isascii() or not length.isdigit() or int(length)>response_bytes):
                        raise UncertainDispatch('RESPONSE_LIMIT')
                    raw=bytearray()
                    # Do not buffer a minimum chunk before checking the total
                    # deadline: a slow trickle must not evade it indefinitely.
                    async for chunk in response.aiter_raw():
                        if self.cancel.is_set() or self.clock()-start>=total_seconds:
                            raise UncertainDispatch('CANCELLED_OR_TIMED_OUT')
                        if len(raw)+len(chunk)>response_bytes:raise UncertainDispatch('RESPONSE_LIMIT')
                        raw.extend(chunk)
                    if self.cancel.is_set() or self.clock()-start>=total_seconds:
                        raise UncertainDispatch('CANCELLED_OR_TIMED_OUT')
                    import json
                    data=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs)
                    # Bounded metadata only: no headers/body/reasoning/keys in
                    # logs. The isolated evaluation may retain this observation.
                    if isinstance(data,dict):
                        safe=lambda value: value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',value) else None
                        self.observation.update({
                            'actual_model':safe(data.get('model')),
                            'provider_status':safe(data.get('status')),
                            'actual_service_tier':safe(data.get('service_tier')),
                            'response_id':safe(data.get('id')),
                            'request_id':safe(response.headers.get('x-request-id')),
                            'input_tokens':_usage(data)[0],'output_tokens':_usage(data)[1]})
                    self._record_task_observation(data)
                    return self._normalize_task_response(data)
        except (NotSent,UncertainDispatch):raise
        except httpx.PoolTimeout:
            raise NotSent('CONNECTION_NOT_ACQUIRED') from None
        except (Exception,KeyboardInterrupt):
            # DNS/TLS/connect/read/write failures are conservatively uncertain.
            # Local close/cancellation cannot undo remote work already accepted.
            raise UncertainDispatch('PROVIDER_OUTCOME_UNKNOWN') from None
