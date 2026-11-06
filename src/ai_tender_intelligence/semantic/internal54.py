"""Versioned internal documentary view; raw responses/source states never repaired."""
from ai_tender_intelligence.assets import root as asset_root
import json,re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from pydantic import Field
from . import contract_v2 as v2,references34 as aliases,shortrefs37
from .contract import SemanticError
from .retrieval import digest
from .validation import ValidatedBatch,_SEAL,validate as canonical_validate
from .completeness39 import issues
from ..classification.contract import Reply
from ..extraction.models import canonical

VERSION='internal-documentary54-v1';PROMPT_VERSION='sol-internal54-v1'
PROMPT_FILE=asset_root()/'prompts/sol_internal54.md'
def instructions():return shortrefs37.instructions()+'\n'+PROMPT_FILE.read_text(encoding='utf-8')
# Code/prompt bytes are also pinned in the job recipe; old contracts stay intact.
def prompt_hash():return sha256(instructions().encode()).hexdigest()

class Finding(v2.Finding):
    statement_bg:str=Field(min_length=1)
class Verification(v2.Verification):
    reference_registry_sha256:str
    findings:list[Finding]=Field(max_length=12)
class AliasFinding(Finding):
    evidence:list[aliases.AliasReference]=Field(max_length=6)
    source_limitations:list[aliases.AliasLimitation]=Field(max_length=12)
class Wire(Verification):
    findings:list[AliasFinding]=Field(max_length=12)
RESPONSE_SCHEMA=Verification.model_json_schema()

def schema(packet):
    result=Wire.model_json_schema();allowed=[e['alias'] for e in aliases.registry(packet)['entries']]
    for name in ('AliasReference','AliasLimitation'):
        result['$defs'][name]['properties']['citation_id']['enum']=allowed
    if all(s['source']['legal_applicability']=='UNRESOLVED' for s in packet['spans']):
        result['$defs']['AliasFinding']['properties']['applicability']['enum']=['UNRESOLVED','UNRELATED_LOTS']
    return result

def metadata(packet):
    return dict(schema_revision=VERSION,schema_sha256=digest(schema(packet)),prompt_revision=PROMPT_VERSION,
        prompt_sha256=prompt_hash(),model='gpt-6.1-sol',reasoning_effort='medium',
        reference_registry_sha256=digest(aliases.registry(packet)))

def request(packet,documents=()):
    # Reuse exact short-reference serialization for all eight categories.
    body=deepcopy(v2.request(packet));value=json.loads(body['input'])
    lookup={r['citation_id']:r['alias'] for r in aliases.registry(packet)['entries']}
    def wire(x):
        if isinstance(x,list):return [wire(v) for v in x]
        if not isinstance(x,dict):return x
        return {k:lookup[v] if k=='citation_id' else [lookup[i] for i in v]
            if k=='equivalent_same_source_text_citations' else wire(v) for k,v in x.items()}
    value=wire(value);value['verification_metadata']=metadata(packet)
    value['document_inventory']=[{'occurrence':d['occurrence'],'filename':d.get('reference',{}).get('filename'),
        'status':d['status']} for d in documents]
    if packet['category']=='SUBMISSION_DEADLINES':
        from .submission import guide
        value['submission_coverage']=wire(guide(packet))
    body.update(model='gpt-6.1-sol',reasoning={'effort':'medium'},instructions=instructions(),input=canonical(value).decode())
    body['text']['format'].update(name='internal_documentary54_v1',schema=schema(packet))
    if len(canonical(body))>131072:raise SemanticError('INTERNAL54_REQUEST_BOUND')
    return body

PREFIX='Според цитирания източник (приложимостта не е потвърдена): '
SUFFIX=' Това е документално сведение, а не потвърдено приложимо задължение.'
PACKET_GAP='Уточнение за обхвата: твърдението за непредоставено съдържание се отнася само до този извлечен пакет; наличността на документите в колекцията се показва отделно.'

