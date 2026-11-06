"""Internal Beta: technical report binding blocks; semantic flags diagnose only."""
import json
from hashlib import sha256
from ..classification.contract import Reply
from ..semantic.contract import SemanticError
from ..semantic.beta59 import VERSION as BETA_PROFILE
from ..semantic.internal54 import report_guard
from . import validation40_offline as strict
from .contract import organization_input
from .synthesis36 import SECTIONS

VERSION='internal-beta65-semantic-diagnostics-v1'


def technical_validate(reply,inputs):
    beta=inputs.get('beta59') or {}
    if (beta.get('profile')!=BETA_PROFILE or beta.get('internal_only') is not True
        or beta.get('customer_publication_allowed') is not False):
        raise SemanticError('BETA65_INTERNAL_MODE_REQUIRED')
    organization_input(inputs)  # Canonical hash/identity binding, not access authorization.
    if reply.state!='complete' or not isinstance(reply.body,bytes) or len(reply.body)>262144:
        raise SemanticError('BETA65_INCOMPLETE_OR_OVERSIZED_RESPONSE')
    def unique(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    try:data=strict.Synthesis.model_validate(json.loads(reply.body,object_pairs_hook=unique),strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError):raise SemanticError('BETA65_UNUSABLE_RESPONSE_STRUCTURE') from None
    if data['input_digest']!=inputs['input_digest']:raise SemanticError('BETA65_WRONG_INPUT_IDENTITY')
    if [s['key'] for s in data['sections']]!=[key for key,_ in SECTIONS]:
        raise SemanticError('BETA65_UNUSABLE_SECTION_STRUCTURE')
    known={f['qualified_finding_id'] for f in inputs['findings']}
    meaningful=False
    for section in data['sections']:
        for paragraph in section['paragraphs']:
            for contribution in paragraph['contributions']:
                # An unknown pointer cannot be linked safely. Whether a claim
                # actually follows from a KNOWN finding is semantic, not blocking.
                if not set(contribution['finding_ids'])<=known:
                    raise SemanticError('BETA65_UNRESOLVED_SOURCE_REFERENCE')
                if section['key']!='SOURCES' and any(c.isalpha() for c in contribution['text_bg']):meaningful=True
    if not meaningful:raise SemanticError('BETA65_NO_MEANINGFUL_REPORT_CONTENT')
    return data


def validate(reply,inputs):
    data=technical_validate(reply,inputs)
    diagnostics=[]
    # No semantic checker, including its own failure, can veto a technically
    # usable Beta report. Strict mode still calls those checkers normally.
    try:diagnostics.extend(strict.audit(reply,inputs)['issues'])
    except Exception as error:
        diagnostics.append({'checker':'report_audit','code':str(error) if isinstance(error,SemanticError) else type(error).__name__})
    try:report_guard(data,inputs)
    except Exception as error:
        diagnostics.append({'checker':'internal_report_guard','code':str(error) if isinstance(error,SemanticError) else type(error).__name__})
    return data,{'contract':VERSION,'semantic_policy':'DIAGNOSTIC_ONLY_NONBLOCKING',
        'semantic_accuracy':'NOT_INDEPENDENTLY_VERIFIED','technical_validation':'PASS',
        'original_response_sha256':sha256(reply.body).hexdigest(),'input_digest':inputs['input_digest'],
        'diagnostics':diagnostics,'text_rewritten':False,'excluded_paragraphs':[]}
