"""Read-only assessments over original grants and existing extraction checkpoints."""
from collections import Counter
from hashlib import sha256
from copy import deepcopy
from ..acquisition.models import AcquisitionError
from ..extraction.models import canonical,ExtractionError
from ..extraction.service import Extractor,in_scope
from .candidates import LABELS,locate
from .rules import DIMENSIONS,decide,identity


def scope_of(reference):
    if reference['kind'] in {'ANNOUNCEMENT','ATTACHMENT'} or reference.get('announcement_id') is not None:
        return 'LOT_CLARIFICATION_SOURCE' if reference.get('lot') is not None else 'PARENT_CLARIFICATION_SOURCE'
    if reference.get('lot') is not None:return 'SELECTED_LOT_SOURCE'
    if reference.get('applicability')=='GENERAL_CANDIDATE':return 'GENERAL_CANDIDATE_SOURCE'
    return 'PARENT_OR_UNKNOWN_SOURCE'


class Assessor:
    def __init__(self,evidence,*,extractor=None):
        self.evidence=evidence
        self.extractor=extractor or Extractor(evidence)

    def assess(self,actor,company,collection):
        manifest=self.evidence.manifest(actor,company,collection)
        if len(manifest['artifacts'])>200:raise ExtractionError('ASSESSMENT_INVENTORY_BOUND')
        documents=[];categories={k:{'status':'NOT_ASSESSED','candidates':[],
            'semantic_coverage':'NOT_ASSESSED'} for k in LABELS}
        dependencies=[];inputs=[];processed=usable=0;truncations=set()
        for item in manifest['artifacts']:
            self.evidence.authorize(actor,company,collection)
            reference=item['reference'];row={'occurrence':item['id'],'source_sha256':item['blob_sha256'],
                'reference':deepcopy(reference),'format':item['validation']['kind'],
                'source_scope':scope_of(reference),'legal_applicability':'UNRESOLVED',
                'container_occurrence':item['container_occurrence'],'member_path':item['member_path'],
                'essentiality':'NOT_ESTABLISHED'}
            result_digest=None
            if not in_scope(manifest,item):
                row.update(status='OUT_OF_SELECTED_SCOPE',legal_applicability='EXCLUDED_BY_VERIFIED_SOURCE_LOT',flags=[])
            elif not item['blob_sha256']:
                row.update(status='NOT_ACQUIRED',flags=[item['validation'].get('error','SOURCE_MISSING')])
            elif not item['validation'].get('valid'):
                row.update(status='INVALID_SOURCE',flags=[item['validation'].get('error','SOURCE_INVALID')])
            elif item['validation']['kind']=='ZIP':
                row.update(status='CONTAINER',flags=['ASSESS_MEMBERS_SEPARATELY'])
            elif processed>=100:
                row.update(status='NOT_ASSESSED_BOUND',flags=['ASSESSMENT_DOCUMENT_BOUND']);truncations.add('DOCUMENT_LIMIT')
            else:
                processed+=1
                try:
                    doc=self.extractor.document(actor,company,collection,item['id'])
                    row.update(status=doc['status'],flags=doc['flags'],units=doc['units'],
                        extractor_sha256=doc['source']['extractor_sha256'],blocks=len(doc['blocks']))
                    result_digest=sha256(canonical({k:v for k,v in doc.items() if k not in {'source','blocks'}})+
                        canonical([{k:v for k,v in b.items() if k!='source'} for b in doc['blocks']])).hexdigest()
                    if doc['status'] in {'EXTRACTED','PARTIAL'} and doc['blocks']:usable+=1
                    found,limited=locate(doc);truncations.update(limited)
                    for category,candidates in found.items():
                        remaining=8-len(categories[category]['candidates'])
                        categories[category]['candidates'].extend(candidates[:remaining])
                        if len(candidates)>remaining:truncations.add(category)
                    if doc['status']!='EXTRACTED':
                        classification=('TRANSIENT_TECHNICAL_FAILURE' if 'EXTRACTION_TIMEOUT' in doc['flags'] else
                            'TECHNICAL_FAILURE_UNRESOLVED' if doc['status']=='FAILED' else 'ORDINARY_INCOMPLETE_EVIDENCE')
                        dependencies.append({'occurrence':item['id'],'classification':classification,
                            'reason':doc['status'],'essentiality':'UNKNOWN','action':'FUTURE_BOUNDED_HANDLING_NO_ROUTINE_ADMIN_QUEUE'})
                except (AcquisitionError,ExtractionError) as error:
                    if str(error) not in {'STORAGE_INTEGRITY_FAILED','EXTRACTION_DERIVATIVE_INTEGRITY_FAILED',
                            'EXTRACTION_IDENTITY_MISMATCH','CONFLICTING_EXTRACTION_DERIVATIVE'}:raise
                    row.update(status='TECHNICAL_INTEGRITY_FAILURE',flags=[str(error)])
                    dependencies.append({'occurrence':item['id'],'classification':'EXCEPTIONAL_ACTIONABLE_TECHNICAL_FAILURE',
                        'reason':str(error),'essentiality':'UNKNOWN','action':'REPAIR_INTEGRITY_NO_SUCCESS_CLAIM'})
            if row['status'] in {'NOT_ACQUIRED','INVALID_SOURCE','NOT_ASSESSED_BOUND'}:
                dependencies.append({'occurrence':item['id'],'classification':'ORDINARY_INCOMPLETE_EVIDENCE',
                    'reason':row['status'],'essentiality':'UNKNOWN','action':'PRESERVE_GAP_NO_ROUTINE_ADMIN_QUEUE'})
            documents.append(row)
            inputs.append({'occurrence':item['id'],'source_sha256':item['blob_sha256'],
                'source_validation_sha256':sha256(canonical(item['validation'])).hexdigest(),
                'extractor_sha256':row.get('extractor_sha256'),'derivative_digest':result_digest,
                'assessment_document_status':row['status']})
        for category in categories.values():
            if category['candidates']:category['status']='CANDIDATES_LOCATED';category['semantic_coverage']='UNRESOLVED'
        # Measured extraction gaps and semantic uncertainty are separate. No source
        # is called legally mandatory merely because a title or keyword matches.
        counts=Counter(r['status'] for r in documents)
        dimensions={d:{'state':'UNRESOLVED','positive_evidence':[],'reasons':[]} for d in DIMENSIONS}
        dimensions['acquisition_coverage'].update(observed_state='GAPS_FOUND' if counts['NOT_ACQUIRED'] or counts['INVALID_SOURCE'] else 'ENUMERATED_ACQUIRED_ONLY',
            reasons=['OFFICIAL_INVENTORY_COMPLETENESS_AND_REQUIRED_SOURCE_SET_NOT_VERIFIED'])
        dimensions['readability'].update(observed_state='GAPS_FOUND' if dependencies else 'EXTRACTION_HEURISTICS_PASSED',
            reasons=['ESSENTIAL_SOURCE_READABILITY_NOT_SEMANTICALLY_VERIFIED'])
        dimensions['structural_coverage'].update(observed_state='GAPS_FOUND' if dependencies or truncations else 'IMPLEMENTED_STRUCTURES_PROCESSED',
            reasons=['UNRULED_TABLES_COMPLEX_CONTENT_AND_ESSENTIAL_SECTIONS_NOT_CERTIFIED'])
        dimensions['source_applicability']['reasons']=['SOURCE_PLACEMENT_NOT_LEGAL_APPLICABILITY_PROOF']
        dimensions['required_section_coverage']['reasons']=['CANDIDATE_LABELS_ARE_NOT_SEMANTIC_COVERAGE_PROOF']
        dimensions['unresolved_evidence']['reasons']=['REQUIRED_DOCUMENT_SECTION_TABLE_AND_SCOPE_VALIDATION_OUTSTANDING']
        if not usable:
            dimensions['readability'].update(state='BLOCKED',reasons=['NO_USABLE_SOURCE_EVIDENCE'])
        if counts['TECHNICAL_INTEGRITY_FAILURE']:
            dimensions['structural_coverage'].update(state='BLOCKED',reasons=['KNOWN_SOURCE_OR_DERIVATIVE_INTEGRITY_FAILURE'])
        if manifest['status']=='COLLECTING':
            dimensions['acquisition_coverage']['reasons'].append('COLLECTION_STILL_OPEN')
        version=identity()
        basis={'validation':version,'collection':str(collection),'selection':manifest['selection'],
               'source_collection_status':manifest['status'],'input_evidence':inputs}
        self.evidence.authorize(actor,company,collection)
        if self.evidence.manifest(actor,company,collection)!=manifest:
            raise ExtractionError('ASSESSMENT_INPUT_CHANGED')
        result={'assessment_id':sha256(canonical(basis)).hexdigest(),'validation':version,'inputs':basis,
            'documents':documents,'counts':dict(counts),'categories':categories,'dimensions':dimensions,
            'unresolved_dependencies':dependencies,'candidate_search_truncations':sorted(truncations),
            'known_essential_missing_sources':[],
            'essentiality_note':'NO_RUNTIME_SEMANTIC_OR_REFERENCE_VALIDATOR_CAN_YET_ESTABLISH_THE_ESSENTIAL_SOURCE_SET',
            'outcome':decide(dimensions),'semantic_sufficiency':'NOT_ESTABLISHED','ai_ready':False,
            'corpus_frozen':False,'worker_activation':False,'retry_entitlements_modified':False}
        if len(canonical(result))>2*1024*1024:raise ExtractionError('ASSESSMENT_OUTPUT_BOUND')
        return result
