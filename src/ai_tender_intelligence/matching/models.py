from dataclasses import dataclass
from ..discovery.models import Notice

VERSION = 'company-tender-match-v4'
LABELS = {'DIRECT': 'Пряко съвпадение', 'RELATED': 'Свързана възможност', 'BROADER': 'По-широк сектор'}
DISCLAIMER = 'Резултатите не са проверени за текуща наличност. Съвпадението не установява допустимост или годност за участие.'


class MatchingError(ValueError):
    """Fixed sanitized error, never private input or SQL."""


@dataclass(frozen=True)
class Profile:
    company_id: str
    revision: int
    activities: tuple[tuple[str, str], ...]
    cpv: tuple[str, ...]
    regions: frozenset[str]


@dataclass(frozen=True)
class Observation:
    procedure_id: str
    record_id: str
    sha256: str
    notice: Notice
    last_seen: str


@dataclass(frozen=True)
class Evidence:
    category: str
    company_cpv: str
    tender_cpv: str
    company_label: str
    tender_label: str
    common_parent: str | None
    activity_terms: tuple[str, ...]
    notice_terms: tuple[str, ...]
    rule: str
    explanation_bg: str


@dataclass(frozen=True)
class LotMatch:
    lot_id: str
    title: str | None
    relation: str
    label_bg: str
    evidence: tuple[Evidence, ...]
    geography_kind: str
    overlapping_oblasts: tuple[str, ...]
    deadline_raw: str | None
    deadline_instant: str | None
    expires_within_seven_days: bool
    deadline_warning_bg: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Opportunity:
    procedure_id: str
    source: str
    original_id: str
    source_version: str | None
    source_profile: str
    record_id: str
    sha256: str
    official_url: str
    title: str | None
    publication_raw: str | None
    retrieved_at: str
    last_seen: str
    relation: str
    label_bg: str
    lots: tuple[LotMatch, ...]
    other_source_references: tuple[tuple[str, str, str | None, str, str], ...]
    warnings: tuple[str, ...]
    availability_verified: bool = False


@dataclass(frozen=True)
class MatchResult:
    company_id: str
    profile_revision: int
    as_of: str
    algorithm: str
    catalogue_version: str
    geography_mapping: str
    opportunities: tuple[Opportunity, ...]
    diagnostics: tuple[tuple[str, str], ...]
    disclaimer_bg: str = DISCLAIMER
    availability_verified: bool = False
    production_telegram_ready: bool = False

    def page(self, number):
        if type(number) is not int or number < 1:
            raise MatchingError('Invalid result page.')
        return self.opportunities[(number - 1) * 5:number * 5]
