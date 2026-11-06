"""P40 offline reassessment only; original validator and paid identities stay frozen.

All P39 claim/coverage checks retained. Qualified mixing changes presentation,
never source states, scope, canonical findings or historical outcomes.
"""
import json
import re
from . import synthesis36 as old
from .validation39 import (Synthesis, organization_input, SemanticError, finding_issues, named_roles, anchors)
from .formats40 import claim_errors, coverage_errors

ASSESSMENT_VERSION = 'P61-equivalent-representation-review-v1'

QUALIFICATION = re.compile(
    r'не\s+(?:е|са)\s+(?:установ|потвърд|изясн)|не\s+установява|'
    r'изисква\w*\s+(?:уточн|проверк)|(?:следва|необходимо|важно)\s+.{0,35}(?:провер|изясн|уточн)|'
    r'приоритетна\s+проверка|подлежи\w*\s+на\s+(?:проверк|потвържд)|'
    r'не\s+следва\s+да\s+се\s+извежда|'
    r'(?:е|са)\s+не(?:установ|потвърд|изясн)\w*|'
    r'остава\s+не(?:разрешен|изяснен|установен)\w*|'
    r'\b(?:с|при)\s+неустановен\w*\s+(?:правн\w*\s+)?приложимост', re.I)
PROMOTION = re.compile(
    r'(?<!не )\b(?:е|са)\s+(?:установен|потвърден|доказан|приложим)|'
    r'със\s+сигурност|безусловно\s+приложим|официално\s+съпоставен', re.I)
STOP = {'които','която','което','както','само','себе','това','тези','този','тази',
        'дали','след','преди','между','други','другият','всички','всяка','може','трябва',
        'установено','установява','установяват','потвърдено','потвърждават','необходимо',
        'предоставените','наличния','пакетът','документите','източници','източниците'}


def topics(text):
    words = re.findall(r'[а-яА-Я]{3,}', text.casefold())
    return {re.sub(r'(?:ите|ата|ът|та|то|те)$', '', w)[:4] for w in words if w not in STOP}


def sentences(text):
    return re.split(r'(?<=[.!?])\s+(?=[А-Я])', text)


def denied_claim(prefix):
    """Local denial only, never a disclaimer elsewhere in the paragraph."""
    clause=re.split(r'[;.!?]|\b(?:но|обаче|а|следователно|затова)\b',prefix,flags=re.I)[-1]
    return bool(re.search(r'\b(?:не|няма)\s*$',clause,re.I) or re.search(
        r'\bне\s+(?:е|са)\s+(?:потвърд|доказ|установ)\w*\s*,?\s+че\s+[^,;.!?]{0,100}$',clause,re.I) or re.search(
        r'\bне\s+е\s+(?:потвърдено|доказано)\s+твърдението\s+[„"]\s*[^„“”";.!?]{0,100}$',clause,re.I))


def promotes_certainty(text):
    for match in PROMOTION.finditer(text):
        prefix=text[:match.start()]
        if denied_claim(prefix):continue
        # P55 F08: business establishment, not established legal applicability.
        # Require country-relative syntax, a business subject and predicate end.
        if (re.match(r'(?:е|са)\s+установен',match.group(),re.I)
            and re.search(r'(?:държавата(?:\s+членка)?|държавите(?:\s+членки)?|страната)\s*,?\s+в\s+ко(?:ято|ито)\s+$',prefix,re.I)
            and re.search(r'чуждестранн\w*\s+лица|участниц|предприят|дружеств|стопанск',prefix[-350:],re.I)
            and re.match(r'(?:е|са)\s+установен[а-я]*\s*(?:[,.;]|$)',text[match.start():],re.I)):
            continue
        # Only an explicitly unresolved indirect question in this same clause
        # governs its predicates. A preceding warning or contrast does not.
        clause=re.split(r'[.;!?]|\b(?:но|обаче|а|следователно|затова|защото|който|която|което|които)\b',prefix,flags=re.I)[-1]
        if re.search(r'\bне\s+(?:е|са)\s+(?:установ|потвърд|изясн)\w*\s+'
                     r'(?:дали|кой|коя|кое|кои|кога|как|доколко)\b',clause,re.I):continue
        return True
    return False


def qualification_matches(question, qualification):
    if len(topics(question)&topics(qualification))>=2:return True
    def document_applicability(text):
        return (re.search(r'приложим',text,re.I)
                and re.search(r'методик|редакци|верси|източни|правил',text,re.I))
    if document_applicability(question) and document_applicability(qualification):return True
    # Specific equivalent document-completeness wording, not generic warnings
    # about money, dates or applicability. Both sides need a document subject
    # and an explicit completeness predicate.
    def document_completeness(text):
        return (re.search(r'оферт|документ|откъс',text,re.I)
                and re.search(r'целия\s+комплект|пълн\w*\s+документ|неизчерпател|семантичн\w*\s+покрит',text,re.I))
    return bool(document_completeness(question) and document_completeness(qualification))


