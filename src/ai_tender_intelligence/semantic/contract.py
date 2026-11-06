"""Single authoritative Python schema and hash-pinned task instructions."""
from ai_tender_intelligence.assets import root as asset_root
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal
import json
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from ..classification.contract import MODEL,EFFORT,Reply,object_schema
from ..extraction.models import canonical
from ..assessment.candidates import LABELS

VERSION='tender-evidence-verification-v1'
MODEL_MAX_OUTPUT_TOKENS=128000  # Provider capability, never injected as a request ceiling.
PROMPT_VERSION='tender-verification-prompts-v2'
PROMPT_FILE=asset_root()/'prompts/luna_evidence_verification.md'
PROMPT_SHA256='ad8f9ff82d7cddc969919816a093dee293ce8a1184733e4d65d032958505d6ca'


class SemanticError(ValueError):pass


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Citation(Strict):
    citation_id:str=Field(min_length=1,max_length=90)
    occurrence:str=Field(min_length=1,max_length=80)
    source_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    extractor_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    quote:str=Field(min_length=1,max_length=800)
    table_row:int|None
    table_column:int|None


class Finding(Strict):
    finding_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    statement_bg:str=Field(min_length=1,max_length=1200)
    verification_state:Literal['SUPPORTED','CONTRADICTED','NOT_ESTABLISHED','UNRESOLVED']
    interpretation_state:Literal['STATED','NOT_ESTABLISHED','CONFLICTING']
    applicability:Literal['PROCEDURE','SELECTED_LOTS','UNRELATED_LOTS','UNRESOLVED']
    applicable_lot_ids:list[int]=Field(max_length=10)
    evidence:list[Citation]=Field(max_length=6)
    missing_evidence:list[str]=Field(max_length=20)
    contradictions:list[str]=Field(max_length=20)
    requires_further_verification:bool
    uncertainty:Literal['EVIDENCE_LINKED_NOT_INDEPENDENTLY_VERIFIED','UNRESOLVED','NOT_ESTABLISHED']


class Verification(Strict):
    schema_revision:str
    schema_sha256:str
    prompt_revision:str
    prompt_sha256:str
    packet_id:str
    category:Literal[tuple(LABELS)]
    parent_tender:int
    selected_lot_ids:list[int]=Field(max_length=10)
    model:str
    reasoning_effort:str
    findings:list[Finding]=Field(max_length=12)
    limitations:list[str]=Field(max_length=30)


RESPONSE_SCHEMA=Verification.model_json_schema()


def prompt_identity(revision=None):
    revision=revision or PROMPT_VERSION
    if revision==PROMPT_VERSION:return PROMPT_FILE,PROMPT_SHA256
    if revision=='tender-verification-prompts-v1':
        return PROMPT_FILE.parent/'history/luna_evidence_verification_v1.md','a6971c6399cd73cc07867c55c54ddc5cb656ffa338587fd3e443b37af007cbae'
    raise SemanticError('UNRECOGNIZED_PROMPT_REVISION')


def instructions(revision=None):
    revision=revision or PROMPT_VERSION
    path,expected=prompt_identity(revision)
    try:raw=path.read_bytes()
    except OSError:raise SemanticError('PROMPT_UNAVAILABLE') from None
    if len(raw)>16384 or sha256(raw).hexdigest()!=expected:raise SemanticError('PROMPT_HASH_MISMATCH')
    text=raw.decode('utf-8')
    if f'<!-- prompt-set: {revision} -->' not in text or f'<!-- contract: {VERSION} -->' not in text:
        raise SemanticError('PROMPT_REVISION_MISMATCH')
    return text


def request(packet,*,revision=None):
    if revision=='tender-submission-verification-v3':
        from .contract_submission33 import request as submission_request
        return submission_request(packet)
    if revision and revision!=VERSION:
        from . import contract_v2
        if revision!=contract_v2.VERSION:raise SemanticError('UNRECOGNIZED_VERIFICATION_CONTRACT')
        return contract_v2.request(packet)
    from .accuracy import coverage_guide
    metadata={'schema_revision':VERSION,'schema_sha256':sha256(canonical(RESPONSE_SCHEMA)).hexdigest(),
              'prompt_revision':PROMPT_VERSION,'prompt_sha256':PROMPT_SHA256,'model':MODEL,'reasoning_effort':EFFORT}
    body={'model':MODEL,'reasoning':{'effort':EFFORT},'store':False,'service_tier':'default',
          'instructions':instructions(),'input':canonical({'verification_metadata':metadata,'packet':packet,'coverage_guide':coverage_guide(packet)}).decode(),
          'text':{'format':{'type':'json_schema','name':'tender_evidence_verification_v1','strict':True,'schema':RESPONSE_SCHEMA}}}
    if len(canonical(body))>131072:raise SemanticError('VERIFICATION_REQUEST_BOUND')
    return body


@dataclass(frozen=True,repr=False)
class MockProvider:
    replies:dict
    model:str=MODEL
    reasoning_effort:str=EFFORT
    paid:bool=False
    def respond(self,body):
        packet=json.loads(body['input'])['packet']
        return self.replies.get(packet['packet_id'],Reply(state='refused'))


def parse(reply,*,revision=None):
    if not isinstance(reply,Reply):raise SemanticError('INVALID_REPLY_TYPE')
    if not isinstance(reply.state,str):raise SemanticError('INVALID_REPLY_STATE')
    if reply.state!='complete':
        codes={'refused':'VERIFICATION_REFUSED','incomplete':'VERIFICATION_INCOMPLETE',
               'provider_error':'VERIFICATION_PROVIDER_ERROR','unsupported':'VERIFICATION_UNSUPPORTED'}
        raise SemanticError(codes.get(reply.state,'INVALID_REPLY_STATE'))
    if not isinstance(reply.body,bytes) or len(reply.body)>262144:raise SemanticError('VERIFICATION_OUTPUT_BOUND')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise SemanticError('DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    try:
        value=json.loads(reply.body.decode('utf-8'),object_pairs_hook=pairs,
                         parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
        model=Verification
        if revision and revision!=VERSION:
            from . import contract_v2
            if revision=='internal-documentary54-v1':
                from .internal54 import Verification as InternalVerification
                return InternalVerification.model_validate(value,strict=True).model_dump()
            if revision not in {contract_v2.VERSION,'tender-submission-verification-v3'}:raise SemanticError('UNRECOGNIZED_VERIFICATION_CONTRACT')
            model=contract_v2.Verification
        return model.model_validate(value,strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError) as e:
        if isinstance(e,SemanticError):raise
        raise SemanticError('INVALID_VERIFICATION_SCHEMA') from None
