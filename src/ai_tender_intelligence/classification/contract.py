"""Bounded Luna High contract. No networking, credentials or production mock."""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
import hashlib
import json
import re
from typing import Protocol

from ..catalogue.source import IDENTIFIER, CatalogueError
from .retrieval import CandidateRetriever,PER_CATEGORY

MODEL, EFFORT, VERSION = 'gpt-6-luna', 'high', 'cpv-classification-v2'
MAX_BYTES, MAX_OUTPUT_TOKENS = 65536, 6000
MAX_REQUEST_BYTES=131072
# Technical defaults, not measured recall/capacity or new product policy.
MAX_EXPLANATION = 1200
RESPONSE_CATALOGUE_ERRORS = frozenset({'INVALID_CODE_SET', 'CODE_NOT_FOUND', 'CHECK_DIGIT_MISMATCH'})


class ClassificationError(ValueError):
    def __init__(self, reason='INVALID_RESPONSE'):
        # Only internal fixed codes may be supplied, never provider text.
        self.reason = reason
        super().__init__(reason)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


@dataclass(frozen=True, repr=False)
class Request:
    catalogue_version: str
    categories: tuple
    candidates: dict
    prompts: object

    def payload(self):
        body = {'schema_version':VERSION,'catalogue_version':self.catalogue_version,
                'categories':list(self.categories),'candidates':self.candidates}
        if len(encoded(body)) > MAX_BYTES:
            raise ClassificationError('INPUT_LIMIT')
        return body

    @property
    def digest(self):
        return hashlib.sha256(encoded(self.responses_payload())).hexdigest()

    def responses_payload(self):
        """Official Responses JSON-schema boundary; no actual SDK/client here."""
        body = {'model':MODEL,'reasoning':{'effort':EFFORT},'store':False,'service_tier':'default',
                'max_output_tokens':MAX_OUTPUT_TOKENS,
                'instructions':self.prompts.instructions,
                'input':encoded(self.payload()).decode('utf-8'),
                'text':{'format':{'type':'json_schema','name':'cpv_classification_v2','strict':True,'schema':RESPONSE_SCHEMA}}}
        if len(encoded(body))>MAX_REQUEST_BYTES:raise ClassificationError('INPUT_LIMIT')
        return body


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STRING = {'type':'string'}
SUPPORT = object_schema({'category_key':STRING,'activity_quote':STRING,'description_quote':STRING,
    'relevance':{'type':'string','enum':['supported','insufficient','contradicted']},
    'relationship_explanation':STRING})
RESPONSE_SCHEMA = object_schema({'schema_version':STRING,'catalogue_version':STRING,
    'category_reviews':{'type':'array','items':object_schema({'category_key':STRING,
        'considered_codes':{'type':'array','items':STRING},'no_match_reason':{'type':['string','null']}})},
    'selections':{'type':'array','items':object_schema({'identifier':STRING,
        'supports':{'type':'array','items':SUPPORT}})}})


def build_request(snapshot, categories):
    if not 1 <= len(categories) <= 2 or len({c['category_key'] for c in categories}) != len(categories):
        raise ClassificationError('INVALID_INPUT')
    candidates = {}
    retriever=CandidateRetriever(snapshot)
    for category in sorted(categories,key=lambda c:c['category_key']):
        candidates[category['category_key']] = [
            {'identifier':e.identifier,'description_bg':e.description_bg}
            for e in retriever.candidates(category['activity'])]
    if not any(candidates.values()):
        raise ClassificationError('NO_CANDIDATES')
    from .prompts import load_prompts
    request = Request(snapshot.version_id,tuple(dict(c) for c in sorted(categories,key=lambda c:c['category_key'])),
                      candidates,load_prompts(RESPONSE_SCHEMA,VERSION))
    request.responses_payload()
    return request


def _keys(value, expected):
    if not isinstance(value,dict) or set(value) != set(expected):
        raise ClassificationError()


def _pairs(pairs):
    result = {}
    for k,v in pairs:
        if k in result:
            raise ClassificationError()
        result[k] = v
    return result


