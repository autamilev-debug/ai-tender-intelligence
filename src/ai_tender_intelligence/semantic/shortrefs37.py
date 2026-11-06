"""New Sol wire contract; exact P34 aliases delegated to existing canonical checks."""
from ai_tender_intelligence.assets import root as asset_root
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import references34 as aliases, contract_v2 as v2, comparison32
from .contract import SemanticError
from .retrieval import digest
from .submission import guide, audit
from .validation import validate as canonical_validate, ValidatedBatch, _SEAL
from ..classification.contract import Reply
from ..extraction.models import canonical

VERSION = 'sol-tender-shortrefs37-v1'
PROMPT_VERSION = 'sol-deep-evidence37-v1'
MODEL = 'gpt-6.1-sol'
EFFORT = 'medium'
CATEGORIES = ('TECHNICAL_SPECIFICATIONS', 'SUBMISSION_DEADLINES')
PROMPT_FILE = asset_root() / 'prompts/sol_evidence_shortrefs37.md'
PROMPT_SHA256 = '642a75997a1e9e8c0708cf51c7bdd45bad30c12c8760424a12a0d7d991652366'


def instructions():
    raw = PROMPT_FILE.read_bytes()
    if sha256(raw).hexdigest() != PROMPT_SHA256:
        raise SemanticError('SOL37_PROMPT_CHANGED')
    return raw.decode('utf-8')


def metadata(packet):
    return dict(schema_revision=VERSION, schema_sha256=digest(aliases.schema(packet)),
        prompt_revision=PROMPT_VERSION, prompt_sha256=PROMPT_SHA256, model=MODEL,
        reasoning_effort=EFFORT, reference_registry_sha256=digest(aliases.registry(packet)))


def request(packet):
    if packet['category'] not in CATEGORIES:
        raise SemanticError('SOL37_CATEGORY')
    registry = aliases.registry(packet)
    lookup = {e['citation_id']: e['alias'] for e in registry['entries']}
    def wire(value):
        if isinstance(value, list): return [wire(v) for v in value]
        if not isinstance(value, dict): return value
        result = {}
        for k, v in value.items():
            if k == 'citation_id':
                if v not in lookup: raise SemanticError('SOL37_UNAUTHORIZED_CONTEXT_REF')
                result[k] = lookup[v]
            elif k == 'equivalent_same_source_text_citations':
                if any(i not in lookup for i in v): raise SemanticError('SOL37_UNAUTHORIZED_CONTEXT_REF')
                result[k] = [lookup[i] for i in v]
            else: result[k] = wire(v)
        return result
    body = deepcopy(v2.request(packet)); data = json.loads(body['input'])
    if packet['category'] == 'SUBMISSION_DEADLINES': data['submission_coverage'] = guide(packet)
    data = wire(data); data['verification_metadata'] = metadata(packet)
    data['reference_semantics'] = 'EXACT_PACKET_LOCAL_ALIAS_ORIGINAL_PACKET_ID'
    body.update(model=MODEL, reasoning={'effort': EFFORT}, instructions=instructions(), input=canonical(data).decode())
    body['text']['format'].update(name='sol_evidence_shortrefs37_v1', schema=aliases.schema(packet))
    if len(canonical(body)) > 131072: raise SemanticError('SOL37_REQUEST_BOUND')
    return body


def validate(reply, packet, *, execution_mode='MOCK_ONLY'):
    if packet['category'] not in CATEGORIES or reply.state != 'complete':
        raise SemanticError('SOL37_CATEGORY_OR_INCOMPLETE')
    if not isinstance(reply.body, bytes) or len(reply.body) > 262144:
        raise SemanticError('SOL37_RESPONSE_BOUND')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise ValueError('duplicate')
            result[k] = v
        return result
    try:
        data = aliases.AliasVerification.model_validate(json.loads(reply.body, object_pairs_hook=pairs), strict=True).model_dump()
    except (ValueError, UnicodeError, RecursionError):
        raise SemanticError('SOL37_SCHEMA') from None
    expected = {**metadata(packet), 'packet_id': packet['packet_id'], 'category': packet['category'],
        'parent_tender': packet['scope']['parent_tender'], 'selected_lot_ids': packet['scope']['lot_ids']}
    if any(data[k] != v for k, v in expected.items()): raise SemanticError('SOL37_CONTEXT')
    reverse = {e['alias']: e['citation_id'] for e in aliases.registry(packet)['entries']}
    resolved = deepcopy(data)
    for f in resolved['findings']:
        for ref in [*f['evidence'], *f['source_limitations']]:
            if ref['citation_id'] not in reverse: raise SemanticError('SOL37_ALIAS_NOT_AUTHORIZED')
            ref['citation_id'] = reverse[ref['citation_id']]
    resolved.pop('reference_registry_sha256')
    resolved.update(schema_revision=v2.VERSION, schema_sha256=digest(v2.RESPONSE_SCHEMA),
        prompt_revision=comparison32.PROMPT_REVISION,
        prompt_sha256=sha256(comparison32.instructions().encode()).hexdigest())
    # Delegation checks original IDs/hashes/cells/applicability. It does not claim
    # the old prompt ran; actual wire metadata/hash are restored and retained.
    batch = canonical_validate(Reply(canonical(resolved), input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
        packet, execution_mode=execution_mode, contract_revision=v2.VERSION,
        evaluation_profile=(MODEL, EFFORT))
    result = batch.data
    if packet['category'] == 'SUBMISSION_DEADLINES': result['submission_coverage_review'] = audit(result, packet)
    result.update(**metadata(packet), reference_registry=aliases.registry(packet),
        reference_resolution='EXACT_ALIAS_LOOKUP_NO_NORMALIZATION', wire_response_sha256=sha256(reply.body).hexdigest(),
        canonical_validation_contract=v2.VERSION, canonical_validation_profile='SOL32_SOURCE_CHECKS_ONLY')
    return ValidatedBatch(result, _SEAL)
