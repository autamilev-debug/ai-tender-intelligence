"""Mechanical provenance validation, explicitly not semantic entailment or quality."""
from hashlib import sha256
from dataclasses import dataclass
import json,re
from . import contract
from .retrieval import digest
from ..extraction.models import canonical

_SEAL=object()


@dataclass(frozen=True,repr=False,init=False)
class ValidatedBatch:
    _body:bytes
    _seal:object
    def __init__(self,data,seal):
        if seal is not _SEAL:raise contract.SemanticError('UNVALIDATED_FINDINGS')
        object.__setattr__(self,'_body',canonical(data));object.__setattr__(self,'_seal',seal)
    @property
    def data(self):return json.loads(self._body)


def table_value(span,row,column):
    for r in span['rows']:
        if r['row']!=row:continue
        for index,c in enumerate(r['cells'],1):
            col=c.get('locator',{}).get('column',c.get('column',index))
            if col==column:
                value=c.get('text',c.get('value'))
                return None if value is None else str(value),c.get('locator',{'row':row,'column':column})
    return None,None


def validate(reply,packet,*,execution_mode='MOCK_ONLY',prompt_revision=None,contract_revision=None,evaluation_profile=None):
    if execution_mode not in {'MOCK_ONLY','LIVE'}:raise contract.SemanticError('INVALID_EXECUTION_MODE')
    if packet['packet_id']!='packet-'+digest({k:v for k,v in packet.items() if k!='packet_id'}):
        raise contract.SemanticError('PACKET_INTEGRITY_FAILED')
    submission=contract_revision=='tender-submission-verification-v3'
    internal=contract_revision=='internal-documentary54-v1'
    v2=internal or contract_revision in {'tender-evidence-verification-v2','tender-submission-verification-v3'}
    if evaluation_profile is not None and (evaluation_profile!=('gpt-6.1-sol','medium') or not v2 or submission):
        raise contract.SemanticError('UNRECOGNIZED_EVALUATION_PROFILE')
    if contract_revision and contract_revision not in {contract.VERSION,'tender-evidence-verification-v2','tender-submission-verification-v3','internal-documentary54-v1'}:
        raise contract.SemanticError('UNRECOGNIZED_VERIFICATION_CONTRACT')
    data=contract.parse(reply,revision=contract_revision)
    revision=prompt_revision or contract.PROMPT_VERSION
    if v2:
        from . import contract_v2
        if internal:
            from . import internal54 as active
        elif submission:
            from . import contract_submission33 as active
        else:active=contract_v2
        if prompt_revision and prompt_revision!=active.PROMPT_VERSION:raise contract.SemanticError('VERIFICATION_CONTEXT_MISMATCH')
        active.instructions();revision=active.PROMPT_VERSION;prompt_hash=active.prompt_hash() if internal else active.PROMPT_SHA256
        schema=active.RESPONSE_SCHEMA;version=active.VERSION
    else:
        contract.instructions(revision)  # Historical inspection is allowlisted and byte-verified.
        _,prompt_hash=contract.prompt_identity(revision);schema=contract.RESPONSE_SCHEMA;version=contract.VERSION
    expected={'schema_revision':version,'schema_sha256':sha256(canonical(schema)).hexdigest(),'prompt_revision':revision,
        'prompt_sha256':prompt_hash,'packet_id':packet['packet_id'],'category':packet['category'],
        'parent_tender':packet['scope']['parent_tender'],'selected_lot_ids':packet['scope']['lot_ids'],
        'model':contract.MODEL,'reasoning_effort':contract.EFFORT}
    if evaluation_profile is not None and not internal:
        expected.update(model=evaluation_profile[0],reasoning_effort=evaluation_profile[1])
        from .comparison32 import PROMPT_REVISION,instructions as comparison_instructions
        expected.update(prompt_revision=PROMPT_REVISION,prompt_sha256=sha256(comparison_instructions().encode()).hexdigest())
    if internal:expected.update(model='gpt-6.1-sol',reasoning_effort='medium')
    if any(data[k]!=v for k,v in expected.items()):raise contract.SemanticError('VERIFICATION_CONTEXT_MISMATCH')
    if v2:data=contract_v2.materialize(data,packet)
    spans={s['citation_id']:s for s in packet['spans']};seen=set();verified=[]
    if not data['findings']:raise contract.SemanticError('EMPTY_VERIFICATION_FINDINGS')
    states={'SUPPORTED':'STATED','CONTRADICTED':'CONFLICTING','NOT_ESTABLISHED':'NOT_ESTABLISHED','UNRESOLVED':'CONFLICTING'}
    if v2:states['UNRESOLVED']='UNRESOLVED'
    for finding in data['findings']:
        if finding['finding_id'] in seen:raise contract.SemanticError('DUPLICATE_FINDING_ID')
        seen.add(finding['finding_id'])
        if not re.search(r'[\u0400-\u04ff]',finding['statement_bg']):raise contract.SemanticError('BULGARIAN_STATEMENT_REQUIRED')
        if states[finding['verification_state']]!=finding['interpretation_state']:
            raise contract.SemanticError('INTERPRETATION_STATE_MISMATCH')
        if len(set(finding['applicable_lot_ids']))!=len(finding['applicable_lot_ids']) or not set(finding['applicable_lot_ids'])<=set(packet['scope']['lot_ids']):
            raise contract.SemanticError('UNRELATED_LOT_FINDING')
        if finding['verification_state'] in {'SUPPORTED','CONTRADICTED'} and not finding['evidence']:
            raise contract.SemanticError('REQUIRED_CITATION_MISSING')
        if finding['verification_state'] in {'NOT_ESTABLISHED','UNRESOLVED'} and (not finding['requires_further_verification'] or not finding['missing_evidence']):
            raise contract.SemanticError('UNCERTAINTY_NOT_PRESERVED')
        if finding['verification_state']=='CONTRADICTED' and not finding['contradictions']:
            raise contract.SemanticError('CONTRADICTION_NOT_EXPLAINED')
        refs=[];unique=set()
        for citation in finding['evidence']:
            span=spans.get(citation['citation_id'])
            if span is None:raise contract.SemanticError('CITATION_NOT_IN_PACKET')
            s=span['source']
            if citation['occurrence']!=s['occurrence'] or citation['source_sha256']!=s['source_sha256'] or citation['extractor_sha256']!=s['extractor_sha256']:
                raise contract.SemanticError('CITATION_REVISION_MISMATCH')
            key=canonical(citation)
            if key in unique:raise contract.SemanticError('DUPLICATE_CITATION')
            unique.add(key)
            if not citation['quote'].strip():raise contract.SemanticError('EMPTY_EVIDENCE_QUOTE')
            if span['kind']=='TABLE':
                if citation['table_row'] is None or citation['table_column'] is None:
                    raise contract.SemanticError('TABLE_CELL_REQUIRED')
                actual,cell=table_value(span,citation['table_row'],citation['table_column'])
            else:
                if citation['table_row'] is not None or citation['table_column'] is not None:
                    raise contract.SemanticError('FABRICATED_CELL_LOCATOR')
                actual,cell=span['text'],None
            if actual is None or citation['quote'] not in actual:raise contract.SemanticError('EVIDENCE_QUOTE_MISMATCH')
            if finding['applicability'] in {'PROCEDURE','SELECTED_LOTS'} and s['legal_applicability']!='VERIFIED':
                raise contract.SemanticError('UNPROVEN_APPLICABILITY_PROMOTION')
            refs.append({**citation,'source':s,'locator':span['locator'],'character_range':span['character_range'],
                'cell_locator':cell,'block_index':span['block_index'],'extraction_limitations':span['limitations']})
        if finding['applicability'] in {'UNRESOLVED','UNRELATED_LOTS'} and not finding['requires_further_verification']:
            raise contract.SemanticError('UNRESOLVED_SCOPE_NOT_PRESERVED')
        if finding['applicability'] in {'PROCEDURE','SELECTED_LOTS'} and not refs:
            raise contract.SemanticError('SCOPE_EVIDENCE_MISSING')
        if finding['applicability']=='UNRELATED_LOTS' and finding['applicable_lot_ids']:
            raise contract.SemanticError('UNRELATED_LOT_CANNOT_BIND_SELECTED_SCOPE')
        verified.append({**finding,'validated_evidence':refs,
            'qualified_finding_id':'finding-'+digest({'packet_id':packet['packet_id'],'finding':finding}),
            'mechanical_provenance':'VALIDATED','semantic_accuracy':'NOT_INDEPENDENTLY_EVALUATED'})
    quality={}
    if revision==contract.PROMPT_VERSION or v2:
        from .accuracy import audit_coverage
        quality={'coverage_review':audit_coverage(data,packet)}
    if submission:
        from .submission import audit
        quality['submission_coverage_review']=audit(data,packet)
    return ValidatedBatch({**data,**quality,'findings':verified,'assessment_id':packet['assessment_id'],
        'execution_mode':execution_mode,'mechanical_provenance':'VALIDATED',
        'semantic_accuracy':'NOT_EVALUATED_WITH_REAL_MODEL' if execution_mode=='MOCK_ONLY' else 'REAL_MODEL_OUTPUT_NOT_INDEPENDENTLY_EVALUATED',
        'usage':None,'measured_cost':None,'ai_ready':False,'corpus_frozen':False},_SEAL)


def integrate(assessment,batches):
    from copy import deepcopy
    from ..assessment.rules import decide
    result=deepcopy(assessment);findings=[]
    for batch in batches:
        if type(batch) is not ValidatedBatch or batch._seal is not _SEAL:
            raise contract.SemanticError('UNVALIDATED_FINDINGS')
        data=batch.data
        if data['assessment_id']!=assessment['assessment_id']:
            raise contract.SemanticError('STALE_ASSESSMENT_FINDINGS')
        findings.extend(data['findings'])
    support=[{'finding_id':f['qualified_finding_id'],'verification_state':f['verification_state'],
              'basis':'MOCK_OUTPUT_MECHANICAL_PROVENANCE_ONLY_NOT_COMPLETENESS_PROOF'} for f in findings]
    for dimension in ['required_section_coverage','source_applicability','unresolved_evidence']:
        result['dimensions'][dimension]['semantic_support']=support
    result['outcome']=decide(result['dimensions'])
    result['semantic_findings']=findings
    result['semantic_sufficiency']='NOT_ESTABLISHED'
    result['ai_ready']=result['corpus_frozen']=False
    return result
