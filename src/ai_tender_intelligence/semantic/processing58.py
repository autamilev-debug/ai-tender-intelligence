"""Python-owned processing limitations over the unchanged strict source delegate."""
from ai_tender_intelligence.assets import root as asset_root
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from . import internal54,references34
from .contract import SemanticError
from .retrieval import digest
from .validation import ValidatedBatch,_SEAL
from ..classification.contract import Reply
from ..extraction.models import canonical

VERSION='internal-processing58-v1';PROMPT_VERSION='sol-processing58-v1'
PROMPT_FILE=asset_root()/'prompts/sol_processing58.md'
def instructions():return internal54.instructions()+'\n'+PROMPT_FILE.read_text(encoding='utf-8')

def schema(packet):
    result=deepcopy(internal54.schema(packet))
    finding=result['$defs']['AliasFinding']
    finding['properties'].pop('source_limitations')
    finding['required'].remove('source_limitations')
    result['$defs'].pop('AliasLimitation',None)
    return result

def metadata(packet):
    return {**internal54.metadata(packet),'schema_revision':VERSION,'schema_sha256':digest(schema(packet)),
        'prompt_revision':PROMPT_VERSION,'prompt_sha256':sha256(instructions().encode()).hexdigest()}

def processing(packet):
    registry=references34.registry(packet);aliases={e['citation_id']:e['alias'] for e in registry['entries']}
    return {'authority':'PYTHON_PACKET_METADATA','packet_id':packet['packet_id'],
        'retrieval_is_exhaustive':packet['retrieval_is_exhaustive'],
        'whole_document_completeness':'NOT_ESTABLISHED',
        'sources':[{'citation_id':aliases[s['citation_id']],'occurrence':s['source']['occurrence'],
            'source_sha256':s['source']['source_sha256'],'extractor_sha256':s['source']['extractor_sha256'],
            'extraction_status':s['extraction_status'],'actual_caveats':list(s['limitations']),
            'omitted_oversized_rows':s.get('table_window',{}).get('omitted_oversized_row_count',0)} for s in packet['spans']]}

def limitation_codes(packet):
    """Derive only attested legacy reasons; PDF caveats remain separate facts."""
    result={}
    for s in processing(packet)['sources']:
        codes=[]
        if s['extraction_status']=='PARTIAL':codes.append('PARTIAL_EXTRACTION')
        if s['omitted_oversized_rows']>0:codes.append('OVERSIZED_ROWS_OMITTED')
        if packet['retrieval_is_exhaustive'] is False:codes.append('NONEXHAUSTIVE_PACKET_SELECTION')
        result[s['citation_id']]=codes
    return result

def request(packet,documents=()):
    body=internal54.request(packet,documents);value=json.loads(body['input'])
    value.update(verification_metadata=metadata(packet),trusted_processing=processing(packet))
    body.update(instructions=instructions(),input=canonical(value).decode())
    body['text']['format'].update(name='internal_processing58_v1',schema=schema(packet))
    if len(canonical(body))>131072:raise SemanticError('PROCESSING58_REQUEST_BOUND')
    return body

def _parse(reply):
    if reply.state!='complete' or not isinstance(reply.body,bytes) or len(reply.body)>262144:
        raise SemanticError('PROCESSING58_INCOMPLETE_RESPONSE')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise SemanticError('DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    try:return json.loads(reply.body,object_pairs_hook=pairs)
    except (ValueError,UnicodeError,RecursionError) as e:raise SemanticError('PROCESSING58_SCHEMA') from e

def validate(reply,packet,*,execution_mode='MOCK_ONLY'):
    data=_parse(reply)
    if not isinstance(data,dict) or any(data.get(k)!=v for k,v in metadata(packet).items()):
        raise SemanticError('PROCESSING58_CONTEXT')
    if not isinstance(data.get('findings'),list):raise SemanticError('PROCESSING58_SCHEMA')
    if any(not isinstance(f,dict) or 'source_limitations' in f for f in data['findings']):
        raise SemanticError('PROCESSING58_MODEL_PROCESSING_FIELD_FORBIDDEN')
    original=deepcopy(data['findings'])
    for f in data['findings']:
        # Exact authorized references are still checked by the existing delegate.
        # The legacy delegate's AI field is empty. Python-owned metadata is
        # attached separately, without its old 12-label model-output bound.
        f['source_limitations']=[]
    data.update(internal54.metadata(packet))
    batch=internal54.validate(Reply(canonical(data),input_tokens=reply.input_tokens,output_tokens=reply.output_tokens),
        packet,execution_mode=execution_mode)
    result=batch.data
    result.update(original_model_findings=original,internal_contract=VERSION,
        wire_metadata=metadata(packet),canonical_validation_delegate=internal54.VERSION,
        trusted_processing={**processing(packet),'derived_codes_by_alias':limitation_codes(packet)},
        raw_response_sha256=sha256(reply.body).hexdigest())
    return ValidatedBatch(result,_SEAL)

def assess_saved(reply,packet,*,execution_mode='LIVE'):
    """Separate offline view, NOT acceptance/repair of historical wire output."""
    data=_parse(reply)
    if any(data.get(k)!=v for k,v in internal54.metadata(packet).items()):
        raise SemanticError('PROCESSING58_HISTORICAL_CONTEXT')
    try:data=internal54.Wire.model_validate(data,strict=True).model_dump()
    except ValueError as e:raise SemanticError('PROCESSING58_HISTORICAL_SCHEMA') from e
    original=deepcopy(data);allowed=limitation_codes(packet);assertions=[]
    for f in data['findings']:
        for item in f.get('source_limitations',[]):
            if item['citation_id'] not in allowed:raise SemanticError('PROCESSING58_UNAUTHORIZED_HISTORICAL_LIMITATION_REF')
            assertions.append({'finding_id':f['finding_id'],**item,
                'supported_by_metadata':item.get('reason') in allowed.get(item.get('citation_id'),[]),
                'disposition':'MODEL_ASSERTION_RECORDED_NOT_TRANSLATED'})
        f.pop('source_limitations',None)
    data.update(metadata(packet))
    batch=validate(Reply(canonical(data)),packet,execution_mode=execution_mode)
    return {'assessment':'OFFLINE_DERIVED_VIEW_NOT_HISTORICAL_SUCCESS',
        'original_response_sha256':sha256(reply.body).hexdigest(),'original_model_findings':original['findings'],
        'model_limitation_assertions':assertions,'derived_internal_assessment':batch.data}
