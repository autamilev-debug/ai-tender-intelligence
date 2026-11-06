"""Versioned correction: source references and deterministic display states.

Old contract remains intact for historical inspection; no failed reply conversion.
"""
from ai_tender_intelligence.assets import root as asset_root
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Literal
import re
from pydantic import Field
from .contract import Strict,Verification as LegacyVerification,SemanticError,MODEL,EFFORT
from ..extraction.models import canonical

VERSION='tender-evidence-verification-v2'
PROMPT_VERSION='tender-verification-prompts-v3'
PROMPT_FILE=asset_root()/'prompts/luna_evidence_verification_v3.md'
PROMPT_SHA256='ec91a994894dd4f4fa118ce696a53795fcc80b04ec7539bfd871a02535e27b61'

class EvidenceReference(Strict):
    citation_id:str=Field(min_length=1,max_length=90)
    occurrence:str=Field(min_length=1,max_length=80)
    source_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    extractor_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    table_row:int|None
    table_column:int|None

class SourceLimitation(Strict):
    citation_id:str=Field(min_length=1,max_length=90)
    reason:Literal['PARTIAL_EXTRACTION','OVERSIZED_ROWS_OMITTED','NONEXHAUSTIVE_PACKET_SELECTION']

class Finding(Strict):
    finding_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    statement_bg:str=Field(min_length=1,max_length=1200)
    verification_state:Literal['SUPPORTED','CONTRADICTED','NOT_ESTABLISHED','UNRESOLVED']
    applicability:Literal['PROCEDURE','SELECTED_LOTS','UNRELATED_LOTS','UNRESOLVED']
    applicable_lot_ids:list[int]=Field(max_length=10)
    evidence:list[EvidenceReference]=Field(max_length=6)
    source_limitations:list[SourceLimitation]=Field(max_length=12)
    missing_evidence:list[str]=Field(max_length=20)
    contradictions:list[str]=Field(max_length=20)
    requires_further_verification:bool
    uncertainty:Literal['EVIDENCE_LINKED_NOT_INDEPENDENTLY_VERIFIED','UNRESOLVED','NOT_ESTABLISHED']

class Verification(LegacyVerification):
    findings:list[Finding]=Field(max_length=12)

RESPONSE_SCHEMA=Verification.model_json_schema()
STATE={'SUPPORTED':'STATED','CONTRADICTED':'CONFLICTING','NOT_ESTABLISHED':'NOT_ESTABLISHED','UNRESOLVED':'UNRESOLVED'}

def instructions():
    raw=PROMPT_FILE.read_bytes()
    if sha256(raw).hexdigest()!=PROMPT_SHA256:raise SemanticError('PROMPT_HASH_MISMATCH')
    return raw.decode('utf-8')

def availability(packet):
    return [{'citation_id':s['citation_id'],'supplied_content':'COMPLETE_EXTRACTED_CELLS_OR_SPAN_NO_SILENT_CLIPPING',
        'extraction_status':s['extraction_status'],'extraction_limitations':s['limitations'],
        'omitted_oversized_rows':s.get('table_window',{}).get('omitted_oversized_row_count',0),
        'whole_original_document_complete':False,
        'table_cell_characters':[{'row':r['row'],'column':c.get('locator',{}).get('column',c.get('column',i)),
            'characters':len(str(c.get('text',c.get('value',''))))} for r in (s.get('rows') or []) for i,c in enumerate(r['cells'],1)]}
        for s in packet['spans']]

def request(packet):
    from .accuracy import coverage_guide
    metadata={'schema_revision':VERSION,'schema_sha256':sha256(canonical(RESPONSE_SCHEMA)).hexdigest(),
        'prompt_revision':PROMPT_VERSION,'prompt_sha256':PROMPT_SHA256,'model':MODEL,'reasoning_effort':EFFORT}
    body={'model':MODEL,'reasoning':{'effort':EFFORT},'store':False,'service_tier':'default','instructions':instructions(),
        'input':canonical({'verification_metadata':metadata,'packet':packet,'coverage_guide':coverage_guide(packet),
                           'source_availability':availability(packet)}).decode('utf-8'),
        'text':{'format':{'type':'json_schema','name':'tender_evidence_verification_v2','strict':True,'schema':RESPONSE_SCHEMA}}}
    if len(canonical(body))>131072:raise SemanticError('VERIFICATION_REQUEST_BOUND')
    return body

