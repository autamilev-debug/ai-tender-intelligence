"""Bounded Bulgarian lexical retrieval, never a company classification.

Language rules contain no CPV codes/category keys. All results are supplied
official entries; no branch expansion or inference of business capabilities.
"""
from collections import Counter
from dataclasses import dataclass,replace
from math import log
import re
import unicodedata
from ..catalogue.validation import ancestor_candidates

RETRIEVAL_VERSION='cpv-bg-retrieval-v2'
PER_CATEGORY=32

# Engineering vocabulary, not an official Bulgarian stemmer or CPV mapping.
# Longest matching family wins. Query and official labels use the same rules.
FAMILIES={
    'счетовод':'счетовод', 'отчетн':'счетовод',
    'уебсайт':'уебсайт','уебдизайн':'уебсайт','сайтов':'уебсайт',
    'фармацевт':'лекарств','медикамент':'лекарств','лекарств':'лекарств',
    'автомобил':'автомобил','автомонтьор':'автомобил',
    'компют':'компют','електронноизчислит':'компют','софтуер':'софтуер','програмир':'разработ',
    'разработ':'разработ','развива':'разработ',
    'поддърж':'поддръж','поддръж':'поддръж','обслужв':'обслужв','ремонт':'ремонт','сервиз':'ремонт','възстанов':'ремонт',
    'изграж':'строит','строит':'строит','строим':'строит',
    'монтира':'монтаж','монтаж':'монтаж','инстали':'монтаж','инсталир':'монтаж',
    'достав':'достав','снабд':'достав','прода':'достав','търговия':'достав','търгув':'достав',
    'почист':'почист','почиств':'почист','измива':'почист','миене':'почист',
    'проекти':'проект','консулт':'консулт','обуч':'обуч',
    'жилищ':'жилищ','сград':'сград','администрат':'администрат','канцелария':'офис','канцеларии':'офис',
    'водопровод':'водопровод','тръбопровод':'тръбопровод','тръб':'тръб',
    'канализац':'канализац','климатиз':'климатиз',
    'видеонаблюд':'видеонаблюд','охран':'охран','транспорт':'транспорт',
    'превоз':'транспорт','куриер':'куриер','превод':'превод',
    'печат':'печат','мебел':'мебел','прозор':'прозор','дограма':'дограма','приготв':'приготв',
}
EXACT={'коли':'автомобил','колите':'автомобил','сайт':'уебсайт',
       'лаптоп':'лаптоп','лаптопи':'лаптоп','лаптопите':'лаптоп',
       'климатици':'климатиз','климатик':'климатиз','cctv':'видеонаблюд'}
STOP={'и','на','за','по','от','до','с','със','в','във','или','както','при','чрез',
      'услуг','работ','дейност','извършв','предлаг','осигуря','включ','свързан',
      'фирм','компан','бизнес','предприят','клиент','малк','наш','друг','само',
      'техничес','синтетич','общ','общо','общи','поръчк','правим'}
OPERATIONS={'разработ','поддръж','обслужв','ремонт','строит','монтаж','достав','почист','проект','приготв'}
WORD=re.compile(r'[a-zа-яѝ0-9]+')
NEGATIVE_SPLIT=re.compile(r'[;.!?]|,|\bно\b',re.IGNORECASE)
FAMILY_GROUPS={}
for _prefix,_canonical in sorted(FAMILIES.items(),key=lambda x:(-len(x[0]),x[0])):
    FAMILY_GROUPS.setdefault(_prefix[:3],[]).append((_prefix,_canonical))


def normalize_word(word):
    word=unicodedata.normalize('NFKC',word).casefold().replace('ѝ','и')
    if word in EXACT:return EXACT[word]
    for prefix,canonical in FAMILY_GROUPS.get(word[:3],()):
        if word.startswith(prefix):return canonical
    for suffix in ('овете','евете','ията','ите','ата','ята','ове','еве','ето','ият','ът','та','те','и','а'):
        if word.endswith(suffix) and len(word)-len(suffix)>=4:
            word=word[:-len(suffix)];break
    return word[:8]


def _words(text):
    return WORD.findall(unicodedata.normalize('NFKC',text).casefold().replace('ѝ','и'))


def _positive_text(text):
    """Limited explicit clause handling, not general Bulgarian negation NLP."""
    parts=[]
    for clause in NEGATIVE_SPLIT.split(text):
        value=clause.strip().casefold()
        if value.startswith('не само '):value=value[8:]
        elif re.match(r'^(не|без)\b',value):continue
        value=re.split(r'\bбез\b|\bне\b',value,maxsplit=1)[0]
        if value:parts.append(value)
    return ' '.join(parts)


def terms(text,cache=None):
    raw=_words(text);normalized=set()
    for word in raw:
        if cache is not None and word in cache:term=cache[word]
        else:
            term=None if word in STOP or any(word.startswith(s) for s in STOP if len(s)>=4) else normalize_word(word)
            if cache is not None:cache[word]=term
        if term:normalized.add(term)
    # Compositional phrases, not company/code special cases. More specific
    # terms supplement their literal words, never manufacture catalogue rows.
    if any(w.startswith('преносим') for w in raw) and any(w.startswith('компют') for w in raw):normalized.add('лаптоп')
    if ('интернет' in raw and any(w.startswith('страниц') for w in raw)) or ('онлайн' in raw and any(w.startswith('магазин') for w in raw)):
        normalized.add('уебсайт')
    if 'вик' in raw:normalized.update(('водопровод','канализац'))
    return frozenset(t for t in normalized if len(t)>=3 and t not in STOP)


