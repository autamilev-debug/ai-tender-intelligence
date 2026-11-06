from ..persistence.identity import freeze_users
"""Short owner-locked transactions around foreground discovery; never network under lock."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, insert, update
from ..persistence.profiles import PostgresProfiles
from ..persistence.schema import searches, assignments, catalogues
from ..matching.service import MatchingService
from ..discovery.storage import notices, evidence
from ..telegram.models import InteractionError
from .codec import FORMAT, ERROR, canonical, decode, digest, encode


@dataclass(frozen=True, repr=False)
class StoredSearch:
    id: str
    started_at: str
    completed_at: str
    parameters: dict
    result: object
    historical_snapshot: bool = True
    production_telegram_ready: bool = False
    buyers: dict = field(default_factory=dict)
    source_payloads: dict = field(default_factory=dict)

    def page(self, number):
        return self.result.matches.page(number)


class SearchResults:
    def __init__(self, db, allowed_users, fresh_search=None):
        self.db = db
        self.profiles = PostgresProfiles(db, freeze_users(allowed_users))
        self.fresh_search = fresh_search

    def _state(self, s, actor, company):
        owner = self.profiles._owner(s, actor)
        row = self.profiles._company(s, owner, company, lock=True)
        if row['classification_status'] != 'READY' or self.profiles._pending(s, row['id']):
            raise InteractionError(ERROR)
        rows = s.execute(select(assignments).where(assignments.c.company_id == row['id'],
            assignments.c.owner_user_id == owner['id']).order_by(assignments.c.code)).mappings().all()
        versions = {a['catalogue_version_id'] for a in rows}
        active = set(s.scalars(select(catalogues.c.id).where(catalogues.c.status == 'ACTIVE')))
        if not rows or len(rows) > 20 or versions != active or len(versions) != 1:
            raise InteractionError(ERROR)
        assignment = [{k: str(v) for k, v in a.items()} for a in rows]
        state = dict(profile_revision=row['profile_revision'], company_generation=row['revocation_generation'],
                     assignment_digest=digest({'assignments':assignment,'owner_generation':owner['revocation_generation']}),
                     classification_identity=[{k: a[k] for k in ('code','catalogue_version_id','classifier_revision','assignment_set_id')} for a in assignment],
                     catalogue_version=next(iter(versions)))
        return owner, row, state

    @staticmethod
    def _same(record, state):
        if any(record[k] != v for k, v in state.items()):
            raise InteractionError(ERROR)

    def _record(self, s, owner, company, search_id):
        try:
            sid = UUID(str(search_id))
        except (ValueError, TypeError, AttributeError):
            raise InteractionError(ERROR) from None
        row = s.execute(select(searches).where(searches.c.id == sid, searches.c.owner_user_id == owner['id'],
            searches.c.company_id == company['id'])).mappings().first()
        if row is None:
            raise InteractionError(ERROR)
        return row

    @staticmethod
    def _references(s, result):
        # Check references, not copied source documents. Includes earlier/later lifecycle facts.
        refs = {}
        other = set()
        for o in result.matches.opportunities:
            refs[o.record_id] = o.sha256
            other.update(o.other_source_references)
        for assessment in result.assessments:
            for fact in assessment.facts:
                if fact.record_id in refs and refs[fact.record_id] != fact.sha256:
                    raise InteractionError(ERROR)
                refs[fact.record_id] = fact.sha256
        if refs:
            try:
                ids = [UUID(k) for k in refs]
            except ValueError:
                raise InteractionError(ERROR) from None
            actual = {str(k): h for k,h in s.execute(select(notices.c.id, notices.c.sha256).where(notices.c.id.in_(ids)))}
            if actual != refs:
                raise InteractionError(ERROR)
        if other:
            rows = s.execute(select(notices.c.source, notices.c.original_id, notices.c.sha256, notices.c.payload)
                .where(notices.c.sha256.in_({r[4] for r in other}))).all()
            found = {(a,b,p.get('version'),p.get('official_url'),h) for a,b,h,p in rows}
            if not other.issubset(found):
                raise InteractionError(ERROR)

    def read(self, actor, company, search_id):
        with self.db.transaction() as s:
            owner, current, state = self._state(s, actor, company)
            row = self._record(s, owner, current, search_id)
            self._same(row, state)
            if row['status'] not in {'COMPLETE', 'PARTIAL'} or row['result_sha256'] != digest(row['result']):
                raise InteractionError(ERROR)
            result = decode(row['result'])
            if (result.matches.company_id != str(current['id']) or result.matches.profile_revision != state['profile_revision']
                    or result.matches.catalogue_version != state['catalogue_version']):
                raise InteractionError(ERROR)
            self._references(s, result)
            ids = [UUID(o.record_id) for o in result.matches.opportunities]
            payloads = {str(record): payload for record,payload in
                s.execute(select(notices.c.id,notices.c.payload).where(notices.c.id.in_(ids)))} if ids else {}
            buyers = {record: tuple(payload.get('buyers', ())) for record,payload in payloads.items()}
            # Presentation-only projection from the exact referenced immutable bytes.
            # Never revise normalized stored observations or snapshot hashes.
            from .source_display import estimated_values, procedure_value
            digests = {o.sha256 for o in result.matches.opportunities if o.source == 'TED'}
            raw = dict(s.execute(select(evidence.c.sha256, evidence.c.raw_payload)
                       .where(evidence.c.sha256.in_(digests))).all()) if digests else {}
            for o in result.matches.opportunities:
                if o.record_id in payloads and o.sha256 in raw:
                    values = estimated_values(raw[o.sha256], o.sha256)
                    procedure_amount = procedure_value(raw[o.sha256], o.sha256)
                    if procedure_amount:
                        payloads[o.record_id].setdefault('source_fields', {})['procedure_estimated_value'] = procedure_amount
                    for lot in payloads[o.record_id].get('lots', ()):
                        if lot['id'] in values:
                            lot.setdefault('source_fields', {})['estimated_value'] = values[lot['id']]
            return StoredSearch(str(row['id']), row['started_at'].isoformat(), row['completed_at'].isoformat(),
                                dict(row['parameters']), result, buyers=buyers, source_payloads=payloads)

    def execute(self, actor, company, limits, *, request_key, before_fetch=None):
        if not isinstance(request_key, str) or not 16 <= len(request_key) <= 200 or self.fresh_search is None:
            raise InteractionError(ERROR)
        # Reuse catalogue verification; no external request on invalid owner/profile.
        MatchingService(self.db, self.profiles.allowed_users).context(actor, company)
        parameters = {'format': FORMAT, 'discovery_policy':'TED_ACTIVE_COMPETITION_LATEST_V1', **{k: v.isoformat() if hasattr(v, 'isoformat') else v for k,v in asdict(limits).items()}}
        key_hash = digest({'key': request_key})
        with self.db.transaction() as s:
            owner, current, state = self._state(s, actor, company)
            existing = s.execute(select(searches).where(searches.c.company_id == current['id'],
                searches.c.owner_user_id == owner['id'], searches.c.request_key_hash == key_hash)).mappings().first()
            if existing:
                self._same(existing, state)
                if existing['parameters'] != parameters or existing['status'] not in {'COMPLETE','PARTIAL'}:
                    raise InteractionError(ERROR)  # RUNNING/FAILED never automatically dispatch again.
                search_id = str(existing['id'])
                reused = True
            else:
                search_id = str(uuid4()); reused = False
                s.execute(insert(searches).values(id=UUID(search_id),company_id=current['id'],owner_user_id=owner['id'],
                    request_key_hash=key_hash,parameters=parameters,status='RUNNING',started_at=datetime.now(timezone.utc),**state))
        if reused:
            return self.read(actor, company, search_id)
        try:
            if before_fetch:
                before_fetch()  # validated and durably reserved; no HTTP yet
            result = self.fresh_search.run(actor, company, limits)
            if result.outcome == 'FAILED':
                raise InteractionError('Търсенето не беше завършено поради проблем с източниците. Не е записан успешен празен резултат.')
            payload = encode(result)
            with self.db.transaction() as s:
                owner, current, latest = self._state(s, actor, company)
                self._same(state, latest)
                record = self._record(s, owner, current, search_id)
                if (record['status'] != 'RUNNING' or result.matches.company_id != str(current['id'])
                        or result.matches.profile_revision != latest['profile_revision']
                        or result.matches.catalogue_version != latest['catalogue_version']):
                    raise InteractionError(ERROR)
                self._references(s, result)
                s.execute(update(searches).where(searches.c.id == UUID(search_id)).values(
                    status='PARTIAL' if result.outcome=='PARTIAL' else 'COMPLETE',
                    completed_at=datetime.now(timezone.utc),result=payload,result_sha256=digest(payload)))
            return self.read(actor, company, search_id)
        except Exception:
            # Conditional close only; deletion must not recreate private data or enable replay.
            with self.db.transaction() as s:
                s.execute(update(searches).where(searches.c.id == UUID(search_id),searches.c.status=='RUNNING').values(
                    status='FAILED',completed_at=datetime.now(timezone.utc)))
            raise InteractionError('Търсенето не беше завършено. Не е създаден успешен празен резултат. Използвайте нова заявка за нов опит.') from None
