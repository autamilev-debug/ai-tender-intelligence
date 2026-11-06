"""Short PostgreSQL transactions around an injected provider, never across it."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4, UUID

from sqlalchemy import select, update, insert, func, or_, text

from ..catalogue.validation import CatalogueReader
from ..catalogue.source import CatalogueError
from ..persistence.profiles import PostgresProfiles, VerifiedAssignmentSet
from ..persistence.schema import users, companies, intents, attempts, budget
from .contract import (MODEL, EFFORT, MAX_OUTPUT_TOKENS, DisabledLuna,
    ClassificationError, NotSent, Reply, build_request, validate_response, encoded)
from .prompts import PromptError

# Proposed engineering defaults; CPV counters are independent of analysis entitlements.
LEASE_SECONDS, MAX_DISPATCHES, RETRY_SECONDS = 180, 2, 30
CEILING = Decimal('20')
COST_LOCK = 0x5035434F5354


@dataclass(frozen=True, repr=False)
class Claim:
    intent_id: object
    owner_id: object
    company_id: object
    user: int
    token: object
    fence: int
    checkpoint: object
    categories: tuple


class Worker:
    def __init__(self, db, allowed_users, provider=None, *, price_quote=None, intent_id=None,_owner_experiment=None):
        if intent_id is not None and not isinstance(intent_id,UUID):
            raise ValueError('A trusted UUID intent target is required.')
        self.intent_id=intent_id
        self.db = db
        self.profiles = PostgresProfiles(db,allowed_users)
        self.provider = provider if provider is not None else DisabledLuna()
        if (self.provider.model,self.provider.reasoning_effort)!=(MODEL,EFFORT):
            raise ValueError('Only the approved Luna High configuration is supported.')
        self.price = price_quote
        if _owner_experiment is not None:raise ValueError('Archived evaluation adapters are not application entrypoints.')
        self._owner_experiment=_owner_experiment
        self.last_catalogue_timings = {}

    def _lock(self,s,owner_id,company_id,intent_id,*,skip=False):
        # Match profile mutation lock order, including deferred DB triggers.
        owner=s.execute(select(users).where(users.c.id==owner_id).with_for_update(skip_locked=skip)).mappings().first()
        if owner is None:return None
        company=s.execute(select(companies).where(companies.c.id==company_id,
            companies.c.owner_user_id==owner_id).with_for_update(skip_locked=skip)).mappings().first()
        if company is None:return None
        job=s.execute(select(intents).where(intents.c.id==intent_id,
            intents.c.owner_user_id==owner_id,intents.c.company_id==company_id).with_for_update(skip_locked=skip)).mappings().first()
        return (owner,company,job) if job else None

    def _current(self,owner,company,job):
        return (owner['scope']==self.db.settings.scope and owner['telegram_user_id'] in self.profiles.allowed_users
            and owner['status']=='ACTIVE' and company['deleted_at'] is None
            and job['status']=='PENDING' and job['company_generation']==company['revocation_generation']
            and job['base_category_revision']==company['category_revision'])

    @staticmethod
    def _end_stale(s,company,job):
        PostgresProfiles._finish(s,job['id'],'CANCELLED')
        if company['deleted_at'] is None and company['classification_status']=='PENDING_CPV':
            s.execute(update(companies).where(companies.c.id==company['id']).values(classification_status='FAILED'))

    def claim(self):
        with self.db.transaction() as s:
            candidates=s.execute(select(intents.c.id,intents.c.owner_user_id,intents.c.company_id)
                .join(users,users.c.id==intents.c.owner_user_id)
                .where(intents.c.status=='PENDING', users.c.scope==self.db.settings.scope,
                    True if self.intent_id is None else intents.c.id==self.intent_id,
                    users.c.telegram_user_id.in_(self.profiles.allowed_users),
                    intents.c.processing_state.in_(('QUEUED','CLAIMED','DISPATCHED','CHECKPOINTED','RETRY_WAIT')),
                    or_(intents.c.lease_until.is_(None),intents.c.lease_until<=func.now()),
                    or_(intents.c.next_attempt_at.is_(None),intents.c.next_attempt_at<=func.now()))
                .order_by(intents.c.created_at,intents.c.id).limit(64)).all()
        for candidate in candidates:
            with self.db.transaction() as s:
                locked=self._lock(s,candidate.owner_user_id,candidate.company_id,candidate.id,skip=True)
                if locked is None:continue
                owner,company,job=locked
                now=s.scalar(select(func.now()))
                if (job['status']!='PENDING' or job['processing_state'] not in
                        {'QUEUED','CLAIMED','DISPATCHED','CHECKPOINTED','RETRY_WAIT'}
                        or (job['lease_until'] and job['lease_until']>now)
                        or (job['next_attempt_at'] and job['next_attempt_at']>now)):continue
                if not self._current(owner,company,job):
                    self._end_stale(s,company,job);continue
                if job['processing_state']=='DISPATCHED':
                    latest=s.execute(select(attempts.c.status).where(attempts.c.intent_id==job['id'])
                        .order_by(attempts.c.number.desc()).limit(1)).scalar_one_or_none()
                    if latest=='FAILED_KNOWN':
                        # Crash after recording guaranteed non-dispatch but
                        # before moving the intent to retry/terminal state.
                        if job['dispatch_count']>=MAX_DISPATCHES:
                            self.profiles.fail_classification(owner['telegram_user_id'],company['id'],job['id'],_session=s)
                        else:
                            self._patch(s,job['id'],processing_state='RETRY_WAIT',last_error='PROVIDER_NOT_SENT',
                                lease_token=None,lease_until=None,next_attempt_at=now+timedelta(seconds=RETRY_SECONDS))
                        continue
                    s.execute(update(attempts).where(attempts.c.intent_id==job['id'],attempts.c.status=='RESERVED').values(status='UNKNOWN'))
                    self._patch(s,job['id'],processing_state='UNCERTAIN',last_error='OUTCOME_UNKNOWN',lease_token=None,lease_until=None)
                    continue
                token=uuid4(); fence=job['fence']+1
                self._patch(s,job['id'],lease_token=token,fence=fence,lease_until=now+timedelta(seconds=LEASE_SECONDS),
                    processing_state='CHECKPOINTED' if job['result_checkpoint'] else 'CLAIMED',next_attempt_at=None)
                return Claim(job['id'],owner['id'],company['id'],owner['telegram_user_id'],token,fence,
                    job['result_checkpoint'],tuple(job['candidate_categories']))
        return None

    @staticmethod
    def _patch(s,intent_id,**patch):
        s.execute(update(intents).where(intents.c.id==intent_id).values(**patch))

    @contextmanager
    def _fenced(self,claim):
        with self.db.transaction() as s:
            locked=self._lock(s,claim.owner_id,claim.company_id,claim.intent_id)
            if locked is None:
                yield s,None;return
            owner,company,job=locked
            now=s.scalar(select(func.now()))
            if (not self._current(owner,company,job) or job['lease_token']!=claim.token
                    or job['fence']!=claim.fence or job['lease_until'] is None or job['lease_until']<=now):
                yield s,None;return
            yield s,job

    def _block(self,claim,reason):
        with self._fenced(claim) as (s,job):
            if job:self._patch(s,claim.intent_id,processing_state='BLOCKED',last_error=reason,lease_token=None,lease_until=None)

    def retry_blocked(self,intent_id):
        """Explicit operator retry after correcting configuration; no uncertain replay."""
        with self.db.transaction() as s:
            row=s.execute(select(intents).where(intents.c.id==intent_id)).mappings().first()
            if not row:return False
            locked=self._lock(s,row['owner_user_id'],row['company_id'],row['id'])
            owner,company,job=locked
            if not self._current(owner,company,job) or job['processing_state']!='BLOCKED':return False
            self._patch(s,job['id'],processing_state='QUEUED',last_error=None)
            return True

    def _reserve(self,claim,request):
        with self._fenced(claim) as (s,job):
            if job is None:return None
            if job['dispatch_count']>=MAX_DISPATCHES:
                self.profiles.fail_classification(claim.user,claim.company_id,claim.intent_id,_session=s)
                return None
            maximum=Decimal(0); basis='MOCK_NO_CHARGE'
            # One byte/token upper estimate plus schema/instruction allowance.
            input_bound=len(encoded(request.responses_payload()))+4096
            if self._owner_experiment is not None:
                self._owner_experiment.verify_owner_reservation(s,claim,request)
                # Frozen schema requires a nonnegative reservation. Zero means
                # no EUR amount reserved, NOT free/unbilled: cost_basis marks
                # UNKNOWN EUR, estimated/actual EUR stay NULL, usage is recorded.
                # No synthetic funding, EUR rate, threshold or new ledger.
                basis='OWNER_EXPERIMENT_EUR_UNKNOWN'
            elif self.provider.paid:
                # Advisory lock needs no UPDATE grant on administrative budget
                # evidence. Budget writers must use this same short lock.
                s.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':COST_LOCK})
                state=s.execute(select(budget).where(budget.c.id==1)).mappings().first()
                now=s.scalar(select(func.now())); quote=self.price
                # A structural zero in the owner experiment is not evidence
                # of zero liability. General paid work must not count an
                # unpriced real experiment as free; owner path above is exempt.
                unpriced_owner=s.scalar(select(func.count()).select_from(attempts).where(
                    attempts.c.is_mock.is_(False),attempts.c.cost_basis=='OWNER_EXPERIMENT_EUR_UNKNOWN',
                    attempts.c.actual_eur.is_(None),attempts.c.estimated_eur.is_(None)))
                quote_ok=(quote is not None and all(isinstance(v,Decimal) and v.is_finite() and v>=0
                    for v in (quote.input_eur_per_million,quote.output_eur_per_million))
                    and isinstance(quote.evidence_reference,str) and 1<=len(quote.evidence_reference)<=128
                    and quote.valid_until is not None and quote.valid_until>now)
                if (unpriced_owner or state is None or not state['paid_authorized'] or state['prior_attributable_eur'] is None
                        or state['other_reserved_eur'] is None or state['prepaid_coverage_eur'] is None or not state['evidence_reference']
                        or not state['valid_until'] or state['valid_until']<=now or not quote_ok):
                    self._patch(s,job['id'],processing_state='BLOCKED',last_error='BUDGET_UNAVAILABLE',lease_token=None,lease_until=None)
                    return None
                maximum=quote.cost(input_bound,MAX_OUTPUT_TOKENS)
                # Retain the greater of reservation/usage estimate for in-flight,
                # uncertain or unpriced calls. Mock-only records consume no real money.
                liability=s.scalar(select(func.coalesce(func.sum(func.coalesce(attempts.c.actual_eur,
                    attempts.c.estimated_eur,attempts.c.reserved_eur)),0)).where(attempts.c.is_mock.is_(False)))
                unfunded=max(Decimal(0),liability+maximum-state['prepaid_coverage_eur'])
                if state['prior_attributable_eur']+state['other_reserved_eur']+unfunded>CEILING:
                    self._patch(s,job['id'],processing_state='BLOCKED',last_error='BUDGET_EXHAUSTED',lease_token=None,lease_until=None)
                    return None
                if unfunded:
                    # No new top-up/credit/card spending is implicitly authorized.
                    self._patch(s,job['id'],processing_state='BLOCKED',last_error='PREPAID_EXHAUSTED',lease_token=None,lease_until=None)
                    return None
                basis=quote.evidence_reference
            attempt=uuid4();number=job['dispatch_count']+1
            s.execute(insert(attempts).values(id=attempt,intent_id=job['id'],number=number,model=MODEL,
                reasoning_effort=EFFORT,is_mock=self.provider.is_mock,status='RESERVED',request_digest=request.digest,
                prompt_version=request.prompts.version,prompt_sha256=request.prompts.sha256,
                reserved_eur=maximum,cost_basis=basis))
            self._patch(s,job['id'],processing_state='DISPATCHED',dispatch_count=number)
            return attempt,input_bound

    def _settle(self,claim,attempt,reply,status,input_bound):
        # Independent cost evidence survives fencing/deletion; no private payload.
        usage_ok=(isinstance(reply,Reply) and type(reply.input_tokens) is int and type(reply.output_tokens) is int
                  and 0<=reply.input_tokens<=input_bound and 0<=reply.output_tokens<=MAX_OUTPUT_TOKENS)
        with self.db.transaction() as s:
            patch={'status':status}
            if usage_ok:
                patch.update(input_tokens=reply.input_tokens,output_tokens=reply.output_tokens)
                if self.provider.paid and self.price:
                    patch['estimated_eur']=self.price.cost(reply.input_tokens,reply.output_tokens)
            if not self.provider.paid or status=='FAILED_KNOWN':patch['estimated_eur']=Decimal(0)
            s.execute(update(attempts).where(attempts.c.id==attempt,attempts.c.status.in_(('RESERVED','UNKNOWN'))).values(**patch))

    def _fail(self,claim,reason,*,retry=False,unknown=False):
        with self._fenced(claim) as (s,job):
            if job is None:return
            if unknown:
                self._patch(s,job['id'],processing_state='UNCERTAIN',last_error=reason,lease_token=None,lease_until=None)
            elif retry and job['dispatch_count']<MAX_DISPATCHES:
                now=s.scalar(select(func.now()))
                self._patch(s,job['id'],processing_state='RETRY_WAIT',last_error=reason,
                    next_attempt_at=now+timedelta(seconds=RETRY_SECONDS),lease_token=None,lease_until=None)
            else:
                self.profiles.fail_classification(claim.user,claim.company_id,claim.intent_id,_session=s)
                self._patch(s,job['id'],last_error=reason)

    def _publish(self,claim):
        with self._fenced(claim) as (s,job):
            if job is None or job['processing_state']!='CHECKPOINTED':return 'stale'
            checkpoint=job['result_checkpoint']
            self.profiles.apply_verified_classification(claim.user,claim.company_id,claim.intent_id,
                VerifiedAssignmentSet(checkpoint['catalogue_version'],tuple(checkpoint['codes']),
                    checkpoint['evidence']['schema_version'],
                    'classification-intent:'+str(claim.intent_id)),_session=s)
            return 'succeeded'

    def run_once(self):
        claim=self.claim()
        if claim is None:return 'idle'
        if claim.checkpoint:return self._publish(claim)
        if not self.provider.enabled:
            self._block(claim,'PROVIDER_DISABLED');return 'blocked'
        try:
            snapshot=CatalogueReader(self.db).snapshot(self.last_catalogue_timings)
            request=build_request(snapshot,claim.categories)
        except CatalogueError:
            self._block(claim,'CATALOGUE_UNAVAILABLE');return 'blocked'
        except PromptError as exc:
            self._block(claim,exc.reason);return 'blocked'
        except ClassificationError as exc:
            self._fail(claim,exc.reason);return 'failed'
        reservation=self._reserve(claim,request)
        if reservation is None:return 'not_dispatched'
        attempt,input_bound=reservation
        # All transactions and catalogue advisory locks have ended here.
        try:
            reply=self.provider.classify(request)
        except PromptError as exc:
            self._settle(claim,attempt,None,'FAILED_KNOWN',input_bound)
            self._block(claim,exc.reason);return 'blocked'
        except NotSent:
            self._settle(claim,attempt,None,'FAILED_KNOWN',input_bound)
            self._fail(claim,'PROVIDER_NOT_SENT',retry=True);return 'retry'
        except Exception:
            # Includes timeouts: a billable outcome cannot safely be inferred.
            self._settle(claim,attempt,None,'UNKNOWN',input_bound)
            self._fail(claim,'OUTCOME_UNKNOWN',unknown=True);return 'uncertain'
        self._settle(claim,attempt,reply,'SUCCEEDED',input_bound)
        try:
            if not isinstance(reply,Reply):raise ClassificationError('UNSUPPORTED_RESPONSE')
            verified,evidence=validate_response(reply,request,snapshot)
        except ClassificationError as exc:
            self._fail(claim,exc.reason);return 'failed'
        with self._fenced(claim) as (s,job):
            if job is None:return 'stale'
            self._patch(s,job['id'],processing_state='CHECKPOINTED',result_checkpoint={
                'catalogue_version':snapshot.version_id,'codes':list(verified),'evidence':evidence,
                'request_digest':request.digest})
        return self._publish(claim)