def mixed_state_guard(contributions, by, *, finding_claims=None):
    findings = [by[i] for c in contributions for i in c['finding_ids']]
    if len({old.signature(f) for f in findings}) == 1:
        return
    # Different official lot sets/applicability are never pooled by prose.
    if len({(f['applicability'],tuple(f['applicable_lot_ids'])) for f in findings}) != 1:
        raise SemanticError('EMAIL40_MIXED_APPLICABILITY')
    allowed = {('SUPPORTED','STATED'),('NOT_ESTABLISHED','NOT_ESTABLISHED'),('UNRESOLVED','UNRESOLVED')}
    if any((f['verification_state'],f['interpretation_state']) not in allowed for f in findings):
        raise SemanticError('EMAIL40_UNHANDLED_MIXED_STATE')
    for c in contributions:
        uncertain = [by[i] for i in c['finding_ids'] if by[i]['verification_state'] != 'SUPPORTED']
        if not uncertain: continue
        text = c['text_bg']
        if promotes_certainty(text): raise SemanticError('EMAIL40_UNCERTAINTY_PROMOTED')
        qualified = [s for s in sentences(text) if QUALIFICATION.search(s)]
        for f in uncertain:
            questions = [s for s in sentences(f['statement_bg']) if QUALIFICATION.search(s)]
            if not questions: raise SemanticError('EMAIL40_UNCERTAINTY_NOT_LOCALIZED')
            # A split finding still needs a topic-linked qualification locally.
            # Only claims citing this exact canonical finding can complete its
            # other uncertainty topics; another finding/disclaimer cannot.
            if not any(qualification_matches(q,s) for q in questions for s in qualified):
                raise SemanticError('EMAIL40_UNCERTAINTY_QUALIFICATION_MISSING')
            related=qualified if finding_claims is None else [s for claim in finding_claims
                if f['qualified_finding_id'] in claim['finding_ids']
                for s in sentences(claim['text_bg']) if QUALIFICATION.search(s)]
            for q in questions:
                if not any(qualification_matches(q,s) for s in related):
                    raise SemanticError('EMAIL40_UNCERTAINTY_QUALIFICATION_MISSING')


def advance_exemption(text):
    return bool(re.search(r'(?:при\s+отказ\s+от\s+аванс|ако\s+[^.]{0,70}не\s+желае\s+аванс(?:ово\s+плащане)?)'
        r'[^.]{0,160}(?:не\s+се\s+изисква\s+гаранци\w*\s+за\s+(?:него|аванса)|'
        r'отпада\s+задължението(?:\s+му)?\s+(?:за\s+(?:тази\s+)?авансов\w*\s+гаранци|'
        r'за\s+гаранция\s+за\s+аванса|да\s+осигури\s+тази\s+гаранция))',text,re.I))


def referenced_exemption(group, by, claims):
    """Only the same advance-exemption clause, same original document and lot scope.

    This does not lend amounts, dates or other conditions across paragraphs.
    """
    def documents(f):
        return {(e['occurrence'],e['source_sha256'],e['extractor_sha256']) for e in f['validated_evidence']}
    origins=set().union(*(documents(by[i]) for i in group if advance_exemption(by[i]['statement_bg'])))
    scopes={(by[i]['applicability'],tuple(by[i]['applicable_lot_ids'])) for i in group}
    if not origins or len(scopes)!=1:return False
    for c in claims:
        if not advance_exemption(c['text_bg']):continue
        for fid in c['finding_ids']:
            f=by[fid]
            if ((f['applicability'],tuple(f['applicable_lot_ids'])) in scopes
                and documents(f)&origins and advance_exemption(f['statement_bg'])):return True
    return False


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
                if any(not denied_claim(text[:m.start()]) for m in re.finditer(r'компанията\s+отговаря|потвърдена\s+приложимост|готов\s+за\s+публикуване|за\s+избраната\s+позиция\s+се\s+изисква', text, re.I)):
                    raise SemanticError('EMAIL39_UNSUPPORTED_SCOPE_OR_ELIGIBILITY')
                original = ' '.join(by[i]['statement_bg'] for i in ids)
                errors.extend({'code':e,'finding_ids':ids} for e in claim_errors(text, original, scope_ids))
                claims.append(c); seen.update(ids)
    # References and schema are validated before cross-paragraph lookup. Keep
    # semantic failures as audit issues so all remaining safe checks can run;
    # validate() still rejects any issue and never promotes a historical job.
    for s in data['sections']:
        for number,p in enumerate(s['paragraphs'],1):
            try:mixed_state_guard(p['contributions'],by,finding_claims=claims)
            except SemanticError as error:
                errors.append({'code':str(error),'section':s['key'],'paragraph':number})
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
                # Qualified editorial mixing must not create a new cross-state
                # factual coverage pool merely by listing findings together.
                same_states = len({old.signature(by[i]) for i in groups[a]|groups[b]}) == 1
                if len(same_lots)==1 and (keys.intersection(other_keys) or roles.intersection(other_roles) or (declared_group and same_states)):
                    groups[a] |= groups.pop(b); changed = True; break
            if changed: break
    for group in groups:
        related = [c['text_bg'] for c in claims if group.intersection(c['finding_ids'])]
        original = ' '.join(by[i]['statement_bg'] for i in sorted(group))
        text=' '.join(related)
        missing=coverage_errors(text, original, scope_ids)
        if (anchors(original)-anchors(text)=={'NEGATED_REQUIREMENT'}
            and referenced_exemption(group,by,claims)):
            missing=[e for e in missing if e!='CONDITION_OR_EXCEPTION_OMITTED']
        errors.extend({'code':e,'finding_ids':sorted(group)} for e in missing)
    return {'data':data,'issues':errors,'semantic_review':'REQUIRED_NOT_CERTIFIED','publishable':False}


def validate(reply, inputs):
    review = audit(reply, inputs)
    if review['issues']: raise SemanticError('EMAIL40_REJECTED:'+review['issues'][0]['code'])
    return review['data']
