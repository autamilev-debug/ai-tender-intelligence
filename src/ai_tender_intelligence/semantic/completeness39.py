"""Offline completeness admission and exact genuine-finding reuse. No inference."""
import re
from copy import deepcopy
from .contract import SemanticError
from .retrieval import digest


def evidence_keys(finding):
    return frozenset(tuple(e.get(k) for k in ('occurrence', 'source_sha256', 'extractor_sha256',
        'citation_id', 'table_row', 'table_column')) for e in finding['validated_evidence'])


def clause_markers(text):
    """Small source-driven safety checklist, not a legal-completeness classifier."""
    rules = {
        'MANUFACTURER_DECLARATION': r'деклараци\w*\s+от\s+производител',
        'OTHER_SUITABLE_EVIDENCE': r'други\s+подходящи\s+доказател',
        'EQUIVALENT': r'еквивалент',
    }
    return {key for key, pattern in rules.items() if re.search(pattern, text, re.I)}


def manufacturer_exception(text):
    return bool(re.search(r'(?:при|когато|ако|където)[^.!?]{0,100}изключени[^.!?]{0,120}деклараци[^.!?]{0,50}производител'
                          r'|деклараци[^.!?]{0,50}производител[^.!?]{0,80}(?:при|когато|ако)[^.!?]{0,60}изключени', text, re.I))


def issues(findings):
    result = []
    for f in findings:
        text = f['statement_bg'].strip()
        fid = f['qualified_finding_id']
        if not text.endswith(('.', '!', '?', '…')):
            result.append({'finding_id': fid, 'code': 'UNFINISHED_FINDING'})
        related = [g['statement_bg'] for g in findings if evidence_keys(g).intersection(evidence_keys(f))
            and g['applicability'] == f['applicability'] and g['applicable_lot_ids'] == f['applicable_lot_ids']
            and g['verification_state'] == f['verification_state']]
        coverage_text = ' '.join(related) or text
        # Check only conditions actually in the supplied cited cells. Quotes and
        # missing-evidence metadata are never substituted for interpretation.
        source = ' '.join(e['quote'] for e in f['validated_evidence'])
        required = clause_markers(source) - clause_markers(coverage_text)
        for marker in sorted(required):
            if marker != 'EQUIVALENT':  # Equivalence is handled in report validation.
                result.append({'finding_id': fid, 'code': 'SOURCE_CLAUSE_NOT_INTERPRETED', 'clause': marker})
        if 'MANUFACTURER_DECLARATION' in clause_markers(source) and re.search(r'изключени|изключения', source, re.I):
            if not manufacturer_exception(coverage_text):
                result.append({'finding_id': fid, 'code': 'MANUFACTURER_EXCEPTION_CONDITION_MISSING'})
    return result


def require_complete(findings):
    problems = issues(findings)
    if problems:
        raise SemanticError('FINDING39_INCOMPLETE:' + problems[0]['code'])


def reuse_complete(inputs, current_batch, prior_batch, *, current_attempt, prior_attempt):
    """Caller supplies revalidated genuine batches, not arbitrary model metadata.

    This pure offline operation does not grant access. Any future execution must
    also use the existing private evidence/source fence, as in P37.
    """
    from .validation import ValidatedBatch, _SEAL
    if any(type(b) is not ValidatedBatch or b._seal is not _SEAL for b in (current_batch, prior_batch)):
        raise SemanticError('FINDING39_VALIDATED_BATCH_REQUIRED')
    current, prior = current_batch.data, prior_batch.data
    if (current['execution_mode'] != 'LIVE' or prior['execution_mode'] != 'LIVE'
        or current['packet_id'] != prior['packet_id'] or current['category'] != prior['category']
        or current['parent_tender'] != prior['parent_tender'] or current['selected_lot_ids'] != prior['selected_lot_ids']):
        raise SemanticError('FINDING39_REUSE_CONTEXT_MISMATCH')
    if inputs['input_digest'] != digest({k:v for k,v in inputs.items() if k != 'input_digest'}):
        raise SemanticError('FINDING39_INPUT_CHANGED')
    result = deepcopy(inputs); replacements = []
    by_current = {f['qualified_finding_id']:f for f in current['findings']}
    incomplete_ids = {i['finding_id'] for i in issues(current['findings'])}
    for n, f in enumerate(result['findings']):
        if f['qualified_finding_id'] not in by_current: continue
        expected = by_current[f['qualified_finding_id']]
        if any(f[k] != expected[k] for k in expected): raise SemanticError('FINDING39_CURRENT_FINDING_CHANGED')
        if f['qualified_finding_id'] not in incomplete_ids: continue
        candidates = [old for old in prior['findings'] if evidence_keys(old) == evidence_keys(f)
            and old['applicability'] == f['applicability'] and old['applicable_lot_ids'] == f['applicable_lot_ids']
            and old['verification_state'] == f['verification_state'] and not issues([old])]
        if len(candidates) != 1: raise SemanticError('FINDING39_NO_UNIQUE_COMPLETE_GENUINE_REUSE')
        old = candidates[0]
        # Same hashes/IDs must also resolve to the same entire original evidence.
        if old['validated_evidence'] != f['validated_evidence']: raise SemanticError('FINDING39_EVIDENCE_CHANGED')
        result['findings'][n] = {**deepcopy(old), 'category': prior['category'], 'luna_execution_mode': 'LIVE'}
        replacements.append({'removed_finding_id': f['qualified_finding_id'], 'reused_finding_id': old['qualified_finding_id'],
            'current_attempt': current_attempt, 'original_attempt': prior_attempt,
            'original_model': prior['model'], 'original_prompt_sha256': prior['prompt_sha256'],
            'original_schema_revision': prior['schema_revision'], 'source_packet_id': prior['packet_id'],
            'evidence_sha256': digest(old['validated_evidence']), 'statement_unchanged': True})
    require_complete([f for f in result['findings'] if f['category'] == current['category']])
    result['finding_reuse39'] = replacements
    result['analysis_provenance']['dataset'] = 'MIXED_GENUINE_SOL_P32_P38_AND_HISTORICAL_LUNA'
    for item in replacements:
        provenance = result['analysis_provenance']['findings']
        provenance.pop(item['removed_finding_id'], None)
        provenance[item['reused_finding_id']] = {'model': prior['model'], 'category': prior['category'],
            'execution_mode': 'LIVE', 'original_attempt': prior_attempt, 'prompt_sha256': prior['prompt_sha256']}
    result.pop('input_digest'); result['input_digest'] = digest(result)
    return result