def content_claim(text):
    """Conservative guard for explicit content-loss narratives, not semantic QA.

    Machine-supported limitations are attached separately. Free text must not
    invent loss in a supplied cell. Unknown business facts remain permissible.
    """
    anchors=r'текст(?:ът|а)?|клетк(?:ата|ите|а)|извлечени(?:ето|я)|фрагмент(?:ът|а)?|вход(?:ът|а)?|input|cell|span|extract|fragment|continuation'
    loss=r'прекъснат\w*|отрязан\w*|съкратен\w*|truncat\w*|cut off|липсващ\w*|непълн\w*|missing|incomplete'
    # Tie loss to the content noun. "The text says execution is interrupted"
    # is a business fact, not a claim that the supplied text was truncated.
    pattern=(r'(?:'+anchors+r')(?:\s+(?:на|за|от|в)\s+[^,.!?;]{1,60})?\s+'
             r'(?:(?:е|са|is|was|has been|изглежда)\s+)?(?:'+loss+r')|'
             r'(?:'+loss+r')\s+(?:(?:предоставен\w*|supplied)\s+)?(?:'+anchors+r')|'
             r'извлечени(?:ето|ят)\s+прекъсва\s+текст')
    return bool(re.search(pattern,text,re.I))

def materialize(data,packet):
    """Exact attachment after source-ID checks; never normalize a model quote."""
    from .validation import table_value
    result=deepcopy(data);spans={s['citation_id']:s for s in packet['spans']}
    if any(content_claim(t) for t in data['limitations']):raise SemanticError('UNSUPPORTED_SOURCE_CONTENT_CLAIM')
    for f in result['findings']:
        if any(content_claim(t) for t in [f['statement_bg'],*f['missing_evidence'],*f['contradictions']]):
            raise SemanticError('UNSUPPORTED_SOURCE_CONTENT_CLAIM')
        f['interpretation_state']=STATE[f['verification_state']]
        limitations=[]
        for item in f['source_limitations']:
            s=spans.get(item['citation_id'])
            if s is None:raise SemanticError('LIMITATION_NOT_IN_PACKET')
            reason=item['reason']
            allowed=(reason=='PARTIAL_EXTRACTION' and s['extraction_status']=='PARTIAL' or
                reason=='OVERSIZED_ROWS_OMITTED' and s.get('table_window',{}).get('omitted_oversized_row_count',0)>0 or
                reason=='NONEXHAUSTIVE_PACKET_SELECTION' and packet.get('retrieval_is_exhaustive') is False)
            if not allowed:raise SemanticError('UNSUPPORTED_SOURCE_LIMITATION')
            text={'PARTIAL_EXTRACTION':'Източникът има запазени ограничения на извличането; това не доказва липса на продължение в конкретната предоставена клетка.',
                  'OVERSIZED_ROWS_OMITTED':'Метаданните потвърждават пропуснати прекомерно големи редове; предоставените клетки не са изрязани.',
                  'NONEXHAUSTIVE_PACKET_SELECTION':'Избраният пакет не е изчерпателен преглед на целия оригинален документ.'}[reason]
            limitations.append({'citation_id':item['citation_id'],'reason':reason,'explanation_bg':text})
        f['validated_source_limitations']=limitations
        for c in f['evidence']:
            s=spans.get(c['citation_id'])
            if s is None:raise SemanticError('CITATION_NOT_IN_PACKET')
            if any(c[k]!=s['source'][k] for k in ['occurrence','source_sha256','extractor_sha256']):
                raise SemanticError('CITATION_REVISION_MISMATCH')
            if s['kind']=='TABLE':
                if c['table_row'] is None or c['table_column'] is None:raise SemanticError('TABLE_CELL_REQUIRED')
                actual,_=table_value(s,c['table_row'],c['table_column'])
            else:
                if c['table_row'] is not None or c['table_column'] is not None:raise SemanticError('FABRICATED_CELL_LOCATOR')
                actual=s['text']
            if actual is None or not actual.strip():raise SemanticError('EMPTY_EVIDENCE_QUOTE')
            c['quote']=actual
        f['quotation_attachment']='DETERMINISTIC_COMPLETE_EXACT_SUPPLIED_CELL_OR_SPAN'
    return result
