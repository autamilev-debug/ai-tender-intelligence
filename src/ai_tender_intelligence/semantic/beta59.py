"""Explicit internal Beta: isolate model findings, never source/access failures."""
from copy import deepcopy
from hashlib import sha256
from collections import Counter
from . import processing58,internal54,references34
from .contract import SemanticError
from .validation import ValidatedBatch,_SEAL
from .accuracy import audit_coverage
from ..classification.contract import Reply
from ..extraction.models import canonical

VERSION='internal-beta59-v1'
ISOLATABLE={
 'INTERNAL54_SCHEMA','INTERNAL54_UNAUTHORIZED_ALIAS','INTERNAL54_UNSUPPORTED_CERTAINTY',
 'INTERNAL54_NO_USABLE_FINDINGS','UNSUPPORTED_SOURCE_CONTENT_CLAIM',
 'UNSUPPORTED_SOURCE_LIMITATION','UNRELATED_LOT_FINDING','UNRESOLVED_SCOPE_NOT_PRESERVED',
 'UNCERTAINTY_NOT_PRESERVED','REQUIRED_CITATION_MISSING','BULGARIAN_STATEMENT_REQUIRED',
 'CITATION_NOT_IN_PACKET','TABLE_CELL_REQUIRED','FABRICATED_CELL_LOCATOR',
 'EMPTY_EVIDENCE_QUOTE','DUPLICATE_CITATION','EVIDENCE_QUOTE_MISMATCH',
 'CONTRADICTION_NOT_EXPLAINED'}

def review(reply,packet,*,execution_mode='MOCK_ONLY'):
    registry=references34.registry(packet)  # Before any finding-level handling.
    allowed={e['alias'] for e in registry['entries']}
    data=processing58._parse(reply)  # Incomplete/ambiguous/malformed responses stop.
    expected={**processing58.metadata(packet),'packet_id':packet['packet_id'],'category':packet['category'],
              'parent_tender':packet['scope']['parent_tender'],'selected_lot_ids':packet['scope']['lot_ids']}
    if not isinstance(data,dict) or any(data.get(k)!=v for k,v in expected.items()):
        raise SemanticError('BETA59_RESPONSE_CONTEXT')
    findings=data.get('findings')
    if not isinstance(findings,list) or len(findings)>12:raise SemanticError('BETA59_ENVELOPE_SCHEMA')
    header={**deepcopy(data),'findings':[]}
    try:internal54.Wire.model_validate(header,strict=True)
    except ValueError as e:raise SemanticError('BETA59_ENVELOPE_SCHEMA') from e
    counts=Counter(f.get('finding_id') for f in findings if isinstance(f,dict) and isinstance(f.get('finding_id'),str))
    batches=[];excluded=[];warnings=[]
    for index,original in enumerate(findings):
        fid=original.get('finding_id') if isinstance(original,dict) else None
        reason=None;finding=deepcopy(original)
        if not isinstance(finding,dict):reason='INVALID_FINDING_SHAPE'
        elif isinstance(fid,str) and counts[fid]>1:reason='DUPLICATE_FINDING_ID'
        elif finding.get('applicability')=='UNRELATED_LOTS':reason='UNRELATED_LOTS'
        elif 'source_limitations' in finding:
            claims=finding.pop('source_limitations')
            if not isinstance(claims,list) or any(not isinstance(c,dict) or c.get('citation_id') not in allowed for c in claims):
                reason='UNAUTHORIZED_PROCESSING_REFERENCE'
            elif claims:warnings.append({'finding_id':fid,'reason':'MODEL_PROCESSING_ASSERTIONS_IGNORED',
                                  'original_assertions':deepcopy(claims)})
        if reason is None:
            subset={**deepcopy(data),'findings':[finding]}
            try:batches.append(processing58.validate(Reply(canonical(subset),input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens),packet,execution_mode=execution_mode))
            except SemanticError as e:
                reason=str(e)
                if reason not in ISOLATABLE and not reason.startswith('INTERNAL54_FACTUAL_ERROR:'):
                    raise  # Hash/source/context/integrity and unknown failures remain hard.
        if reason is not None:excluded.append({'finding_id':fid,'finding_index':index,'reason':reason})
    result={'category':packet['category'],'excluded_findings':excluded,'warnings':warnings,
            'missing_category':not batches,'batch':None,'raw_response_sha256':sha256(reply.body).hexdigest()}
    if batches:
        merged=deepcopy(batches[0].data)
        merged['findings']=[f for b in batches for f in b.data['findings']]
        merged.update(original_model_findings=deepcopy(findings),beta_profile=VERSION,
            beta_excluded_findings=deepcopy(excluded),beta_warnings=deepcopy(warnings),
            raw_response_sha256=sha256(reply.body).hexdigest(),
            coverage_review=audit_coverage({'findings':merged['findings']},packet))
        result['batch']=ValidatedBatch(merged,_SEAL)
    return result

BETA_TITLE='БЕТА ВЕРСИЯ — ДЕМОНСТРАЦИОНЕН АНАЛИЗ'
BETA_NOTICE=(
 'Този доклад демонстрира автоматизиран процес за откриване и анализ на обществени поръчки. Системата не е преминала цялостно тестване върху представителна извадка от поръчки с независимо проверени резултати.',
 'Не са интегрирани усъвършенствани услуги за извличане на документи, като Azure AI Document Intelligence, а обработката на някои PDF, DOCX и архивни формати е ограничена.',
 'Възможни са пропуснати документи, непълно извлечена информация и неточности в автоматичния анализ. Докладът не представлява изчерпателна проверка или правно становище. Преди вземане на решения проверете оригиналната документация.')
