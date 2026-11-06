"""Bounded public search and explicit as-published lifecycle evidence."""
from dataclasses import dataclass
from datetime import date
from ..matching.models import MatchResult


@dataclass(frozen=True)
class SearchLimits:
    since: date
    until: date
    pages: int = 1
    page_size: int = 10
    discovery_xml: int = 30
    followup_xml: int = 20
    related_procedures: int = 5
    eop_days: int = 3

    def __post_init__(self):
        if self.until < self.since or (self.until - self.since).days > 30:
            raise ValueError('An explicit publication window of at most31 days is required.')
        bounds = {'pages': (1,3), 'page_size': (1,25), 'discovery_xml': (1,60),
                  'followup_xml': (0,40), 'related_procedures': (0,10), 'eop_days': (0,3)}
        if any(type(getattr(self, k)) is not int or not lo <= getattr(self,k) <= hi for k,(lo,hi) in bounds.items()):
            raise ValueError('Unsupported source bounds.')


@dataclass(frozen=True)
class QueryPlan:
    tier: str
    codes: tuple[str, ...]


@dataclass(frozen=True)
class SourceCheck:
    phase: str
    key: str
    outcome: str
    pages: int
    records: int
    reason: str | None = None


@dataclass(frozen=True)
class Fact:
    source: str
    original_id: str
    version: str | None
    record_id: str
    sha256: str
    official_url: str
    publication: str | None
    retrieved_at: str
    path: str
    value: str | None


@dataclass(frozen=True)
class LotAssessment:
    procedure_id: str
    source_lot_id: str
    evidence_state: str
    exclude: bool
    reasons: tuple[str, ...]
    facts: tuple[Fact, ...]
    matching_deadline_withheld: bool = False
    current_openness: str = 'UNVERIFIED'


@dataclass(frozen=True)
class Difference:
    procedure_id: str
    source_lot_id: str
    field: str
    before: str | None
    after: str | None
    before_record: str
    after_record: str
    supported_relationship: bool


@dataclass(frozen=True)
class FreshResult:
    collection_started: str
    collection_finished: str
    publication_since: str
    publication_until: str
    queries: tuple[QueryPlan, ...]
    source_checks: tuple[SourceCheck, ...]
    outcome: str
    incomplete_reasons: tuple[str, ...]
    assessments: tuple[LotAssessment, ...]
    differences: tuple[Difference, ...]
    matches: MatchResult
    matching_views_are_derived: bool = True
    current_openness_verified: bool = False
    production_telegram_ready: bool = False
