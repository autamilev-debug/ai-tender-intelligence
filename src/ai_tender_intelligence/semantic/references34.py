"""Opt-in short-reference boundary; no prefix recovery or historical migration.

No live runner/release/ledger path is enabled here. A future experiment must pin
its new request and authorize it separately. Historical contracts stay unchanged.
"""
from ai_tender_intelligence.assets import root as asset_root
import json,re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from pydantic import Field
from . import contract_v2 as v2,contract_submission33 as prior
from .contract import SemanticError
from .retrieval import digest
from ..classification.contract import Reply
from ..extraction.models import canonical

VERSION='tender-submission-shortrefs-v4';PROMPT_VERSION='submission-references-v5'
PROMPT_FILE=asset_root()/'prompts/submission_references_v5.md'
PROMPT_SHA256='d36004241934b20b6d74a81d6097d76810ee5415849939a46b528f94b097c7dc'

class AliasReference(v2.EvidenceReference):
    citation_id:str=Field(pattern=r'^R[0-9]{4}$')

class AliasLimitation(v2.SourceLimitation):
    citation_id:str=Field(pattern=r'^R[0-9]{4}$')

class AliasFinding(v2.Finding):
    evidence:list[AliasReference]=Field(max_length=6)
    source_limitations:list[AliasLimitation]=Field(max_length=12)

class AliasVerification(v2.Verification):
    reference_registry_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    findings:list[AliasFinding]=Field(max_length=12)

def registry(packet):
    if packet.get('packet_id')!='packet-'+digest({k:v for k,v in packet.items() if k!='packet_id'}):
        raise SemanticError('PACKET_INTEGRITY_FAILED')
    spans=packet['spans'];ids=[s['citation_id'] for s in spans]
    if not ids or len(ids)>9999:raise SemanticError('REFERENCE_REGISTRY_SIZE_INVALID')
    if len(set(ids))!=len(ids):raise SemanticError('REFERENCE_REGISTRY_COLLISION')
    if any(not re.fullmatch(r'span-[0-9a-f]{64}',i) for i in ids):raise SemanticError('ORIGINAL_REFERENCE_FORMAT_INVALID')
    by_id={s['citation_id']:s for s in spans}
    entries=[{'alias':f'R{i:04d}','citation_id':cid,
        'source':{k:by_id[cid]['source'][k] for k in ['occurrence','source_sha256','extractor_sha256']}}
        for i,cid in enumerate(sorted(ids),1)]
    return {'packet_id':packet['packet_id'],'scope':deepcopy(packet['scope']),'entries':entries}

def schema(packet):
    aliases=[e['alias'] for e in registry(packet)['entries']]
    result=deepcopy(AliasVerification.model_json_schema())
    for name in ['AliasReference','AliasLimitation']:
        result['$defs'][name]['properties']['citation_id']['enum']=aliases
    return result

def instructions():
    raw=PROMPT_FILE.read_bytes()
    if sha256(raw).hexdigest()!=PROMPT_SHA256:raise SemanticError('REFERENCE_PROMPT_HASH_MISMATCH')
    return raw.decode('utf-8')

def metadata(packet):
    return {'schema_revision':VERSION,'schema_sha256':digest(schema(packet)),
        'prompt_revision':PROMPT_VERSION,'prompt_sha256':PROMPT_SHA256,'model':v2.MODEL,'reasoning_effort':v2.EFFORT,
        'reference_registry_sha256':digest(registry(packet))}

def request(packet):
    """Pure versioned request factory; never saves/pins or dispatches a request."""
    binding=registry(packet);forward={e['citation_id']:e['alias'] for e in binding['entries']}
    body=prior.request(packet);data=json.loads(body['input'])
    def wire(value):
        if isinstance(value,dict):
            result={}
            for k,v in value.items():
                if k=='citation_id':
                    if v not in forward:raise SemanticError('CONTEXT_REFERENCE_NOT_AUTHORIZED')
                    result[k]=forward[v]
                elif k=='equivalent_same_source_text_citations':
                    if any(i not in forward for i in v):raise SemanticError('CONTEXT_REFERENCE_NOT_AUTHORIZED')
                    result[k]=[forward[i] for i in v]
                else:result[k]=wire(v)
            return result
        if isinstance(value,list):return [wire(v) for v in value]
        return value
    data=wire(data);data['verification_metadata']=metadata(packet)
    data['reference_semantics']='EXACT_PACKET_LOCAL_ALIAS_ORIGINAL_PACKET_ID_NOT_WIRE_VIEW_HASH'
    body['input']=canonical(data).decode('utf-8');body['instructions']=instructions()
    body['text']['format'].update(name='tender_submission_shortrefs_v4',schema=schema(packet))
    if len(canonical(body))>131072:raise SemanticError('VERIFICATION_REQUEST_BOUND')
    return body

def validate(reply,packet,*,execution_mode='MOCK_ONLY'):
    """Resolve exact aliases, then reuse every established source/lot check."""
    from .validation import validate as prior_validate,ValidatedBatch,_SEAL
    if not isinstance(reply,Reply) or reply.state!='complete':raise SemanticError('REFERENCE_RESPONSE_NOT_COMPLETE')
    if not isinstance(reply.body,bytes) or len(reply.body)>262144:raise SemanticError('VERIFICATION_OUTPUT_BOUND')
    def pairs(items):
        value={}
        for k,v in items:
            if k in value:raise ValueError('duplicate')
            value[k]=v
        return value
    try:data=AliasVerification.model_validate(json.loads(reply.body,object_pairs_hook=pairs),strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError):raise SemanticError('INVALID_ALIAS_SCHEMA') from None
    expected=metadata(packet)
    expected.update(packet_id=packet['packet_id'],category=packet['category'],parent_tender=packet['scope']['parent_tender'],selected_lot_ids=packet['scope']['lot_ids'])
    if any(data[k]!=value for k,value in expected.items()):raise SemanticError('REFERENCE_CONTEXT_MISMATCH')
    binding=registry(packet);reverse={e['alias']:e['citation_id'] for e in binding['entries']}
    resolved=deepcopy(data)
    for f in resolved['findings']:
        for reference in [*f['evidence'],*f['source_limitations']]:
            alias=reference['citation_id']
            if alias not in reverse:raise SemanticError('REFERENCE_ALIAS_NOT_AUTHORIZED')
            reference['citation_id']=reverse[alias]
    # This adapter accepts only its NEW wire contract. It never tries to repair
    # old replies or match a prefix. Canonical checks run on exact original IDs.
    resolved.pop('reference_registry_sha256')
    resolved.update(schema_revision=prior.VERSION,schema_sha256=digest(prior.RESPONSE_SCHEMA),
        prompt_revision=prior.PROMPT_VERSION,prompt_sha256=prior.PROMPT_SHA256)
    batch=prior_validate(Reply(canonical(resolved),input_tokens=reply.input_tokens,output_tokens=reply.output_tokens),
        packet,execution_mode=execution_mode,contract_revision=prior.VERSION)
    result=batch.data
    result.update(**metadata(packet),reference_resolution='EXACT_ALIAS_LOOKUP_NO_NORMALIZATION',
        reference_registry=deepcopy(binding),wire_response_sha256=sha256(reply.body).hexdigest(),
        canonical_validation_contract=prior.VERSION)
    return ValidatedBatch(result,_SEAL)
