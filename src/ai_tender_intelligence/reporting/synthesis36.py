"""Offline opt-in email contract. No provider, release, delivery or production route."""
from ai_tender_intelligence.assets import root as asset_root
import json
import re
from copy import deepcopy
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..classification.contract import Reply
from ..extraction.models import canonical
from ..semantic.contract import SemanticError, Strict
from ..semantic.retrieval import digest
from . import terra33
from .contract import organization_input
from .render import DISCLAIMER, safe_url, stamp

VERSION = 'terra-bulgarian-email-v2-experimental'
MODEL = 'gpt-6.1-terra'
EFFORT = 'medium'
PROMPT = asset_root() / 'prompts/terra_bulgarian_email_v2.md'
PROMPT_SHA256 = '320f24b880dae86841d4fb13381d300067fc78209ef8b08c33eebe34115a7274'
SECTIONS = (('OVERVIEW', 'Поръчката накратко'), ('TECHNICAL', 'Технически изисквания'),
            ('PARTICIPATION', 'Участие и документи'), ('FINANCE_CONTRACT', 'Финансови и договорни условия'),
            ('DATES', 'Критични дати и срокове'), ('RISKS', 'Рискове и въпроси за проверка'),
            ('SOURCES', 'Източници и период на събиране'))
ALLOWED_SECTIONS = {
    'GENERAL_PROCUREMENT': {'OVERVIEW', 'TECHNICAL', 'RISKS'},
    'TECHNICAL_SPECIFICATIONS': {'TECHNICAL', 'DATES', 'RISKS'},
    'PARTICIPATION_QUALIFICATION': {'PARTICIPATION', 'RISKS'},
    'SUBMISSION_DEADLINES': {'DATES', 'PARTICIPATION', 'RISKS'},
    'EVALUATION_METHODOLOGY': {'RISKS', 'DATES'},
    'FINANCIAL_CONDITIONS': {'FINANCE_CONTRACT', 'RISKS'},
    'CONTRACT_OBLIGATIONS': {'FINANCE_CONTRACT', 'DATES', 'RISKS'},
    'CLARIFICATIONS_AMENDMENTS': {'RISKS', 'DATES', 'PARTICIPATION', 'TECHNICAL'},
}


class Contribution(Strict):
    text_bg: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)


class Paragraph(Strict):
    contributions: list[Contribution] = Field(min_length=1)


class Section(Strict):
    key: Literal[tuple(k for k, _ in SECTIONS)]
    paragraphs: list[Paragraph]


class Synthesis(Strict):
    schema_revision: Literal['terra-bulgarian-email-v2-experimental']
    input_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    model: Literal['gpt-6.1-terra']
    reasoning_effort: Literal['medium']
    language: Literal['bg']
    sections: list[Section] = Field(min_length=7, max_length=7)


def instructions():
    raw = PROMPT.read_bytes()
    if sha256(raw).hexdigest() != PROMPT_SHA256:
        raise SemanticError('EMAIL36_PROMPT_CHANGED')
    return raw.decode('utf-8')


def request(inputs):
    view = organization_input(inputs)  # Exact canonical digest, not access authority.
    view['organization_input_revision'] = VERSION
    view['sections'] = [k for k, _ in SECTIONS]
    body = {'model': MODEL, 'reasoning': {'effort': EFFORT}, 'store': False,
            'service_tier': 'default', 'instructions': instructions(),
            'input': canonical(view).decode('utf-8'),
            'text': {'format': {'type': 'json_schema', 'name': 'bulgarian_email_v2',
                                'strict': True, 'schema': Synthesis.model_json_schema()}}}
    if len(canonical(body)) > 524288:
        raise SemanticError('EMAIL36_REQUEST_RESOURCE_BOUND')
    return body


def signature(finding):
    return tuple(canonical(finding[k]) for k in ('applicability', 'applicable_lot_ids',
                 'verification_state', 'interpretation_state'))