def service_label(text):
    value=text.casefold().strip()
    words=_words(value)
    return ('услуги' in words or (bool(words) and words[0]=='работи')
            or (bool(words) and words[0]=='строителни' and 'работи' in words[:5])
            or bool(re.match(r'^(ремонт\b|поддържане\b|поддръжка\b|техническо обслужване\b|обслужване\b|монтаж\b|инсталиране\b|разработване\b|проектиране\b|почистване\b|разпределение\b|водоснабдяване\b|превоз\b|даване под наем\b)',value)))


def query_operations(text):
    """Verbs/verbal nouns only: product adjectives are not service intent."""
    nouns={'ремонт':'ремонт','поддръжка':'поддръж','поддържане':'поддръж',
           'обслужване':'обслужв','разработване':'разработ','програмиране':'разработ',
           'строителство':'строит','изграждане':'строит','монтаж':'монтаж','инсталиране':'монтаж',
           'доставка':'достав','доставки':'достав','снабдяване':'достав','продажба':'достав',
           'търговия':'достав','почистване':'почист','миене':'почист','проектиране':'проект','приготвяне':'приготв'}
    verbs=('ремонтир','сервизир','поддърж','обслужв','разработв','програмир',
           'строим','изгражд','монтира','инсталира','доставя','снабдя','продава',
           'търгува','почиств','измива','проектирам','приготвя')
    words=_words(text);result=set()
    for word in words:
        if word in nouns:result.add(nouns[word])
        elif word.startswith(verbs):result.add(normalize_word(word))
    if 'строителни' in words and 'работи' in words:result.add('строит')
    return frozenset(result)&OPERATIONS


@dataclass(frozen=True)
class RankedCandidate:
    entry: object
    score: float
    matched_terms: tuple
    hierarchy_promoted: bool=False


class CandidateRetriever:
    def __init__(self,snapshot):
        self.version=snapshot.version_id
        self.documents=[];frequencies=Counter();cache={}
        for entry in sorted(snapshot.entries.values(),key=lambda e:e.code):
            tokens=terms(entry.description_bg,cache)
            domain=tokens-OPERATIONS
            self.documents.append((entry,tokens,domain,service_label(entry.description_bg)))
            frequencies.update(domain)
        count=len(self.documents)
        self.idf={term:log(1+(count-n+.5)/(n+.5)) for term,n in frequencies.items()}
        self.documents=[(entry,tokens,domain,kind,sum(self.idf[t] for t in sorted(domain)))
                        for entry,tokens,domain,kind in self.documents]

    def rank(self,activity):
        positive=_positive_text(activity)
        query=terms(positive);domain=(query-OPERATIONS)&self.idf.keys()
        if not domain:return ()
        query_weight=sum(self.idf[t] for t in sorted(domain))
        operations=query_operations(positive)
        required=operations-{'достав'}
        if required&{'ремонт','поддръж','обслужв'}:required=required|{'ремонт','поддръж','обслужв'}
        goods='достав' in operations
        service=bool(operations-{'достав'}) or 'услуги' in _words(positive)
        results=[]
        for entry,tokens,words,is_service,document_weight in self.documents:
            overlap=domain&words
            if not overlap:continue
            # Explicit single-mode supply/repair intent screens the opposite
            # label kind; unknown/mixed wording remains for model judgment.
            if goods and not service and is_service:continue
            if service and not goods and not is_service:continue
            if required and not goods and not (required&tokens):continue
            weight=sum(self.idf[t] for t in sorted(overlap))
            coverage=weight/query_weight
            precision=weight/document_weight if document_weight else 0
            score=weight*(.5+.5*coverage)*(.35+.65*precision)
            score/=1+.12*len(tokens-query)
            if operations:
                matches=operations&tokens
                score*=1+.4*len(matches)/len(operations)
            results.append(RankedCandidate(entry,score,tuple(sorted(overlap))))
        results.sort(key=lambda r:(-r.score,r.entry.code))
        if not results:return ()
        floor=results[0].score*.2
        results=[r for r in results if r.score>=floor]
        # Reserve at most two existing slots for positively matched ancestors
        # of scored hits. Never traverse a whole branch or admit an ancestor
        # absent from this independently scored, kind-compatible result set.
        support=Counter(code for r in results[:PER_CATEGORY] for code in ancestor_candidates(r.entry.code))
        parents=[r for r in results[PER_CATEGORY:] if r.entry.code in support
                 and sum(self.idf[t] for t in r.matched_terms)/query_weight>=.5]
        parents.sort(key=lambda r:(-support[r.entry.code],-r.score,r.entry.code))
        parents=parents[:2]
        if parents:
            first=results[:PER_CATEGORY-len(parents)]
            first.extend(replace(r,hierarchy_promoted=True) for r in parents)
            kept={r.entry.code for r in first}
            results=first+[r for r in results if r.entry.code not in kept]
        return tuple(results)

    def candidates(self,activity):
        return tuple(r.entry for r in self.rank(activity)[:PER_CATEGORY])
