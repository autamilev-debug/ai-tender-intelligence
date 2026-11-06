"""Versioned offline correction of P36 checks; pinned historical validators stay intact.

Exact decimal equivalence and source-connected coverage are not semantic entailment.
No provider, paid plan, release or publication operation is defined here.
"""
import json
import re
from decimal import Decimal
from typing import Literal
from ..classification.contract import Reply
from ..semantic.contract import SemanticError
from ..semantic.completeness39 import issues as finding_issues, clause_markers, manufacturer_exception
from . import synthesis36 as old
from .contract import organization_input

VERSION = 'terra-bulgarian-email-v3-validation39'


class Synthesis(old.Synthesis):
    schema_revision: Literal['terra-bulgarian-email-v3-validation39']


NUMBER = r'(?<![\w])(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\d)'
UNITS = r'%|процент\w*|евро|EUR\b|лв\.?|BGN\b|мм/с|мм\b|cm\b|GB\b|GHz\b|MHz\b|VAC\b|VA\b|W\b|Hz\b|ppm\b|грама?\b|g\b|месеца?\b|месеци\b|дни\b|ден\b|бр\.?'


def normalized(text, scope_ids=()):
    text = text.replace('\u00a0', ' ').replace('\u202f', ' ')
    # Documentary coordinates and legal cross-references remain in canonical
    # evidence; they are not quantities or deadlines to repeat in every passage.
    text = re.sub(r'\b(?:чл\.|ал\.|ред|колона|позиция\s*№)\s*\d+[а-я]?', ' ', text, flags=re.I)
    text = re.sub(r'\b(?:E|F)\d+\b', ' ', text)
    text = re.sub(r'\b[23]D\b', ' ', text, flags=re.I)  # Documentary product label, not a quantity.
    text = re.sub(r'\bv(?=\d)', '', text, flags=re.I)
    for identifier in scope_ids:
        text = re.sub(r'(?<!\d)' + re.escape(str(identifier)) + r'(?!\d)', ' ', text)
    text = re.sub(r'\bтри\s+дни\b', '3 дни', text, flags=re.I)
    text = re.sub(r'\bв\s+триднев\w*\s+срок', 'до 3 дни', text, flags=re.I)
    text = re.sub(r'\bтриднев\w*', '3 дни', text, flags=re.I)
    return text


def value(token):
    return Decimal(re.sub(r'\s', '', token).replace(',', '.'))


def numbers(text, scope_ids=()):
    return {value(m.group()) for m in re.finditer(NUMBER, normalized(text, scope_ids))}


def unit(token):
    t = token.casefold().rstrip('.')
    for pattern, result in [(r'евро|eur', 'EUR'), (r'лв|bgn', 'BGN'), (r'процент|%', '%'),
                            (r'грама?|g', 'G'), (r'месец', 'MONTH'), (r'дни|ден', 'DAY'), (r'бр', 'COUNT')]:
        if re.match(pattern, t): return result
    return t.upper()


def quantities(text, scope_ids=()):
    text = normalized(text, scope_ids); result = set()
    for m in re.finditer(NUMBER, text):
        suffix = text[m.end():]; u = re.match(r'\s*('+UNITS+')', suffix, re.I)
        label = unit(u.group(1)) if u else ('EUR' if text[:m.start()].rstrip().endswith('€') else None)
        if label:
            prefix = text[max(0, m.start()-30):m.start()]
            comparator = None
            if re.search(r'(?:минимум|най-малко|поне)\s*$', prefix, re.I): comparator = 'MIN'
            elif re.search(r'(?:максимум|не\s+повече\s+от|до)\s*$', prefix, re.I): comparator = 'MAX'
            elif re.search(r'(?:повече\s+от|над)\s*$', prefix, re.I): comparator = 'GT'
            elif re.search(r'(?:по-малко\s+от|под)\s*$', prefix, re.I): comparator = 'LT'
            result.add((value(m.group()), label, comparator))
    return {q for q in result if q[2] is not None or not any(other[:2] == q[:2] and other[2] is not None for other in result)}


def dimensions(text):
    return {tuple(value(x) for x in re.split(r'\s*[×xх]\s*', m.group(1)))
            for m in re.finditer(r'(\d+(?:[.,]\d+)?\s*[×xх]\s*\d+(?:[.,]\d+)?(?:\s*[×xх]\s*\d+(?:[.,]\d+)?)*)\s*(?:мм|cm)', text, re.I)}


def vat(text):
    result = set()
    if re.search(r'без\s+ДДС', text, re.I): result.add('EXCLUDED')
    if re.search(r'с\s+(?:включен\s+)?ДДС', text, re.I): result.add('INCLUDED')
    return result