def validate_response(reply, request, snapshot):
    if reply.state != 'complete' or not isinstance(reply.body,bytes) or len(reply.body)>MAX_BYTES:
        raise ClassificationError('UNSUPPORTED_RESPONSE')
    try:
        data = json.loads(reply.body.decode('utf-8'),object_pairs_hook=_pairs)
        _keys(data,('schema_version','catalogue_version','category_reviews','selections'))
        if data['schema_version'] != VERSION or data['catalogue_version'] != snapshot.version_id:
            raise ClassificationError('RESPONSE_VERSION')
        selections, reviews = data['selections'], data['category_reviews']
        if not isinstance(selections,list) or not isinstance(reviews,list):
            raise ClassificationError()
        identifiers = []
        for selection in selections:
            _keys(selection,('identifier','supports'))
            identifier = selection['identifier']
            if not isinstance(identifier,str) or not IDENTIFIER.fullmatch(identifier):
                raise ClassificationError('INVALID_IDENTIFIER')
            identifiers.append(identifier)
        verified = ()
        if identifiers:
            try:
                verified = snapshot.validate_set(identifiers)
            except CatalogueError as exc:
                reason = exc.reason if exc.reason in RESPONSE_CATALOGUE_ERRORS else 'INVALID_RESPONSE'
                raise ClassificationError(reason) from None
        activities = {c['category_key']:c['activity'] for c in request.categories}
        considered, seen = {}, set()
        for review in reviews:
            _keys(review,('category_key','considered_codes','no_match_reason'))
            key, values, reason = review['category_key'], review['considered_codes'], review['no_match_reason']
            if key not in activities or key in considered or not isinstance(values,list) or len(values)>PER_CATEGORY:
                raise ClassificationError('CATEGORY_COVERAGE')
            available = {c['identifier'] for c in request.candidates[key]}
            if any(not isinstance(v,str) or v not in available for v in values) or len(set(values))!=len(values):
                raise ClassificationError('CATEGORY_COVERAGE')
            if reason is not None and (not isinstance(reason,str) or not 1<=len(reason.strip())<=1000):
                raise ClassificationError()
            considered[key] = (set(values),reason)
        if set(considered)!=set(activities):
            raise ClassificationError('CATEGORY_COVERAGE')
        for selection,valid in zip(selections,verified):
            supports = selection['supports']
            if not isinstance(supports,list) or not 1<=len(supports)<=len(activities):
                raise ClassificationError('UNSUPPORTED_ACTIVITY')
            support_keys = set()
            for support in supports:
                _keys(support,('category_key','activity_quote','description_quote','relevance','relationship_explanation'))
                key, aq, dq = support['category_key'],support['activity_quote'],support['description_quote']
                if (key not in activities or key in support_keys or selection['identifier'] not in considered[key][0]
                        or not isinstance(aq,str) or not aq.strip() or aq not in activities[key]
                        or not isinstance(dq,str) or dq != valid.entry.description_bg):
                    raise ClassificationError('UNSUPPORTED_ACTIVITY')
                explanation = support['relationship_explanation']
                # Shape/completeness only: neither a fluent explanation nor an
                # asserted decision proves semantic correctness. Luna owns it.
                if (support['relevance'] != 'supported' or not isinstance(explanation,str)
                        or not 30 <= len(explanation.strip()) <= MAX_EXPLANATION
                        or len(re.findall(r'[^\W\d_]+',explanation,flags=re.UNICODE)) < 5
                        or any(ord(c)<32 or ord(c)==127 for c in explanation)):
                    raise ClassificationError('UNSUPPORTED_RELATIONSHIP')
                support_keys.add(key); seen.add(key)
        for key,(_,reason) in considered.items():
            if (key in seen and reason is not None) or (key not in seen and reason is None):
                raise ClassificationError('CATEGORY_COVERAGE')
        if not selections:
            raise ClassificationError('NO_SUPPORTED_MATCH')
        return tuple(v.entry.code for v in verified), data
    except ClassificationError:
        raise
    except (TypeError,KeyError,UnicodeError,ValueError,RecursionError):
        raise ClassificationError('INVALID_RESPONSE') from None


@dataclass(frozen=True, repr=False)
class Reply:
    body: bytes = b''
    state: str = 'complete'
    input_tokens: int | None = None
    output_tokens: int | None = None


class NotSent(Exception):
    """Provider guarantees no billable dispatch occurred; eligible bounded retry."""


class ClassificationProvider(Protocol):
    enabled: bool
    paid: bool
    is_mock: bool
    model: str
    reasoning_effort: str

    def classify(self, request: Request) -> Reply: ...


@dataclass(frozen=True)
class PriceQuote:
    input_eur_per_million: Decimal
    output_eur_per_million: Decimal
    evidence_reference: str
    valid_until: object

    def __post_init__(self):
        if (any(not isinstance(v,Decimal) or not v.is_finite() or v<0
                for v in (self.input_eur_per_million,self.output_eur_per_million))
                or not isinstance(self.evidence_reference,str) or not 1<=len(self.evidence_reference)<=128
                or not isinstance(self.valid_until,datetime) or self.valid_until.tzinfo is None):
            raise ValueError('A dated, finite, nonnegative EUR price quote is required.')

    def cost(self, input_tokens, output_tokens):
        return (self.input_eur_per_million*input_tokens+self.output_eur_per_million*output_tokens)/Decimal(1_000_000)


class DisabledLuna:
    """Safe no-dispatch default for direct internal worker construction."""
    enabled, paid, is_mock = False, True, False
    model, reasoning_effort = MODEL, EFFORT

    def classify(self, request):
        raise NotSent('PROVIDER_DISABLED')
