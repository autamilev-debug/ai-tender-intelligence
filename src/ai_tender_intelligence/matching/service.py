from ..persistence.identity import freeze_users
"""Authorized read-only company matching against bounded ingested public data."""
from datetime import date
from uuid import UUID

from sqlalchemy import select, func, cast, Text, or_, text
from ..persistence.profiles import PostgresProfiles
from ..persistence.schema import assignments, users
from ..catalogue.validation import CatalogueReader
from ..discovery.storage import notices
from ..discovery.models import Notice, Lot, SourceTime, validate_notice
from .models import MatchingError, Profile, Observation
from .engine import match

MAX_OBSERVATIONS = 500
MAX_PAYLOAD_BYTES = 32_000_000


def decode_notice(payload):
    try:
        value = dict(payload)
        value['publication'] = SourceTime(**value['publication'])
        decoded = []
        for original in value['lots']:
            lot = dict(original)
            lot['deadline'] = SourceTime(**lot['deadline'])
            for key in ('main_cpv', 'additional_cpv', 'geography'):
                lot[key] = tuple(lot[key])
            decoded.append(Lot(**lot))
        value['lots'] = tuple(decoded)
        for key in ('buyers', 'main_cpv', 'additional_cpv', 'identities', 'relationships'):
            value[key] = tuple(value[key])
        notice = Notice(**value)
        validate_notice(notice)
        return notice
    except (KeyError, TypeError, ValueError):
        raise MatchingError('Stored public metadata is unsupported or inconsistent.') from None


class MatchingService:
    def __init__(self, db, allowed_users):
        self.db = db
        self.profiles = PostgresProfiles(db, freeze_users(allowed_users))

    def context(self, actor, company_id):
        company = self.profiles.company(actor, company_id)  # authorization before catalogue/public reads
        if company.classification != 'ready':
            raise MatchingError('Approved ready company classification is required.')
        catalogue = CatalogueReader(self.db).snapshot()
        with self.db.transaction() as s:
            versions = set(s.scalars(select(assignments.c.catalogue_version_id).where(
                assignments.c.company_id == UUID(company.id), assignments.c.owner_user_id.in_(
                    select(users.c.id).where(users.c.telegram_user_id == actor, users.c.scope == self.db.settings.scope)))))
            if versions != {catalogue.version_id}:
                raise MatchingError('Company assignments and active verified catalogue differ.')
        return company, catalogue

    def evaluate_context(self, actor, company, catalogue, observations, *, now):
        profile = Profile(company.id, company.revision, company.fields.activities, company.cpv, company.fields.regions)
        result = match(profile, observations, catalogue, now=now)
        self.recheck(actor, company)
        return result

    def recheck(self, actor, company):
        current = self.profiles.company(actor, company.id)
        if (current.revision, current.classification, current.cpv, current.fields) != (
                company.revision, company.classification, company.cpv, company.fields):
            raise MatchingError('Company changed during matching; request a fresh result.')

    def for_company(self, actor, company_id, *, now, since: date | None = None):
        company, catalogue = self.context(actor, company_id)
        with self.db.transaction() as s:
            s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            where = []
            if since:
                raw = notices.c.payload['publication']['raw'].astext
                where.append(or_(raw.is_(None), func.left(raw, 10) >= since.isoformat()))
            count, size = s.execute(select(func.count(), func.coalesce(func.sum(func.octet_length(cast(notices.c.payload, Text))), 0))
                                   .select_from(notices).where(*where)).one()
            if count > MAX_OBSERVATIONS or size > MAX_PAYLOAD_BYTES:
                raise MatchingError('Stored candidate pool exceeds bounds; narrow the publication window.')
            rows = s.execute(select(notices).where(*where).order_by(notices.c.source, notices.c.original_id,
                notices.c.version_key, notices.c.sha256)).mappings().all()
        observations = tuple(Observation(str(r['procedure_id']), str(r['id']), r['sha256'],
            decode_notice(r['payload']), r['last_seen'].isoformat()) for r in rows)
        return self.evaluate_context(actor, company, catalogue, observations, now=now)