def anchors(text):
    result = clause_markers(text)
    rules = {'CONDITIONAL': r'\bако\b|в\s+случай|\bкогато\b|при\s+(?:отказ|невъзможност|използване)',
        'EXCEPTION': r'освен(?!\s+това\b)|изключени|изключения', 'NO_INCREASE': r'не\s+подлежи\s+на\s+увеличение',
        'NEGATED_REQUIREMENT': r'не\s+се\s+изисква|отпада\s+задължението|не\s+предоставя\s+авансова\s+гаранция',
        'ONLY_PRICE': r'само\s+(?:на\s+база\s+|по\s+|на\s+)?(?:предложена\w*\s+)?цен',
        'UPPER_BOUND': r'по-малк\w*\s+или\s+равн|≤|не\s+по-висок|не\s+надвишава',
        'LOWER_BOUND': r'по-голям\w*\s+или\s+равн|≥',
        'CALENDAR_DAYS': r'календарни\s+дни', 'WORKING_DAYS': r'работни\s+дни'}
    for key, pattern in rules.items():
        if re.search(pattern, text, re.I): result.add(key)
    return result


def temporal(text):
    result = set()
    for m in re.finditer(r'\b(след|преди|от)\b\s+([^.;!?]{0,90})', text, re.I):
        for event in ['регистр', 'подпис', 'издава', 'връща', 'усвоява', 'получава', 'изтич']:
            if event in m.group(2).lower(): result.add(('BEFORE' if m.group(1).lower() == 'преди' else 'FROM', event))
    return result


def named_roles(text):
    """Bound familiar payment roles to percentages, not just a bag of values."""
    result = set()
    for role, pattern in [('PENALTY', r'неустойк\w*'), ('PERFORMANCE', r'гаранци\w*\s+за\s+изпълнение'),
                          ('ADVANCE', r'аванс(?:ово\s+плащане|ът)?'), ('FINAL', r'окончателно\s+плащане')]:
        for m in re.finditer(pattern+r'[^.;%]{0,65}?('+NUMBER+r')\s*%', text, re.I):
            result.add((role, value(m.group(1))))
    return result


def technical_codes(text):
    return {m.group().casefold() for m in re.finditer(r'\b[A-Za-z][A-Za-z/-]*\d[A-Za-z0-9./-]*', normalized(text))}


def equipment_counts(text):
    """Keep quantities attached to numbered equipment variants in this MVP."""
    text = re.sub('[„“”"«»]', ' ', text); result = set()
    for m in re.finditer(r'(?:преносим\s+)?компютър\s+(\d+)', text, re.I):
        before = re.search('('+NUMBER+r')\s*бр\.?\s*$', text[max(0,m.start()-40):m.start()], re.I)
        after = re.search(r'^[^;!?]{0,65}?('+NUMBER+r')\s*бр\.?', text[m.end():m.end()+90], re.I)
        amount = before or after
        if amount: result.add((m.group(1),value(amount.group(1))))
    return result


def claim_errors(text, original, scope_ids=()):
    errors = []
    if not numbers(text, scope_ids) <= numbers(original, scope_ids): errors.append('UNSUPPORTED_NUMBER')
    if not quantities(text, scope_ids) <= quantities(original, scope_ids): errors.append('UNIT_OR_COMPARATOR_CHANGED')
    if not dimensions(text) <= dimensions(original): errors.append('DIMENSION_CHANGED')
    if not vat(text) <= vat(original): errors.append('VAT_CHANGED')
    if not named_roles(text) <= named_roles(original): errors.append('NUMERIC_ROLE_CHANGED')
    if not technical_codes(text) <= technical_codes(original): errors.append('TECHNICAL_CODE_CHANGED')
    if not equipment_counts(text) <= equipment_counts(original): errors.append('EQUIPMENT_QUANTITY_CHANGED')
    if any(direction == 'BEFORE' and ('FROM', event) in temporal(original)
           or direction == 'FROM' and ('BEFORE', event) in temporal(original) for direction, event in temporal(text)):
        errors.append('TIME_REFERENCE_CHANGED')
    # Absence is checked over related contributions below, so a source finding
    # can support two different sentences without repeating all its conditions.
    # An explicit reversal in a generated sentence remains forbidden.
    if 'NO_INCREASE' in anchors(original) and re.search(r'(?<!не )\bподлежи\s+на\s+увеличение', text, re.I):
        errors.append('NO_INCREASE_REVERSED')
    if ('NEGATED_REQUIREMENT' in anchors(original) and 'NEGATED_REQUIREMENT' not in anchors(text)
        and 'CONDITIONAL' not in anchors(text) and re.search(r'\b(?:изисква\s+се|се\s+изисква)\b', text, re.I)):
        errors.append('NEGATED_REQUIREMENT_REVERSED')
    return errors


def coverage_errors(text, original, scope_ids=()):
    errors = []
    if not numbers(original, scope_ids) <= numbers(text, scope_ids): errors.append('MATERIAL_NUMBER_OMITTED')
    if not quantities(original, scope_ids) <= quantities(text, scope_ids): errors.append('MATERIAL_QUANTITY_OMITTED')
    if not dimensions(original) <= dimensions(text): errors.append('MATERIAL_DIMENSION_OMITTED')
    if not vat(original) <= vat(text): errors.append('VAT_OMITTED')
    if not anchors(original) <= anchors(text): errors.append('CONDITION_OR_EXCEPTION_OMITTED')
    if not temporal(original) <= temporal(text): errors.append('TIME_REFERENCE_OMITTED')
    if not named_roles(original) <= named_roles(text): errors.append('NUMERIC_ROLE_OMITTED')
    if not technical_codes(original) <= technical_codes(text): errors.append('TECHNICAL_CODE_OMITTED')
    if not equipment_counts(original) <= equipment_counts(text): errors.append('EQUIPMENT_QUANTITY_OMITTED')
    if manufacturer_exception(original) and not manufacturer_exception(text): errors.append('MANUFACTURER_EXCEPTION_CONDITION_OMITTED')
    return errors