def validate(reply,packet,*,execution_mode='MOCK_ONLY'):
    if reply.state!='complete' or not isinstance(reply.body,bytes) or len(reply.body)>262144:
        raise SemanticError('INTERNAL54_INCOMPLETE_RESPONSE')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise SemanticError('DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    try:data=Wire.model_validate(json.loads(reply.body,object_pairs_hook=pairs),strict=True).model_dump()
    except (ValueError,UnicodeError,RecursionError) as e:raise SemanticError('INTERNAL54_SCHEMA') from e
    if any(data[k]!=v for k,v in metadata(packet).items()):raise SemanticError('INTERNAL54_CONTEXT')
    registry=aliases.registry(packet);by={s['citation_id']:s for s in packet['spans']}
    reverse={r['alias']:r['citation_id'] for r in registry['entries']}
    original=deepcopy(data['findings']);adjustments=[]
    from ..reporting.validation40_offline import promotes_certainty,denied_claim
    for f in data['findings']:
        for ref in [*f['evidence'],*f['source_limitations']]:
            if ref['citation_id'] not in reverse:raise SemanticError('INTERNAL54_UNAUTHORIZED_ALIAS')
            ref['citation_id']=reverse[ref['citation_id']]
        sources=[by[e['citation_id']]['source'] for e in f['evidence']]
        if any(s['parent_tender']!=packet['scope']['parent_tender'] or
            s.get('lot_id') is not None and s['lot_id'] not in packet['scope']['lot_ids'] for s in sources):
            raise SemanticError('INTERNAL54_UNRELATED_SOURCE')
        if f['applicability']=='UNRELATED_LOTS':continue  # Original builder excludes it.
        if sources and any(s['legal_applicability']=='UNRESOLVED' for s in sources):
            text=f['statement_bg']
            if promotes_certainty(text) or any(not denied_claim(text[:m.start()]) for m in re.finditer(r'компанията\s+отговаря|гарантиран\w*\s+участие|за\s+избраната\s+позиция\s+се\s+изисква',text,re.I)):
                raise SemanticError('INTERNAL54_UNSUPPORTED_CERTAINTY')
            # Qualification adds no factual clause; original claims stay in audit.
            f.update(applicability='UNRESOLVED',requires_further_verification=True)
            f['statement_bg']=PREFIX+text+SUFFIX
            if re.search(r'непредоставен\w*\s+(?:им\s+)?съдържани',text,re.I):
                f['statement_bg']+=' '+PACKET_GAP
            adjustments.append(f['finding_id'])
    data.update(schema_sha256=digest(RESPONSE_SCHEMA))
    batch=canonical_validate(Reply(canonical(data)),packet,execution_mode=execution_mode,
        contract_revision=VERSION,evaluation_profile=('gpt-6.1-sol','medium'))
    result=batch.data;excluded=[];retained=[]
    incomplete={i['finding_id']:i for i in issues(result['findings'])}
    from ..reporting.formats40 import numbers,quantities,NUMBER,value as numeric_value
    def explicit_units(text,scope):
        found={(value,unit) for value,unit,_ in quantities(text,scope) if unit}
        # Literal construction units omitted by the historical report tokenizer;
        # never infer units for diameter symbols or unlabeled dimensions.
        for match in re.finditer('(?P<value>'+NUMBER+r')\s*(?P<unit>л(?:итр[а-я]*)?|м[²³]?)(?![а-яА-Я])',text,re.I):
            unit=match['unit'].casefold()
            found.add((numeric_value(match['value']),'L' if unit.startswith('л') else {'м':'M','м²':'M2','м³':'M3'}[unit]))
        return found
    for f,raw in zip(result['findings'],original):
        # Examine raw ending, before the qualification supplies punctuation.
        text=raw['statement_bg'].strip()
        broken=not text.endswith(('.', '!', '?', '…')) or bool(re.search(r'[\u3400-\u9fff\ufffd]',text))
        source=' '.join(e['quote'] for e in f['validated_evidence'])
        # Raw extracted tables have separate labels (e.g. currency/VAT) unlike
        # canonical prose. Do not apply the report's prose-equivalence heuristic
        # to that different representation. Check invented values and explicit
        # same-value units; keep full report claim/condition checks unchanged.
        scope={packet['scope']['parent_tender'],*packet['scope']['lot_ids']}
        if not numbers(text,scope)<=numbers(source,scope):
            raise SemanticError('INTERNAL54_FACTUAL_ERROR:UNSUPPORTED_NUMBER')
        source_units={}
        for value,unit in explicit_units(source,scope):source_units.setdefault(value,set()).add(unit)
        for value,unit in explicit_units(text,scope):
            if unit and value in source_units and unit not in source_units[value]:
                raise SemanticError('INTERNAL54_FACTUAL_ERROR:UNIT_CHANGED')
        problems=[incomplete[f['qualified_finding_id']]] if f['qualified_finding_id'] in incomplete else []
        if broken or problems:
            excluded.append({'finding_id':f['finding_id'],'reason':'UNFINISHED_OR_CORRUPTED_FINDING' if broken else problems[0]['code'],
                'statement_bg':text,'material_coverage':'INCOMPLETE_NOT_RECONSTRUCTED'})
        else:retained.append(f)
    if not retained:raise SemanticError('INTERNAL54_NO_USABLE_FINDINGS')
    result.update(findings=retained,original_model_findings=original,qualified_internal_findings=adjustments,
        excluded_internal_findings=excluded,internal_contract=VERSION,raw_response_sha256=sha256(reply.body).hexdigest(),
        customer_publication_allowed=False)
    return ValidatedBatch(result,_SEAL)

def report_guard(data,inputs):
    if not inputs.get('internal_review54'):return
    beta=inputs.get('beta59')
    if beta and (beta.get('internal_only') is not True or beta.get('customer_publication_allowed') is not False):
        raise SemanticError('BETA59_INTERNAL_ONLY_REQUIRED')
    by={f['qualified_finding_id']:f for f in inputs['findings']}
    if beta:
        from ..reporting.validation40_offline import denied_claim
        for section in data['sections']:
            for paragraph in section['paragraphs']:
                for c in paragraph['contributions']:
                    text=c['text_bg']
                    if any(not denied_claim(text[:m.start()]) for m in re.finditer(
                        r'(?:докладът|анализът|покритието)\s+е\s+(?:пълен|пълно|изчерпателен)|'
                        r'всички\s+(?:съществени\s+)?изисквания\s+са\s+(?:обхванати|проверени)',text,re.I)):
                        raise SemanticError('INTERNAL54_FALSE_COMPLETENESS')
    for section in data['sections']:
        for paragraph in section['paragraphs']:
            for c in paragraph['contributions']:
                if any(by[i]['applicability']=='UNRESOLVED' for i in c['finding_ids']):
                    t=c['text_bg']
                    if not re.search(r'според|източник|посоч|опис|документ|публикаци|решение|указани|проект(?:ът|а)?\s+на\s+договор',t,re.I) or not re.search(
                        r'приложимост.{0,45}(?:не|неустанов|непотвърд)|не.{0,35}потвърд.{0,30}прилож|неустановен.{0,15}приложимост',t,re.I):
                        raise SemanticError('INTERNAL54_REPORT_LOCAL_QUALIFICATION_REQUIRED')
                if (beta or inputs['internal_review54']['excluded_findings']) and re.search(r'всички\s+изисквания\s+са\s+(?:обхванати|проверени)|изчерпателен\s+анализ|пълнотата\s+е\s+потвърдена',c['text_bg'],re.I):
                    raise SemanticError('INTERNAL54_FALSE_COMPLETENESS')
