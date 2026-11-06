"""Owned profile transactions implementing the existing Telegram service ports.

No network work or generated classifications. Verified-result publication is a
trusted internal worker boundary, not a customer action or API.
"""

from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import select, insert, update, delete, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..telegram.models import CATEGORIES, REGIONS, Company, ProfileFields, InteractionError
from .database import Database
from .schema import (users, companies, categories, oblasts, company_categories,
                     regions, selections, catalogues, codes, assignments, intents, CATALOGUE_LOCK)

DENIED = 'Действието е невалидно или вече не е достъпно.'
CONFLICT = 'Профилът е променен. Отворете редактирането отново.'
CATEGORY_KEYS = {label: f'CAT_{i:02}' for i, label in enumerate(CATEGORIES, 1)}
REGION_CODES = {label: f'OB_{i:02}' for i, label in enumerate(REGIONS, 1)}


def _text(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise InteractionError('Невалидни или непълни данни за бизнес профила.')
    return value.strip()


def _email(value):
    value = _text(value, 254)
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value):
        raise InteractionError('Въведете валиден имейл адрес.')
    return value


def _regions(values):
    if not isinstance(values, (set, frozenset, tuple, list)) or any(not isinstance(v,str) or v not in REGION_CODES for v in values):
        raise InteractionError('Невалиден район.')
    return frozenset(values)


def validate_profile(fields):
    if not isinstance(fields, ProfileFields):
        raise InteractionError('Невалиден бизнес профил.')
    cats = fields.categories
    if (not isinstance(cats, tuple) or not 1 <= len(cats) <= 2
            or any(not isinstance(c,str) or c not in CATEGORY_KEYS for c in cats) or len(set(cats)) != len(cats)):
        raise InteractionError('Изберете една или две валидни категории.')
    if (not isinstance(fields.activities, tuple) or len(fields.activities) != len(cats)
            or any(not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[0],str) for pair in fields.activities)):
        raise InteractionError('Въведете отделна дейност за всяка категория.')
    activities = dict(fields.activities)
    if set(activities) != set(cats):
        raise InteractionError('Въведете отделна дейност за всяка категория.')
    return ProfileFields(_text(fields.name, 120), _email(fields.email), cats,
                         tuple((c, _text(activities[c], 2000)) for c in cats), _regions(fields.regions))


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _key(value):
    return hashlib.sha256(_text(value, 128).encode()).hexdigest()


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise InteractionError(DENIED) from None


@dataclass(frozen=True, repr=False)
class ClassificationIntent:
    id: str
    company_id: str
    kind: str
    base_category_revision: int
    company_generation: int
    categories: tuple[tuple[str, str], ...]


@dataclass(frozen=True, repr=False)
class VerifiedAssignmentSet:
    catalogue_version: str
    codes: tuple[str, ...]
    classifier_revision: str
    rationale_reference: str


from .identity import RegisteredUsers,freeze_users

