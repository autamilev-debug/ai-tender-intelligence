"""Validated findings to non-publishable previews; no inference of sufficiency."""
from copy import deepcopy
from ..semantic.validation import ValidatedBatch,_SEAL
from ..semantic.contract import SemanticError
from ..semantic.retrieval import digest
from ..semantic.accuracy import audit_coverage
from ..assessment.disclosures import document_disclosure
from . import contract


def build_input(batches,assessment,provenance,packets,*,selected_sources=(),read_original=None,finding_selection=None):
    """Internal pure boundary. Authorized workflow supplies fresh local evidence."""
    findings=[];limitations=[];checks=[];excluded=[];seen=set();evaluated_packets=set();evaluation_modes={}
    selection=finding_selection or {};known_packets=set()
    for batch in batches:
        if type(batch) is not ValidatedBatch or batch._seal is not _SEAL:raise SemanticError('UNVALIDATED_REPORT_FINDINGS')
        data=batch.data
        known_packets.add(data['packet_id'])
        known_findings={f['qualified_finding_id'] for f in data['findings']}
        if data['packet_id'] in selection and not set(selection[data['packet_id']])<=known_findings:
            raise SemanticError('UNKNOWN_SELECTED_VALIDATED_FINDING')
        if (data['assessment_id']!=assessment['assessment_id'] or data['parent_tender']!=assessment['inputs']['selection']['parent_tender']
            or data['selected_lot_ids']!=assessment['inputs']['selection']['selected_eop_lots']):raise SemanticError('REPORT_SCOPE_OR_ASSESSMENT_MISMATCH')
        p=next((p for p in packets if p['packet_id']==data['packet_id']),None)
        if p is None:raise SemanticError('REPORT_PACKET_UNAVAILABLE')
        evaluated_packets.add(p['packet_id'])
        evaluation_modes[p['packet_id']]=data['execution_mode']
        limitations.extend(data['limitations'])
        for f in data['findings']:
            fid=f['qualified_finding_id']
            if data['packet_id'] in selection and fid not in selection[data['packet_id']]:
                excluded.append({'finding_id':fid,'reason':'PRIOR_NARROW_RESULT_NOT_SELECTED_FOR_REUSE'});continue
            if fid in seen:raise SemanticError('DUPLICATE_REPORT_FINDING')
            seen.add(fid)
            if f['applicability']=='UNRELATED_LOTS':
                excluded.append({'finding_id':fid,'reason':'UNRELATED_LOTS'});continue
            findings.append({**f,'category':data['category'],'luna_execution_mode':data['execution_mode']})
    if not set(selection)<=known_packets:raise SemanticError('UNKNOWN_SELECTED_PACKET')
    if not findings:raise SemanticError('NO_REPORT_FINDINGS')
    # Compatible reused findings can supply an exact missing row/condition citation.
    # Recompute the checklist over the selected sealed findings, not discarded old
    # findings or a superseded narrow assessment. This does not prove completeness.
    checks=[audit_coverage({'findings':findings},p) for p in packets if p['packet_id'] in evaluated_packets]
    documents=[document_disclosure(d) for d in assessment['documents']]
    from .document_coverage import build_document_coverage
    coverage=build_document_coverage(assessment,[p for p in packets if p['packet_id'] in evaluated_packets],findings,selected_sources=selected_sources,read_original=read_original,evaluation_modes=evaluation_modes)
    inputs={'schema_revision':contract.VERSION,'purpose':'DEVELOPMENT_PREVIEW_ONLY','language':'bg',
        'identity':{'parent_tender':assessment['inputs']['selection']['parent_tender']},
        'scope':{'selected_lot_ids':assessment['inputs']['selection']['selected_eop_lots'],
                 'total_lots':None,'legal_applicability':'UNRESOLVED'},
        'findings':findings,'limitations':list(dict.fromkeys(limitations)),
        'coverage':{'outcome':assessment['outcome'],'counts':assessment['counts'],'checks':checks,
                    'unresolved_dependencies':assessment['unresolved_dependencies']},
        'document_disclosures':documents,'document_coverage':coverage,
        'content_provenance':deepcopy(provenance),'excluded_findings':excluded,
        'customer_publication_allowed':False,'ai_ready':False,'corpus_frozen':False}
    inputs['finding_selection']=deepcopy(selection)
    inputs['input_digest']=digest(inputs)
    return inputs


def assemble(inputs,reply,*,execution_mode,observation=None):
    if execution_mode not in {'MOCK_ONLY','LIVE'}:raise SemanticError('REPORT_EXECUTION_MODE')
    if inputs['input_digest']!=digest({k:v for k,v in inputs.items() if k!='input_digest'}):raise SemanticError('REPORT_INPUT_CHANGED')
    result=contract.validate(reply,inputs)
    by_id={f['qualified_finding_id']:f for f in inputs['findings']}
    sections=[]
    for section,(_,title) in zip(result['sections'],contract.SECTION_TITLES):
        # Quotes, status and lot applicability never come back from the synthesizer.
        sections.append({'category':section['category'],'title_bg':title,
                         'findings':[deepcopy(by_id[i['finding_id']]) for i in section['items']]})
    sources={}
    for f in inputs['findings']:
        for e in f['validated_evidence']:
            key=e['occurrence'];sources[key]={**e['source'],'source_id':key}
    return {'schema_revision':'canonical-tender-report-v2-development','pipeline_revision':'P5-IMP-29',
            'language':'bg','status':'DEVELOPMENT_PREVIEW','publishable':False,
            'identity':inputs['identity'],'scope':inputs['scope'],'content_provenance':inputs['content_provenance'],
            'official_header':{'title':None,'authority':None,'submission_deadlines':None,'values':None,
                               'state':'NOT_ESTABLISHED_IN_SUPPLIED_FINDINGS'},
            'overview':{'purpose':'DEVELOPMENT_PREVIEW_ONLY','finding_ids':list(by_id)},
            'general_requirements':[f for f in inputs['findings'] if f['applicability']=='PROCEDURE'],
            'lots':[{'lot_id':lot,'requirements':[f for f in inputs['findings'] if f['applicability']=='SELECTED_LOTS' and lot in f['applicable_lot_ids']]} for lot in inputs['scope']['selected_lot_ids']],
            'cross_lot_rules':[],
            'ambiguities':[f for f in inputs['findings'] if f['applicability']=='UNRESOLVED' or f['verification_state']!='SUPPORTED'],
            'sections':sections,'sources':list(sources.values()),'coverage':inputs['coverage'],
            'document_disclosures':inputs['document_disclosures'],'document_coverage':inputs['document_coverage'],
            'limitations':inputs['limitations'],
            'excluded_findings':inputs['excluded_findings'],
            'generation':{'model':contract.MODEL,'reasoning_effort':contract.EFFORT,'execution_mode':execution_mode,
                          'prompt_revision':contract.PROMPT_VERSION,'prompt_sha256':contract.PROMPT_SHA256,
                          'input_digest':inputs['input_digest'],'usage':{'input_tokens':reply.input_tokens,'output_tokens':reply.output_tokens},
                          'observation':observation or {},'semantic_quality':'NOT_ESTABLISHED_BY_MOCKS'},
            'ai_ready':False,'corpus_frozen':False,'customer_publication_allowed':False}

