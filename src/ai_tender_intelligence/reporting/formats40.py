"""Only demonstrated P40 representation corrections; no request/provider route."""
import re
from datetime import date
from urllib.parse import urlsplit
from . import validation39 as original
from .validation39 import (value, dimensions, named_roles,
    equipment_counts, manufacturer_exception)

VERSION = 'P61-equivalent-documentary-representations-v1'
NUMBER = original.NUMBER
UNITS = original.UNITS + r'|г\.?\b'
EXPERT_ROLE = r'(?:техническ\w*\s+ръководител\w*|специалист\w*|експерт\w*|длъжностн\w*\s+лице)'
MONTHS = dict(zip(('януари','февруари','март','април','май','юни','юли','август',
                  'септември','октомври','ноември','декември'),range(1,13)))
SMALL = {'един':1,'една':1,'едно':1,'три':3,'четири':4,'пет':5,'шест':6,'десет':10}
DATE = r'\b(\d{1,2})[.](\d{1,2})[.](\d{4})\b'
ACT = r'Наредба\s*№\s*\d+'


def representation(text):
    """Exact forms only; no citation resolution or factual repair."""
    # Only labelled international phone numbers: separators change, digits do not.
    def phone(m):
        digits=re.sub(r'\D','',m[2])
        return m[1]+'+'+digits if 10<=len(digits)<=15 else m[0]
    text=re.sub(r'(\b(?:телефон|тел\.)\s*:?\s*)(\+\d(?:[ \u00a0\u202f-]*\d){9,14})(?!\d)',phone,text,flags=re.I)
    # Official buyer-profile links are evidence metadata, not technical quantities.
    def buyer_link(m):
        try:url=urlsplit(m[0].rstrip('.,;:)'))
        except ValueError:return m[0]
        return ' ' if (url.scheme=='https' and url.netloc=='app.eop.bg' and not url.query and not url.fragment
                       and re.fullmatch(r'/buyer/[1-9]\d*',url.path)) else m[0]
    text=re.sub(r'https?://[^\s<>"\u201d]+',buyer_link,text)
    text=re.sub(r'\bчл\.\s*\d+[а-я]?(?:\s*,?\s*(?:ал|т)\.\s*\d+[а-я]?)*',' ',text,flags=re.I)
    def calendar(m):
        day,month,year=int(m[1]),int(m[2]),int(m[3])
        try: token=date(year,month,day).strftime('%Y%m%d')
        except ValueError:return m[0]
        return ' дата '+token+' '
    text=re.sub(r'\b(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})\b',
                lambda m:f'{m[1]}.{MONTHS[m[2].lower()]}.{m[3]}',text,flags=re.I)
    text=re.sub(DATE+r'(?:\s+г\.)?',calendar,text)
    units=r'(?:работни\s+|календарни\s+)?(?:дни|години|месеца|брой|броя|експерт\w*|преминавания)\b'
    text=re.sub(r'\b('+'|'.join(SMALL)+r')\s+(?='+units+')',lambda m:str(SMALL[m[1].lower()])+' ',text,flags=re.I)
    text=re.sub(r'\b(\d+)\s*\((един|една|едно|три|четири|пет|шест|десет)\)',
                lambda m:m[1] if int(m[1])==SMALL[m[2].lower()] else m[0],text,flags=re.I)
    text=re.sub(r'\b(\d+)\s+(?:календарни|работни)\s+(?=дни\b)',r'\1 ',text,flags=re.I)
    text=re.sub(r'\bв\s+срок\s+от\s+(\d+)\s+дни\b',r'до \1 дни',text,flags=re.I)
    text=re.sub(r'\b(\d+)\s+дневен\s+срок\b',r'до \1 дни',text,flags=re.I)
    text=re.sub(r'\b(\d+)\s+(?=преминавания\b)',r'\1 бр. ',text,flags=re.I)
    text=re.sub(r'\b(?:равни\s+на\s+или\s+по-малко\s+от|до\s+и\s+включително)\s+', 'до ',text,flags=re.I)
    return text


def normalized(text, scope_ids=()):
    text = original.normalized(representation(text), scope_ids)
    # Explicit documentary coordinates only, never bare decimal amounts/dates.
    text = re.sub(r'(\b(?:точка|т\.)\s*)\d+(?:\.\d+)+(?![\d.,])',r'\1',text,flags=re.I)
    text = re.sub(r'(\bКритерии\s+за\s+подбор\s*)\(\d+\)',r'\1',text,flags=re.I)
    # Small attested vocabulary in quantity context, not pronouns such as
    # "един от участниците" or arbitrary Bulgarian number-word interpretation.
    text = re.sub(r'\b(?:1|един|една|едно)\s+(?='+EXPERT_ROLE+r'\b)','1 бр. ',text,flags=re.I)
    text = re.sub(r'\b(?:един|една|едно)\s+(?=бр(?:ой|оя)?\b)','1 ',text,flags=re.I)
    text = re.sub(r'\bдесет\s+(?=месец(?:а|и)?\b)','10 ',text,flags=re.I)
    text = re.sub(r'\b(?:нулев[а-я]*|нули(?:те)?)\b', '0', text, flags=re.I)
    # A documentary stage label before the submission opening is not a deadline.
    text = re.sub(r'\bза\s+етап\s+\d+\s+(?=начало\s+на\s+подаването)', '', text, flags=re.I)
    # Written and typographic ranges retain both endpoints and upper-bound meaning.
    text = re.sub(r'('+NUMBER+r')\s*[–—-]\s*('+NUMBER+r')(?=\s*(?:мм\b|cm\b))',
                  r'от \1 до \2', text)
    return text