def numeric_contexts(text):
    # Preserve ordered adjacent numeric expressions and attached units/standards.
    # This is deliberately conservative, not an entailment or completeness proof.
    return set(re.findall(r'\d+(?:[.,]\d+)*(?:\s*[×/–-]\s*\d+(?:[.,]\d+)*)*'
                          r'(?:\s*(?:%|мм/с|мм|месеца|дни|бр\.|ppm|g\b|GB\b|GHz\b|MHz\b|VAC\b|VA\b|W\b|EUR\b))?', text))


def guards(text, original):
    if not terra33.numbers(text) <= terra33.numbers(original):
        raise SemanticError('EMAIL36_UNSUPPORTED_NUMBER')
    if not terra33.numbers(original) <= terra33.numbers(text):
        raise SemanticError('EMAIL36_NUMBER_OMITTED')
    if not numeric_contexts(original) <= numeric_contexts(text):
        raise SemanticError('EMAIL36_NUMERIC_CONTEXT_CHANGED')
    for trigger, required, name in terra33.QUALIFIERS:
        if re.search(trigger, original, re.I) and not re.search(required, text, re.I):
            raise SemanticError('EMAIL36_QUALIFIER_' + name)
    for trigger in (r'с\s+(?:включен\s+)?ДДС', r'не\s+се\s+изисква', r'не\s+подлежи',
                    r'само\s+на', r'\bслед\b', r'\bпреди\b', r'календарни', r'работни'):
        if re.search(trigger, original, re.I) and not re.search(trigger, text, re.I):
            raise SemanticError('EMAIL36_CONDITION_ANCHOR_OMITTED')


def validate(reply, inputs):
    organization_input(inputs)
    if reply.state != 'complete' or not isinstance(reply.body, bytes) or len(reply.body) > 262144:
        raise SemanticError('EMAIL36_INCOMPLETE_OR_OVERSIZED')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        data = Synthesis.model_validate(json.loads(reply.body, object_pairs_hook=unique_pairs), strict=True).model_dump()
    except (ValueError, UnicodeError, RecursionError):
        raise SemanticError('EMAIL36_SCHEMA') from None
    if data['input_digest'] != inputs['input_digest']:
        raise SemanticError('EMAIL36_STALE_INPUT')
    if [s['key'] for s in data['sections']] != [k for k, _ in SECTIONS] or data['sections'][-1]['paragraphs']:
        raise SemanticError('EMAIL36_SECTIONS')
    by_id = {f['qualified_finding_id']: f for f in inputs['findings']}
    seen = set()
    for section in data['sections']:
        for paragraph in section['paragraphs']:
            scopes = set()
            for claim in paragraph['contributions']:
                ids, text = claim['finding_ids'], claim['text_bg']
                if len(ids) != len(set(ids)) or not set(ids) <= by_id.keys() or seen.intersection(ids):
                    raise SemanticError('EMAIL36_UNKNOWN_OR_REPEATED_FINDING')
                if any(section['key'] not in ALLOWED_SECTIONS[by_id[i]['category']] for i in ids):
                    raise SemanticError('EMAIL36_CATEGORY_MISPLACED')
                scopes.update(signature(by_id[i]) for i in ids)
                if not re.search(r'[А-Яа-я]', text) or re.search(r'https?://|<[^>]+>|SHA-?256|span-|packet-|UNRESOLVED|lot_id|qualified_finding', text, re.I):
                    raise SemanticError('EMAIL36_PROSE_OR_METADATA')
                if re.search(r'компанията\s+отговаря|гарантира\w*\s+(?:участие|пълнота)|потвърдена\s+приложимост|готов\s+за\s+публикуване|за\s+избраната\s+позиция\s+се\s+изисква', text, re.I):
                    raise SemanticError('EMAIL36_UNSUPPORTED_ELIGIBILITY_OR_SCOPE')
                # Per-contribution checks cannot borrow values from unrelated paragraphs.
                original = ' '.join(by_id[i]['statement_bg'] for i in ids)
                guards(text, original)
                seen.update(ids)
            if len(scopes) != 1:
                raise SemanticError('EMAIL36_MIXED_APPLICABILITY_OR_STATE')
    if seen != by_id.keys():
        raise SemanticError('EMAIL36_FINDING_OMITTED')
    return data


