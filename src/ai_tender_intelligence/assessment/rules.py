"""A positive gate needs affirmative facts for every separate dimension."""
from hashlib import sha256
from pathlib import Path
import platform,unicodedata
from ..extraction.models import canonical

DIMENSIONS=('acquisition_coverage','readability','structural_coverage','source_applicability',
            'required_section_coverage','unresolved_evidence')


def decide(dimensions):
    """Trusted internal facts only; the live adapter cannot accept caller-supplied facts."""
    if set(dimensions)!=set(DIMENSIONS):raise ValueError('ASSESSMENT_DIMENSIONS_INVALID')
    for fact in dimensions.values():
        if fact.get('state') not in {'SATISFIED','BLOCKED','UNRESOLVED'}:
            raise ValueError('ASSESSMENT_STATE_INVALID')
    hard_gaps={'KNOWN_MISSING_ESSENTIAL_DOCUMENT','UNREADABLE_ESSENTIAL_SECTION',
               'UNPROCESSED_MANDATORY_TABLE','NO_USABLE_SOURCE_EVIDENCE',
               'KNOWN_SOURCE_OR_DERIVATIVE_INTEGRITY_FAILURE'}
    if any(f['state']=='BLOCKED' or f.get('known_essential_gaps') or
           hard_gaps.intersection(f.get('reasons',[])) for f in dimensions.values()):
        return 'INSUFFICIENT_EVIDENCE'
    def grounded(fact):
        proofs=fact.get('positive_evidence')
        return (not fact.get('unresolved_dependencies') and isinstance(proofs,list) and bool(proofs) and all(isinstance(p,dict) and p.get('assertion')
            and p.get('basis') and isinstance(p.get('anchors'),list) and p['anchors']
            and all(isinstance(a,dict) and a.get('occurrence') and a.get('source_sha256') and a.get('locator')
                    for a in p['anchors']) for p in proofs))
    if all(f['state']=='SATISFIED' and grounded(f) for f in dimensions.values()):
        return 'SUFFICIENT_EVIDENCE'
    return 'UNRESOLVED'


def identity():
    root=Path(__file__).parent
    return {'version':'evidence-sufficiency-v1','implementation_sha256':sha256(b''.join(
        (root/n).read_bytes() for n in ['rules.py','candidates.py','service.py'])).hexdigest(),
        'python':platform.python_version(),'unicode':unicodedata.unidata_version,
        'candidate_limit_per_category':8,'max_documents':100,'max_occurrences':200,
        'candidate_semantics':'LABEL_MATCH_ONLY_NEVER_SEMANTIC_PROOF'}