def technical_codes(text):
    def reference(match):
        try:url=urlsplit(match.group().rstrip('.,;:)'))
        except ValueError:return match.group()
        if (url.scheme=='https' and url.netloc=='app.eop.bg' and not url.query and not url.fragment
            and re.fullmatch(r'/(?:today|buyer)/[1-9][0-9]*',url.path)):
            return ' '
        return match.group()
    # Only full, exact official procedure/buyer URLs are documentary metadata. Bare
    # codes, unknown hosts and technical codes next to that URL stay checked.
    return {code.rstrip('.') for code in original.technical_codes(re.sub(r'https?://[^\s<>"\u201d]+',reference,text))}


def vat(text):
    # Explicitly unknown VAT basis is not an assertion of either basis.
    text=re.sub(r'\bне\s+уточнява\s+дали\b[^.;!?]*\bс\s+или\s+без\s+ДДС', ' ',text,flags=re.I)
    return original.vat(re.sub(r'\bбез\s+и\s+с\s+ДДС','без ДДС и с ДДС',text,flags=re.I))


def temporal(text):
    # Quantity/date normalization can shorten clauses and move unrelated words
    # into the legacy bounded event window. Preserve original temporal context.
    # Documentary prose is not a temporal trigger.
    text=re.sub(r'\bот\s+проекта\s+на\s+договор\b','проекта на договор',text,flags=re.I)
    text=re.sub(r'\bслед\s+текста\b[^.;!?]*?(?=изтичане)', ' ',text,flags=re.I)
    text=re.sub(r'\bслед\s+(\d+|три)\s+месеца\b',r'след изтичане на \1 месеца',text,flags=re.I)
    # The two receipt events in clarification rules must never be conflated.
    result=set()
    if re.search(r'разяснен|запитван',text,re.I):
        deadline=r'(?:изтичането\s+на\s+)?(?:крайния\s+)?срок(?:а|ът)?\s+за\s+(?:получаване\s+на\s+)?(?:заявления(?:\s+за\s+участие)?\s+и/или\s+)?оферти'
        for pattern,event in [
            (r'\b(преди|след|от)\s+'+deadline,'OFFER_DEADLINE'),
            (r'\b(преди|след|от)\s+получаването(?:\s+на\s+искането)?','CLARIFICATION_REQUEST_RECEIPT')]:
            def event_match(m):
                result.add(('BEFORE' if m[1].lower()=='преди' else 'FROM',event))
                return ' '
            text=re.sub(pattern,event_match,text,flags=re.I)
    return result|original.temporal(text)


def anchors(text):
    # A denied generalization about other documents is not a no-document rule.
    checked=re.sub(r'не\s+установява,?\s+че\s+при\s+офериране\s+изобщо\s+не\s+се\s+изискват\s+други\s+документи\s+по\s+други\s+условия',' ',text,flags=re.I)
    result=original.anchors(checked)
    if re.search(r'при\s+(?:аванс\s+до|застрахователна\s+форма)|отказ\s+от\s+поискано\s+удължаване',text,re.I):
        result.add('CONDITIONAL')
    extension=(re.search(r'\bкогато\s+(?:крайни\w*\s+)?срок\w*(?:\s+за\s+подаване\s+на\s+оферти)?\s+се\s+удължава',text,re.I)
               or re.search(r'\bпри\s+удължаване\s+на\s+(?:крайни\w*\s+)?срок\w*(?:\s+за\s+(?:подаване\s+на\s+)?(?:оферти|плащане))?\b',text,re.I))
    if extension:
        result.update({'CONDITIONAL','DEADLINE_EXTENSION_CONDITION'})
        if re.search(r'за\s+(?:подаване\s+на\s+)?оферти',extension.group(),re.I):result.add('SUBMISSION_EXTENSION')
    # The attested ten-month no-funding construction is conditional even when
    # expressed without "ако". Retain both its cause and termination effect.
    norm=normalized(text)
    for timed in re.finditer(r'\bслед\s+'+NUMBER+r'\s+месец\w*\s+(?:няма|без)\s+(?:осигурено\s+)?финансиране\b',norm,re.I):
        result.update({'CONDITIONAL','TIMED_NO_FUNDING'})
        clause=re.split(r'[.;!?]',norm[timed.end():],maxsplit=1)[0]
        if re.search(r'договор\w*\s+(?:се\s+счита\s+за\s+прекратен|се\s+прекратява)',clause,re.I):
            result.add('NO_FUNDING_TERMINATION')
    return result