def audit(reply, inputs, *, historical=False):
    organization_input(inputs)
    if reply.state != 'complete' or not isinstance(reply.body, bytes) or len(reply.body) > 262144:
        raise SemanticError('EMAIL39_INCOMPLETE_OR_OVERSIZED')
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d: raise ValueError('duplicate')
            d[k] = v
        return d
    try:
        model = old.Synthesis if historical else Synthesis
        data = model.model_validate(json.loads(reply.body, object_pairs_hook=pairs), strict=True).model_dump()
    except (ValueError, UnicodeError, RecursionError): raise SemanticError('EMAIL39_SCHEMA') from None
    if data['input_digest'] != inputs['input_digest']: raise SemanticError('EMAIL39_STALE_INPUT')
    if [s['key'] for s in data['sections']] != [k for k,_ in old.SECTIONS] or data['sections'][-1]['paragraphs']:
        raise SemanticError('EMAIL39_SECTIONS')
    by = {f['qualified_finding_id']:f for f in inputs['findings']}; seen = set(); claims = []
    errors = [{'code': 'INCOMPLETE_CANONICAL_FINDING', **i} for i in finding_issues(inputs['findings'])]
    scope_ids = {inputs['identity']['parent_tender'], *inputs['scope']['selected_lot_ids']}
    for s in data['sections']:
        for p in s['paragraphs']:
            scopes = set()
            for c in p['contributions']:
                ids, text = c['finding_ids'], c['text_bg']
                if len(ids) != len(set(ids)) or not set(ids) <= by.keys(): raise SemanticError('EMAIL39_UNKNOWN_OR_DUPLICATE_REFERENCE')
                scopes.update(old.signature(by[i]) for i in ids)
                if not re.search(r'[А-Яа-я]', text) or re.search(r'https?://|<[^>]+>|SHA-?256|span-|packet-|UNRESOLVED|lot_id', text, re.I):
                    raise SemanticError('EMAIL39_PROSE_OR_METADATA')
                if re.search(r'компанията\s+отговаря|потвърдена\s+приложимост|готов\s+за\s+публикуване|за\s+избраната\s+позиция\s+се\s+изисква', text, re.I):
                    raise SemanticError('EMAIL39_UNSUPPORTED_SCOPE_OR_ELIGIBILITY')
                original = ' '.join(by[i]['statement_bg'] for i in ids)
                errors.extend({'code':e,'finding_ids':ids} for e in claim_errors(text, original, scope_ids))
                claims.append(c); seen.update(ids)
            if len(scopes) != 1: raise SemanticError('EMAIL39_MIXED_SCOPE_OR_STATE')
    if seen != by.keys(): errors.append({'code':'FINDING_OMITTED','ids':sorted(by.keys()-seen)})
    # Related coverage uses exact evidence, an exact shared payment-role/value,
    # or explicit grouping of cited findings in the same lot scope. It never
    # pools the entire email or uses title similarity. Each generated claim
    # still has to support its own values, units, roles and technical codes.
    groups = [{fid} for fid in by]
    from ..semantic.completeness39 import evidence_keys
    changed = True
    while changed:
        changed = False
        for a in range(len(groups)):
            keys = set().union(*(evidence_keys(by[i]) for i in groups[a]))
            roles = set().union(*(named_roles(by[i]['statement_bg']) for i in groups[a]))
            for b in range(a+1, len(groups)):
                other_keys = set().union(*(evidence_keys(by[i]) for i in groups[b]))
                other_roles = set().union(*(named_roles(by[i]['statement_bg']) for i in groups[b]))
                declared_group = any(groups[a].intersection(c['finding_ids']) and groups[b].intersection(c['finding_ids']) for c in claims)
                same_lots = {(by[i]['applicability'],tuple(by[i]['applicable_lot_ids'])) for i in groups[a]|groups[b]}
                if len(same_lots)==1 and (keys.intersection(other_keys) or roles.intersection(other_roles) or declared_group):
                    groups[a] |= groups.pop(b); changed = True; break
            if changed: break
    for group in groups:
        related = [c['text_bg'] for c in claims if group.intersection(c['finding_ids'])]
        original = ' '.join(by[i]['statement_bg'] for i in sorted(group))
        errors.extend({'code':e,'finding_ids':sorted(group)} for e in coverage_errors(' '.join(related), original, scope_ids))
    return {'data':data,'issues':errors,'semantic_review':'REQUIRED_NOT_CERTIFIED','publishable':False}


def validate(reply, inputs):
    review = audit(reply, inputs)
    if review['issues']: raise SemanticError('EMAIL39_REJECTED:'+review['issues'][0]['code'])
    return review['data']