def assemble(inputs, reply, *, mode='OFFLINE_EDITORIAL_SAMPLE_NOT_TERRA_OUTPUT'):
    if mode not in {'OFFLINE_EDITORIAL_SAMPLE_NOT_TERRA_OUTPUT', 'MOCK_ONLY', 'LIVE'}:
        raise SemanticError('EMAIL36_EXECUTION_MODE')
    data = validate(reply, inputs)
    if any(not f['validated_evidence'] and not (f['verification_state'] == 'NOT_ESTABLISHED'
           and f['interpretation_state'] == 'NOT_ESTABLISHED' and f['missing_evidence']) for f in inputs['findings']):
        raise SemanticError('EMAIL36_EVIDENCE_MISSING')
    return {**data, 'canonical_input': deepcopy(inputs), 'publishable': False,
            'semantic_review': 'REQUIRED', 'execution_mode': mode,
            'customer_email_count': 1}


def render(report):
    """One HTML/plain-text body; references are local evidence, never model prose."""
    if report['publishable'] is not False or report['semantic_review'] != 'REQUIRED':
        raise SemanticError('EMAIL36_DEVELOPMENT_ONLY')
    inputs = report['canonical_input']
    # Detect caller changes after assembly; never accept an altered output at render.
    validate(Reply(canonical({k: report[k] for k in Synthesis.model_fields})), inputs)
    by_id = {f['qualified_finding_id']: f for f in inputs['findings']}
    refs, evidence_map, anchors = [], {}, {}
    for fid, f in by_id.items():
        evidence_map[fid] = []
        for e in f['validated_evidence']:
            # One compact reference per exact original/extraction, not per quotation.
            key = canonical({k: e.get(k) for k in ('occurrence', 'source_sha256', 'extractor_sha256')})
            if key not in [r[0] for r in refs]:
                refs.append((key, e))
                anchors[key] = []
            loc = e.get('locator') or {}
            anchor = ('стр. ' + str(loc['page'])) if loc.get('page') else 'откъс без установена страница'
            if e.get('table_row') is not None:
                anchor = ('лист ' + str(loc['sheet']) + ', ' if loc.get('sheet') else '') + 'ред ' + str(e['table_row']) + ', колона ' + str(e['table_column'])
            if anchor not in anchors[key]:
                anchors[key].append(anchor)
            evidence_map[fid].append(next(n for n, r in enumerate(refs, 1) if r[0] == key))
    warning = ('РАЗВОЕН ОБРАЗЕЦ — редакторска демонстрация върху действителни запазени констатации; не е резултат от Terra и не се изпраща на клиент. '
               'Приложимостта към избраната позиция и достатъчността на доказателствата не са установени.')
    if report['execution_mode'] in {'MOCK_ONLY', 'LIVE'}:
        warning = ('РАЗВОЕН РЕЗУЛТАТ — ' + ('реален синтез от Terra' if report['execution_mode'] == 'LIVE' else 'симулиран синтез, не реален отговор от Terra')
                   + '. Не се изпраща на клиент. Приложимостта и достатъчността на доказателствата не са установени; нужна е съдържателна оценка.')
        if inputs.get('analysis_provenance'):
            warning += ' Смесен произход: нови констатации от Sol и запазени исторически констатации от Luna; това не е изцяло нов анализ от Sol.'
    html = ['<!doctype html><html lang="bg"><meta charset="utf-8"><title>Анализ на поръчката</title>',
            '<body style="font:16px/1.5 Arial;max-width:850px;margin:24px auto;padding:16px">',
            '<aside>' + escape(warning) + '</aside>']
    plain = [warning]
    all_unresolved = all(f['applicability'] == 'UNRESOLVED' for f in by_id.values())
    for section, (_, title) in zip(report['sections'], SECTIONS):
        html.append('<h2>' + title + '</h2>'); plain.append('\n' + title)
        if section['key'] == 'OVERVIEW':
            text = ('Поръчка ЕОП ' + str(inputs['identity']['parent_tender']) + '; разглеждани официални позиции: '
                    + ', '.join(map(str, inputs['scope']['selected_lot_ids']))
                    + '. Възложител: не е установен в предоставените констатации. '
                    + 'Този образец използва исторически събрани данни; текущият статус не е проверен.')
            html.append('<p>' + escape(text) + '</p>'); plain.append(text)
        for paragraph in section['paragraphs']:
            texts = []
            for c in paragraph['contributions']:
                numbers = sorted({n for fid in c['finding_ids'] for n in evidence_map[fid]})
                texts.append(c['text_bg'] + (' [' + ', '.join(map(str, numbers)) + ']' if numbers else ' (Неустановено в предоставените данни.)'))
            first = by_id[paragraph['contributions'][0]['finding_ids'][0]]
            prefix = 'Относимост за проверка: ' if first['applicability'] == 'UNRESOLVED' and not all_unresolved else ('Други позиции: ' if first['applicability'] == 'UNRELATED_LOTS' else '')
            text = prefix + ' '.join(texts)
            html.append('<p>' + escape(text) + '</p>'); plain.append(text)
        if section['key'] == 'RISKS':
            for a in terra33.attention(inputs):
                if a['kind'] == 'INTERPRETATION_GAP':
                    text = a['text_bg'] + '. Нужна е целева проверка на клаузата; това не е възстановено изискване.'
                    html.append('<p>' + escape(text) + '</p>'); plain.append(text)
            audit = inputs.get('document_coverage') or {}
            incomplete = [d for d in audit.get('review_documents', []) if d.get('incomplete_processing')]
            if incomplete:
                from ..assessment.disclosures import EXPLANATION, ACTION
                text = 'Документи с непълна обработка: ' + '; '.join(d['display_name'] for d in incomplete)
                for text in (text, EXPLANATION, ACTION):
                    html.append('<p>' + escape(text) + '</p>'); plain.append(text)
            unavailable = [d for d in audit.get('review_documents', []) if d.get('unavailable_file')]
            if unavailable:
                text = ('Неполучени източници: ' + '; '.join(d['display_name'] for d in unavailable)
                        + '. Съществеността на липсващото съдържание изисква проверка; липсата не е потвърдено отсъствие на изисквания.')
                html.append('<p>' + escape(text) + '</p>'); plain.append(text)
            if audit.get('confirmed_missing_essential_occurrences'):
                text = 'Липсва съществено доказателство. Завършен клиентски анализ е блокиран.'
                html.append('<p>' + escape(text) + '</p>'); plain.append(text)
    for n, (key, e) in enumerate(refs, 1):
        label = f'[{n}] {e["source"]["filename"]} — ' + '; '.join(anchors[key])
        url = safe_url(e['source'].get('official_reference'))
        html.append('<p>' + ('<a href="' + escape(url, quote=True) + '">' + escape(label) + '</a>' if url else escape(label)) + '</p>')
        plain.append(label + (' — ' + url if url else ' — официална връзка не е установена'))
    for period in inputs['content_provenance'].get('collection_intervals', []):
        text = 'Събиране: ' + stamp(period.get('start')) + ' — ' + stamp(period.get('end'))
        html.append('<p>' + escape(text) + '</p>'); plain.append(text)
    html.append('<p>' + escape(DISCLAIMER) + '</p></body></html>'); plain.append(DISCLAIMER)
    return {'html': '\n'.join(html), 'text': '\n\n'.join(plain)}
