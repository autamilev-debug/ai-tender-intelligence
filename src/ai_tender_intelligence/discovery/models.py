"""Loss-aware interchange contract. Source text never establishes current openness."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from enum import Enum


class Relation(str, Enum):
    UNASSESSED = 'UNASSESSED'
    DIRECT = 'DIRECT'
    RELATED = 'RELATED'
    BROADER = 'BROADER'


# Consumer contract for the subsequent matching ticket, not an implemented ranker.
MAX_DISPLAYED_TENDERS = 15


class DiscoveryError(ValueError):
    """Sanitized metadata failure, never an empty successful search."""


@dataclass(frozen=True)
class SourceTime:
    raw: str | None = None
    instant: str | None = None
    precision: str = 'UNKNOWN'


def source_time(value: str | None) -> SourceTime:
    if value is None or value == '':
        return SourceTime()
    if not isinstance(value, str) or len(value) > 100:
        raise DiscoveryError('Invalid source timestamp.')
    # XML dates can carry an offset but are still dates, never midnight instants.
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:Z|[+-]\d{2}:\d{2})?', value):
        try:
            datetime.strptime(value[:10], '%Y-%m-%d')
        except ValueError:
            raise DiscoveryError('Invalid source date.') from None
        return SourceTime(value, None, 'DATE')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise DiscoveryError('Invalid source timestamp.') from None
    return SourceTime(value, parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None,
                      'INSTANT' if parsed.tzinfo else 'LOCAL_TIME_OFFSET_UNKNOWN')


@dataclass(frozen=True)
class Lot:
    id: str
    title: str | None = None
    description: str | None = None
    main_cpv: tuple[str, ...] = ()
    additional_cpv: tuple[str, ...] = ()
    geography: tuple[str, ...] = ()
    deadline: SourceTime = field(default_factory=SourceTime)
    source_fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Notice:
    source: str
    original_id: str
    version: str | None
    profile: str
    official_url: str
    retrieved_at: str
    title: str | None
    description: str | None
    buyers: tuple[str, ...]
    publication: SourceTime
    lots: tuple[Lot, ...]
    main_cpv: tuple[str, ...] = ()
    additional_cpv: tuple[str, ...] = ()
    identities: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    source_fields: dict = field(default_factory=dict)
    lifecycle: str = 'UNVERIFIED'

    def payload(self):
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryHit:
    notice: Notice
    relation: Relation = Relation.UNASSESSED
    relation_evidence: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.relation, Relation) or (self.relation != Relation.UNASSESSED and not self.relation_evidence):
            raise DiscoveryError('An assessed relationship needs separate matching evidence.')


def validate_notice(notice: Notice):
    if notice.source not in {'TED', 'EOP'} or notice.lifecycle != 'UNVERIFIED':
        raise DiscoveryError('Unsupported source or lifecycle assertion.')
    if not notice.original_id or len(notice.original_id) > 200 or not notice.lots:
        raise DiscoveryError('Missing notice identity or lot scope.')
    if len(notice.lots) > 1000 or len({lot.id for lot in notice.lots}) != len(notice.lots):
        raise DiscoveryError('Invalid lot inventory.')
    if any(not isinstance(l.id, str) or not l.id.strip() or len(l.id) > 200 for l in notice.lots):
        raise DiscoveryError('Invalid lot identifier.')
    if any(not isinstance(b, str) or not b.strip() or len(b) > 10000 for b in notice.buyers):
        raise DiscoveryError('Invalid buyer metadata.')
    if source_time(notice.retrieved_at).precision != 'INSTANT':
        raise DiscoveryError('Retrieval time must identify an instant.')
    for code in notice.main_cpv + notice.additional_cpv + tuple(c for l in notice.lots for c in l.main_cpv + l.additional_cpv):
        if not isinstance(code, str) or not re.fullmatch(r'\d{8}', code):
            raise DiscoveryError('Invalid source CPV representation.')
    if len(json.dumps(notice.payload(), ensure_ascii=False).encode()) > 4_000_000:
        raise DiscoveryError('Notice metadata exceeds limit.')


def content_hash(data: bytes) -> str:
    return sha256(data).hexdigest()