class PostgresProfiles:
    def __init__(self, db: Database, allowed_users: frozenset[int]):
        if not isinstance(allowed_users,RegisteredUsers) and (not allowed_users or any(type(x) is not int or not 0 < x < 2**63 for x in allowed_users)):
            raise ValueError('An explicit authorized Telegram user allowlist is required.')
        self.db, self.allowed_users = db, freeze_users(allowed_users)

    def _actor(self, user):
        if type(user) is not int or user not in self.allowed_users:
            raise InteractionError(DENIED)

    def _owner(self, s, user, *, create=False, lock=True):
        # The short per-owner lock also keeps multi-query DTO reads coherent
        # with writes at READ COMMITTED. No provider work runs under this lock.
        self._actor(user)
        if create:
            s.execute(pg_insert(users).values(id=uuid4(), scope=self.db.settings.scope,
                telegram_user_id=user, private_chat_id=user).on_conflict_do_nothing(index_elements=['scope','telegram_user_id']))
        query = select(users).where(users.c.scope == self.db.settings.scope, users.c.telegram_user_id == user)
        row = s.execute(query.with_for_update() if lock else query).mappings().first()
        if row is not None and row['status'] != 'ACTIVE':
            raise InteractionError(DENIED)
        return row

    def _company(self, s, owner, company, *, lock=False, deleted=False):
        if owner is None:
            raise InteractionError(DENIED)
        query = select(companies).where(companies.c.id == _uuid(company), companies.c.owner_user_id == owner['id'])
        row = s.execute(query.with_for_update() if lock else query).mappings().first()
        if row is None or (row['deleted_at'] is not None and not deleted):
            raise InteractionError(DENIED)
        return row

    def _dto(self, s, owner, row):
        activity_rows = s.execute(select(categories.c.label_bg, company_categories.c.activities_text)
            .join(company_categories, categories.c.key == company_categories.c.category_key)
            .where(company_categories.c.company_id == row['id'], company_categories.c.owner_user_id == owner['id'])
            .order_by(categories.c.display_order)).all()
        selected_regions = s.scalars(select(oblasts.c.name_bg).join(regions, oblasts.c.code == regions.c.oblast_code)
            .where(regions.c.company_id == row['id'], regions.c.owner_user_id == owner['id'])).all()
        cpv = tuple(s.scalars(select(assignments.c.code).where(assignments.c.company_id == row['id'],
            assignments.c.owner_user_id == owner['id']).order_by(assignments.c.code)))
        return Company(str(row['id']), owner['telegram_user_id'],
            ProfileFields(row['name'], row['report_email'], tuple(a[0] for a in activity_rows),
                          tuple(tuple(a) for a in activity_rows), frozenset(selected_regions)), cpv,
            {'PENDING_CPV':'pending','READY':'ready','FAILED':'failed'}[row['classification_status']], row['profile_revision'])

    def companies(self, user):
        self._actor(user)
        with self.db.transaction() as s:
            owner = self._owner(s, user)
            if owner is None:
                return ()
            rows = s.execute(select(companies).where(companies.c.owner_user_id == owner['id'], companies.c.deleted_at.is_(None))
                             .order_by(companies.c.created_at, companies.c.id)).mappings().all()
            return tuple(self._dto(s, owner, row) for row in rows)

    def company(self, user, company):
        with self.db.transaction() as s:
            owner = self._owner(s, user)
            return self._dto(s, owner, self._company(s, owner, company))

    def create_company(self, user, fields, *, confirmation_key=None):
        self._actor(user)
        fields, key = validate_profile(fields), _key(confirmation_key)
        candidate = [{'category_key':CATEGORY_KEYS[c], 'activity':a} for c,a in fields.activities]
        digest = _digest([fields.name, fields.email, candidate, sorted(fields.regions)])
        with self.db.transaction() as s:
            owner = self._owner(s, user, create=True, lock=True)
            old = s.execute(select(intents).where(intents.c.owner_user_id == owner['id'], intents.c.kind == 'CPV_CLASSIFY',
                                                 intents.c.request_key_hash == key)).mappings().first()
            if old:
                row = self._company(s, owner, old['company_id'])
                if old['input_digest'] != digest:
                    raise InteractionError('Потвърждението вече е използвано за други данни.')
                return self._dto(s, owner, row)
            company_id = uuid4()
            s.execute(insert(companies).values(id=company_id, owner_user_id=owner['id'], name=fields.name, report_email=fields.email))
            self._write_categories(s, owner['id'], company_id, candidate)
            self._write_regions(s, owner['id'], company_id, fields.regions)
            s.execute(insert(intents).values(id=uuid4(), company_id=company_id, owner_user_id=owner['id'],
                kind='CPV_CLASSIFY', request_key_hash=key, input_digest=digest, base_profile_revision=1,
                base_category_revision=1, company_generation=1, candidate_categories=candidate))
            self._select(s, owner['id'], company_id)
            return self._dto(s, owner, self._company(s, owner, company_id))

    @staticmethod
    def _write_categories(s, owner, company, candidate):
        s.execute(insert(company_categories), [{'company_id':company,'owner_user_id':owner,
            'category_key':v['category_key'],'activities_text':v['activity']} for v in candidate])

    @staticmethod
    def _write_regions(s, owner, company, values):
        if values:
            s.execute(insert(regions), [{'company_id':company,'owner_user_id':owner,'oblast_code':REGION_CODES[r]} for r in values])

    @staticmethod
    def _select(s, owner, company):
        s.execute(pg_insert(selections).values(user_id=owner, company_id=company).on_conflict_do_update(
            index_elements=['user_id'], set_={'company_id':company, 'generation':selections.c.generation + 1,'updated_at':func.now()}))

    def select_company(self, user, company):
        with self.db.transaction() as s:
            owner = self._owner(s, user, lock=True)
            row = self._company(s, owner, company, lock=True)
            self._select(s, owner['id'], row['id'])

    def active_company(self, user):
        with self.db.transaction() as s:
            owner = self._owner(s, user)
            if owner is None:
                return None
            company = s.scalar(select(selections.c.company_id).where(selections.c.user_id == owner['id']))
            return self._dto(s, owner, self._company(s, owner, company)) if company else None

    def edit_fields(self, user, company, *, expected_revision=None, **changes):
        self._actor(user)
        if not changes or set(changes) - {'name','email','regions'}:
            raise InteractionError('Невалидна промяна на профила.')
        values = {k: _text(v,120) if k == 'name' else _email(v) if k == 'email' else _regions(v) for k,v in changes.items()}
        with self.db.transaction() as s:
            owner = self._owner(s, user, lock=True)
            row = self._company(s, owner, company, lock=True)
            if expected_revision is not None and expected_revision != row['profile_revision']:
                raise InteractionError(CONFLICT)
            patch = {'profile_revision':row['profile_revision']+1,'updated_at':func.now()}
            if 'name' in values:
                patch['name'] = values['name']
            if 'email' in values:
                patch['report_email'] = values['email']
            if 'regions' in values:
                s.execute(delete(regions).where(regions.c.company_id == row['id']))
                self._write_regions(s, owner['id'], row['id'], values['regions'])
            s.execute(update(companies).where(companies.c.id == row['id']).values(**patch))

    def reclassify(self, user, company, fields, *, confirmation_key=None, expected_revision=None):
        fields, key = validate_profile(fields), _key(confirmation_key)
        candidate = [{'category_key':CATEGORY_KEYS[c], 'activity':a} for c,a in fields.activities]
        digest = _digest(candidate)
        with self.db.transaction() as s:
            owner = self._owner(s, user, lock=True)
            row = self._company(s, owner, company, lock=True)
            old = s.execute(select(intents).where(intents.c.owner_user_id == owner['id'], intents.c.kind == 'CPV_RECLASSIFY',
                                                 intents.c.request_key_hash == key)).mappings().first()
            if old:
                if old['company_id'] != row['id'] or old['input_digest'] != digest:
                    raise InteractionError(CONFLICT)
                return
            if expected_revision is None or expected_revision != row['profile_revision']:
                raise InteractionError(CONFLICT)
            if row['classification_status'] != 'READY' or self._pending(s, row['id']):
                raise InteractionError('Изчакайте обработката на категориите и дейностите.')
            s.execute(insert(intents).values(id=uuid4(), company_id=row['id'], owner_user_id=owner['id'], kind='CPV_RECLASSIFY',
                request_key_hash=key, input_digest=digest, base_profile_revision=row['profile_revision'],
                base_category_revision=row['category_revision'], company_generation=row['revocation_generation'], candidate_categories=candidate))

    @staticmethod
    def _pending(s, company):
        return s.execute(select(intents).where(intents.c.company_id == company, intents.c.status == 'PENDING')).mappings().first()

    def classification_pending(self, user, company):
        with self.db.transaction() as s:
            owner = self._owner(s,user)
            row = self._company(s,owner,company)
            return self._pending(s,row['id']) is not None

    def classification_result(self, user, company):
        with self.db.transaction() as s:
            owner = self._owner(s,user); row = self._company(s,owner,company)
            if self._pending(s,row['id']):
                return 'pending'
            status = s.scalar(select(intents.c.status).where(intents.c.company_id == row['id'])
                .order_by(intents.c.created_at.desc(), intents.c.id.desc()).limit(1))
            return 'success' if status == 'SUCCEEDED' else 'failed'

    def pending_intent(self, user, company):
        with self.db.transaction() as s:
            owner = self._owner(s,user); row = self._company(s,owner,company)
            pending = self._pending(s,row['id'])
            if not pending:
                return None
            labels = dict(s.execute(select(categories.c.key,categories.c.label_bg)).all())
            return ClassificationIntent(str(pending['id']),str(row['id']),pending['kind'],pending['base_category_revision'],
                pending['company_generation'],tuple((labels[x['category_key']],x['activity']) for x in pending['candidate_categories']))

    def apply_verified_classification(self, user, company, intent_id, result: VerifiedAssignmentSet, *, _session=None):
        """No classifier here. The trusted worker must verify evidence first.

        This method verifies active-catalogue membership and publishes the whole
        assignment set transactionally; it never makes up a code or a default.
        """
        if (not isinstance(result,VerifiedAssignmentSet) or not isinstance(result.codes,tuple)
                or not 1 <= len(result.codes) <= 20
                or any(not isinstance(c,str) or not re.fullmatch(r'[0-9]{8}',c) for c in result.codes)
                or len(set(result.codes)) != len(result.codes)):
            raise InteractionError('Невалиден проверен набор CPV кодове.')
        classifier = _text(result.classifier_revision,64); rationale = _text(result.rationale_reference,128)
        with (nullcontext(_session) if _session is not None else self.db.transaction()) as s:
            owner = self._owner(s,user,lock=True); row = self._company(s,owner,company,lock=True)
            job = self._intent(s,owner,row,intent_id)
            if job['status'] == 'SUCCEEDED':
                return
            self._current_job(row,job)
            # Catalogue row locks require UPDATE privileges even for a reader.
            # Coordinate with admin import without granting catalogue writes to
            # the future worker/runtime role.
            s.execute(text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':CATALOGUE_LOCK})
            active = s.scalar(select(catalogues.c.id).where(catalogues.c.id == result.catalogue_version, catalogues.c.status == 'ACTIVE'))
            members = set(s.scalars(select(codes.c.code).where(codes.c.version_id == active, codes.c.code.in_(result.codes)))) if active else set()
            if members != set(result.codes):
                raise InteractionError('CPV кодовете не са потвърдени в активния официален каталог.')
            if job['kind'] == 'CPV_RECLASSIFY':
                s.execute(delete(company_categories).where(company_categories.c.company_id == row['id']))
                self._write_categories(s,owner['id'],row['id'],job['candidate_categories'])
            s.execute(delete(assignments).where(assignments.c.company_id == row['id']))
            assignment_set = uuid4()
            s.execute(insert(assignments),[{'company_id':row['id'],'owner_user_id':owner['id'],'code':code,
                'catalogue_version_id':active,'classifier_revision':classifier,'assignment_set_id':assignment_set,
                'rationale_reference':rationale} for code in result.codes])
            s.execute(update(companies).where(companies.c.id == row['id']).values(classification_status='READY',
                profile_revision=row['profile_revision']+1, category_revision=row['category_revision']+(job['kind']=='CPV_RECLASSIFY'), updated_at=func.now()))
            self._finish(s,job['id'],'SUCCEEDED')

    def fail_classification(self, user, company, intent_id, *, _session=None):
        with (nullcontext(_session) if _session is not None else self.db.transaction()) as s:
            owner = self._owner(s,user,lock=True); row = self._company(s,owner,company,lock=True)
            job = self._intent(s,owner,row,intent_id)
            if job['status'] == 'FAILED':
                return
            self._current_job(row,job)
            self._finish(s,job['id'],'FAILED')
            if job['kind'] == 'CPV_CLASSIFY':
                s.execute(update(companies).where(companies.c.id == row['id']).values(classification_status='FAILED', updated_at=func.now()))

    @staticmethod
    def _intent(s,owner,row,intent_id):
        job = s.execute(select(intents).where(intents.c.id == _uuid(intent_id),intents.c.company_id == row['id'],
            intents.c.owner_user_id == owner['id']).with_for_update()).mappings().first()
        if not job:
            raise InteractionError(DENIED)
        return job

    @staticmethod
    def _current_job(row,job):
        if (job['status'] != 'PENDING' or job['company_generation'] != row['revocation_generation']
                or job['base_category_revision'] != row['category_revision']):
            raise InteractionError(CONFLICT)

    @staticmethod
    def _finish(s,job,status):
        s.execute(update(intents).where(intents.c.id == job).values(status=status,candidate_categories=None,completed_at=func.now(), processing_state='FINISHED',lease_token=None,lease_until=None,result_checkpoint=None))

    def delete_company(self,user,company):
        with self.db.transaction() as s:
            owner = self._owner(s,user,lock=True); row = self._company(s,owner,company,lock=True,deleted=True)
            if row['deleted_at'] is not None:
                return
            s.execute(delete(selections).where(selections.c.company_id == row['id']))
            from .schema import searches
            for table in (searches,assignments,regions,company_categories):
                s.execute(delete(table).where(table.c.company_id == row['id']))
            s.execute(update(intents).where(intents.c.company_id == row['id'],intents.c.status == 'PENDING')
                .values(status='CANCELLED',candidate_categories=None,completed_at=func.now(), processing_state='FINISHED',lease_token=None,lease_until=None,result_checkpoint=None))
            s.execute(update(companies).where(companies.c.id == row['id']).values(name=None,report_email=None,
                classification_status='REVOKED', deleted_at=func.now(),updated_at=func.now(),
                revocation_generation=row['revocation_generation']+1, profile_revision=row['profile_revision']+1))