def unit(token):
    t = token.casefold().rstrip('.')
    for pattern, result in [(r'евро|eur','EUR'), (r'лв|bgn','BGN'), (r'процент[а-я]*|%','%'),
                            (r'грама?|g|г','G'), (r'месеца?|месеци','MONTH'), (r'дни|ден','DAY'), (r'бр','COUNT')]:
        if re.fullmatch(pattern,t): return result
    return t.upper()


def numbers(text, scope_ids=()):
    return {value(m.group()) for m in re.finditer(NUMBER, normalized(text, scope_ids))}


def quantities(text, scope_ids=()):
    text = normalized(text, scope_ids); result = set()
    for m in re.finditer(NUMBER, text):
        suffix = text[m.end():]; u = re.match(r'\s*('+UNITS+')', suffix, re.I)
        label = unit(u.group(1)) if u else ('EUR' if text[:m.start()].rstrip().endswith('€') else None)
        if label:
            prefix = text[max(0, m.start()-30):m.start()]
            comparator = None
            if re.search(r'(?:минимум|най-малко|поне)\s*$', prefix, re.I): comparator = 'MIN'
            elif re.search(r'(?:максимум(?:ът)?(?:\s+е)?|не\s+повече\s+от|до)\s*$', prefix, re.I): comparator = 'MAX'
            elif re.search(r'(?:повече\s+от|над)\s*$', prefix, re.I): comparator = 'GT'
            elif re.search(r'(?:по-малко\s+от|под)\s*$', prefix, re.I): comparator = 'LT'
            result.add((value(m.group()), label, comparator))
    return {q for q in result if q[2] is not None or not any(other[:2] == q[:2] and other[2] is not None for other in result)}


def claim_errors(text, original, scope_ids=()):
    errors = []
    if not numbers(text, scope_ids) <= numbers(original, scope_ids): errors.append('UNSUPPORTED_NUMBER')
    if not quantities(text, scope_ids) <= quantities(original, scope_ids): errors.append('UNIT_OR_COMPARATOR_CHANGED')
    if not dimensions(text) <= dimensions(original): errors.append('DIMENSION_CHANGED')
    if not vat(text) <= vat(original): errors.append('VAT_CHANGED')
    if not named_roles(text) <= named_roles(original): errors.append('NUMERIC_ROLE_CHANGED')
    if not technical_codes(text) <= technical_codes(original): errors.append('TECHNICAL_CODE_CHANGED')
    if not equipment_counts(text) <= equipment_counts(original): errors.append('EQUIPMENT_QUANTITY_CHANGED')
    events={'DEADLINE_EXTENSION_CONDITION','SUBMISSION_EXTENSION','TIMED_NO_FUNDING','NO_FUNDING_TERMINATION'}
    if not (anchors(text)&events) <= (anchors(original)&events): errors.append('CONDITION_SUBJECT_CHANGED')
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
    # An edition date in a cited ordinance identifier is not a contractual date.
    # Only omission with the same ordinance number retained is nonmaterial;
    # an asserted changed date remains checked by claim_errors, above.
    def act_date(m):
        act=m[1]
        return act if re.search(re.escape(act)+r'(?!\d)',text,re.I) else m[0]
    material=re.sub(r'('+ACT+r')\s*/\s*'+DATE.replace(r'\b','')+r'\s*г\.',act_date,original,flags=re.I)
    if not numbers(material, scope_ids) <= numbers(text, scope_ids): errors.append('MATERIAL_NUMBER_OMITTED')
    if not quantities(material, scope_ids) <= quantities(text, scope_ids): errors.append('MATERIAL_QUANTITY_OMITTED')
    if not dimensions(original) <= dimensions(text): errors.append('MATERIAL_DIMENSION_OMITTED')
    if not vat(original) <= vat(text): errors.append('VAT_OMITTED')
    if not anchors(original) <= anchors(text): errors.append('CONDITION_OR_EXCEPTION_OMITTED')
    if not temporal(original) <= temporal(text): errors.append('TIME_REFERENCE_OMITTED')
    if not named_roles(original) <= named_roles(text): errors.append('NUMERIC_ROLE_OMITTED')
    if not technical_codes(original) <= technical_codes(text): errors.append('TECHNICAL_CODE_OMITTED')
    if not equipment_counts(original) <= equipment_counts(text): errors.append('EQUIPMENT_QUANTITY_OMITTED')
    if manufacturer_exception(original) and not manufacturer_exception(text): errors.append('MANUFACTURER_EXCEPTION_CONDITION_OMITTED')
    return errors
