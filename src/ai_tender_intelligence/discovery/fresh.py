"""Explicit foreground public discovery -> immutable ingestion -> assessment -> matching."""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func, Text, cast, text
from .fresh_models import QueryPlan, SourceCheck, FreshResult
from .models import DiscoveryError
from .storage import NoticeStore, notices, evidence
from .lifecycle import assess, metadata, fact, tendering
from ..catalogue.validation import ancestor_candidates
from ..matching.service import MatchingService, decode_notice, MAX_OBSERVATIONS, MAX_PAYLOAD_BYTES
from ..matching.models import Profile, Observation, MatchingError
from ..matching.engine import support, ORDER, _deadline, interval_expired
from ..matching.geography import geography


def expansion(codes, catalogue):
    """Query-only expansion; never modifies company assignments or infers relevance."""
    direct = tuple(sorted(set(codes)))
    catalogue.validate_set(direct)
    near, broad = set(), set()
    for code in direct:
        chain = [c for c in ancestor_candidates(code) if c in catalogue.entries]
        if chain:
            nearest = chain[-1]
            if len(nearest.rstrip('0')) >= 4:
                near.add(nearest)
        wider = [c for c in chain if len(c.rstrip('0')) >= 4]
        if wider:
            broad.add(wider[0])
    plans = [QueryPlan('DIRECT', direct)]
    if near - set(direct):
        plans.append(QueryPlan('RELATED', tuple(sorted(near - set(direct)))))
    if broad - near - set(direct):
        plans.append(QueryPlan('BROADER', tuple(sorted(broad - near - set(direct)))))
    return tuple(plans)


def load_pool(db, fresh_ids):
    if not fresh_ids:
        return (), {}
    with db.transaction() as s:
        s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        pids = tuple(s.scalars(select(notices.c.procedure_id).where(notices.c.id.in_(fresh_ids)).distinct()))
        where = notices.c.procedure_id.in_(pids)
        count, size = s.execute(select(func.count(), func.coalesce(func.sum(func.octet_length(cast(notices.c.payload, Text))),0)).where(where)).one()
        if count > MAX_OBSERVATIONS or size > MAX_PAYLOAD_BYTES:
            raise MatchingError('Related source history exceeds bounds; narrow the search window.')
        rows = s.execute(select(notices).where(where)).mappings().all()
        digests = {r['sha256'] for r in rows if r['source'] == 'TED'}
        total = s.scalar(select(func.coalesce(func.sum(func.octet_length(evidence.c.raw_payload)),0)).where(evidence.c.sha256.in_(digests)))
        if total > 64_000_000:
            raise MatchingError('Related XML evidence exceeds bounds.')
        raw = dict(s.execute(select(evidence.c.sha256,evidence.c.raw_payload).where(evidence.c.sha256.in_(digests))).all())
    observations = tuple(Observation(str(r['procedure_id']),str(r['id']),r['sha256'],decode_notice(r['payload']),r['last_seen'].isoformat()) for r in rows)
    return observations, raw


class FreshSearch:
    def __init__(self, db, allowed_users, ted, eop, *, clock=None):
        self.db, self.ted, self.eop = db, ted, eop
        self.matcher, self.store = MatchingService(db, allowed_users), NoticeStore(db)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, actor, company_id, limits):
        started = self.clock()
        if started.tzinfo is None or limits.until > started.date():
            raise MatchingError('Use an explicit valid past/current publication window.')
        company, catalogue = self.matcher.context(actor, company_id)  # before any HTTP
        plans = expansion(company.cpv, catalogue)
        profile = Profile(company.id,company.revision,company.fields.activities,company.cpv,company.fields.regions)
        checks, issues, fresh_ids, seen_ted = [], [], set(), set()
        xml_counts = {'TED_DISCOVERY': 0, 'TED_RELATED': 0}
        raw_bytes = 0
        active_ids = set()

        def fetch_rows(rows, phase):
            nonlocal raw_bytes
            maximum = limits.discovery_xml if phase == 'TED_DISCOVERY' else limits.followup_xml
            complete = True
            for row in rows:
                key = row.get('publication-number')
                identity = (key,str(row.get('notice-version') or ''),str(row.get('notice-identifier') or ''))
                if identity in seen_ted:
                    continue
                if xml_counts[phase] >= maximum:
                    checks.append(SourceCheck(phase+'_XML',str(key),'PARTIAL',0,0,'XML_REQUEST_BOUND'))
                    complete = False
                    continue
                xml_counts[phase] += 1  # failed attempts also consume the bounded admission
                try:
                    n, raw = self.ted.fetch(row)
                except (DiscoveryError, ValueError, TypeError):
                    checks.append(SourceCheck(phase+'_XML',str(key),'FAILED',0,0,'NOTICE_FETCH_OR_NORMALIZATION_FAILED'))
                    complete = False
                    continue
                n = replace(n,retrieved_at=self.clock().isoformat())
                raw_bytes += len(raw)
                if raw_bytes > 64_000_000:
                    checks.append(SourceCheck(phase+'_XML',str(key),'PARTIAL',0,0,'RUN_BYTE_BOUND'))
                    complete = False
                    continue
                # Persistence errors are fatal, not disguised as an empty source.
                record_id, _ = self.store.ingest(n,raw)
                fresh_ids.add(record_id);seen_ted.add(identity)
                if phase == 'TED_DISCOVERY' and tendering(Observation('',str(record_id),'',n,n.retrieved_at)):
                    active_ids.add(str(record_id))
                checks.append(SourceCheck(phase+'_XML',str(key),'COMPLETE',0,1))
            return complete

        for plan in plans:
            self.matcher.recheck(actor,company)
            pages, records, complete, reason = 0,0,True,None
            for number in range(1, limits.pages+1):
                try:
                    page = self.ted.active(plan.codes,page=number,limit=limits.page_size)
                except (DiscoveryError,ValueError,TypeError):
                    complete,reason=False,'SEARCH_FAILED';break
                pages += 1;records += len(page.notices)
                complete = fetch_rows(page.notices,'TED_DISCOVERY') and complete
                if page.truncated:
                    complete,reason=False,'TED_PAGE_NUMBER_CEILING'
                if page.next_page is None:
                    break
                if number == limits.pages:
                    complete,reason=False,'TED_PAGE_BOUND'
            checks.append(SourceCheck('TED_QUERY_ACTIVE_LATEST',plan.tier,'COMPLETE' if complete else ('PARTIAL' if pages else 'FAILED'),pages,records,reason))

        last_day = min(limits.until,started.date()-timedelta(days=1))
        available_days = max(0,(last_day-limits.since).days+1)
        days = min(limits.eop_days,available_days)
        if days < (limits.until-limits.since).days+1:
            issues.append('EOP_DAILY_DOWNLOADS_COVER_ONLY_EXPLICIT_SUBSET')
        for index in range(days):
            self.matcher.recheck(actor,company)
            day = last_day-timedelta(days=index)
            rejected = 0
            try:
                if hasattr(self.eop, 'fetch_day_partial'):
                    normalized,raw,rejected = self.eop.fetch_day_partial(day)
                else:
                    normalized,raw = self.eop.fetch_day(day)
            except (DiscoveryError,ValueError,TypeError):
                checks.append(SourceCheck('EOP_DAY',day.isoformat(),'FAILED',0,0,'DAILY_EXPORT_FAILED'));continue
            observed_at=self.clock().isoformat()
            normalized=tuple(replace(n,retrieved_at=observed_at) for n in normalized)
            raw_bytes += len(raw)
            if len(normalized)>200 or len(fresh_ids)+len(normalized)>MAX_OBSERVATIONS or raw_bytes>64_000_000:
                checks.append(SourceCheck('EOP_DAY',day.isoformat(),'PARTIAL',0,0,'EOP_RECORD_OR_BYTE_BOUND'));continue
            if normalized:
                fresh_ids.update(rid for rid,_ in self.store.ingest_many((n,raw) for n in normalized))
            checks.append(SourceCheck('EOP_DAY',day.isoformat(),
                ('PARTIAL' if normalized else 'FAILED') if rejected else 'COMPLETE',1,len(normalized),
                'UNSUPPORTED_EOP_NOTICE_GROUPS:' + str(rejected) if rejected else None))

        observations,raw = load_pool(self.db,fresh_ids)
        eligible, past_deadline_candidates = {}, set()
        for o in observations:
            if UUID(o.record_id) not in fresh_ids or not tendering(o):
                continue
            for lot in o.notice.lots:
                supporting = support(profile,lot,o.notice,catalogue.entries) if geography(lot,profile.regions) else None
                if supporting:
                    due = _deadline(lot)
                    if (due and due <= started) or interval_expired(lot, started):
                        past_deadline_candidates.add(o.procedure_id)
                    candidate = (min(ORDER[r] for r,_ in supporting),o.notice.source,o.notice.original_id)
                    eligible[o.procedure_id] = min(eligible.get(o.procedure_id,candidate),candidate)
        # Resolve apparently expired relevant ACTIVE candidates before spending
        # the same bounded history budget on future dates. No guessed expiry:
        # assess still withholds uncertain/conflicting/unresolved chains.
        selected = sorted(eligible,key=lambda pid: (pid not in past_deadline_candidates,eligible[pid],pid))
        uncertain = set(selected[limits.related_procedures:])
        if uncertain:
            issues.append('RELATED_PROCEDURE_CHECK_BOUND')
        for pid in selected[:limits.related_procedures]:
            self.matcher.recheck(actor,company)
            group = [o for o in observations if o.procedure_id==pid and UUID(o.record_id) in fresh_ids]
            ids = sorted({i.removeprefix('ted:procedure:') for o in group for i in o.notice.identities if i.startswith('ted:procedure:')})
            kind = 'procedure'
            if not ids:
                ids = sorted({i.removeprefix('ted:notice:') for o in group for i in o.notice.identities if i.startswith('ted:notice:')})
                kind = 'publication'
            if len(ids)!=1:
                uncertain.add(pid)
                checks.append(SourceCheck('TED_RELATED',pid,'PARTIAL',0,0,'NO_UNAMBIGUOUS_SUPPORTED_OFFICIAL_ID'));continue
            try:
                page=self.ted.related(ids[0],kind=kind,page=1,limit=25)
                ok=fetch_rows(page.notices,'TED_RELATED')
                if not page.notices or page.next_page is not None or page.truncated:
                    ok=False
                if not ok:uncertain.add(pid)
                checks.append(SourceCheck('TED_RELATED',pid,'COMPLETE' if ok else 'PARTIAL',1,len(page.notices),None if ok else 'RELATED_PAGE_OR_XML_INCOMPLETE'))
                if kind == 'publication':
                    updated,_ = load_pool(self.db,fresh_ids)
                    procedure_ids = sorted({i.removeprefix('ted:procedure:') for o in updated
                        if o.procedure_id==pid and UUID(o.record_id) in fresh_ids
                        for i in o.notice.identities if i.startswith('ted:procedure:')})
                    if len(procedure_ids)==1:
                        self.matcher.recheck(actor,company)
                        page=self.ted.related(procedure_ids[0],kind='procedure',page=1,limit=25)
                        complete=fetch_rows(page.notices,'TED_RELATED') and bool(page.notices) and page.next_page is None and not page.truncated
                        if not complete:uncertain.add(pid)
                        checks.append(SourceCheck('TED_RELATED_PROCEDURE',pid,'COMPLETE' if complete else 'PARTIAL',1,len(page.notices),None if complete else 'RELATED_PAGE_OR_XML_INCOMPLETE'))
                    else:
                        uncertain.add(pid)
                        checks.append(SourceCheck('TED_RELATED_PROCEDURE',pid,'PARTIAL',0,0,'PROCEDURE_UUID_NOT_ESTABLISHED'))
            except (DiscoveryError,ValueError,TypeError):
                uncertain.add(pid);checks.append(SourceCheck('TED_RELATED',pid,'FAILED',0,0,'RELATED_QUERY_FAILED'))

        # No recursive crawl beyond the bounded identity/procedure follow-ups.
        observations,raw=load_pool(self.db,fresh_ids)
        fresh_strings={str(i) for i in fresh_ids}
        for o in observations:
            if o.record_id in fresh_strings:
                m=metadata(o,raw.get(o.sha256))
                if m.error:
                    uncertain.add(o.procedure_id)
                    issues.append('LIFECYCLE_SOURCE_BYTES_OR_IDENTITY_NOT_VERIFIED')
                if m.changed and not any(m.changed in {metadata(p,raw.get(p.sha256)).key,p.notice.original_id}
                                         and p.record_id in fresh_strings for p in observations):
                    uncertain.add(o.procedure_id)
                    issues.append('LINKED_CHANGE_TARGET_NOT_REFETCHED_WITHIN_BOUNDS')
        finished=self.clock()
        views,assessments,differences=assess(observations,fresh_strings,raw,now=finished,uncertain_procedures=uncertain,active_ids=active_ids)
        matches=self.matcher.evaluate_context(actor,company,catalogue,views,now=finished)
        # Keep all original references alongside the explicitly derived views.
        references=defaultdict(set)
        for o in observations:
            references[o.procedure_id].add((o.notice.source,o.notice.original_id,o.notice.version,o.notice.official_url,o.sha256))
        opportunities=[]
        for opportunity in matches.opportunities:
            refs=tuple(sorted(references[opportunity.procedure_id],key=lambda r:(r[0],r[1],r[2] or '',r[3],r[4])))
            opportunities.append(replace(opportunity,other_source_references=refs,
                warnings=tuple(sorted(set(opportunity.warnings+('LIFECYCLE_MATCHING_VIEW_SEE_FIELD_EVIDENCE',))))))
        matches=replace(matches,opportunities=tuple(opportunities))
        for check in checks:
            if check.outcome!='COMPLETE':
                issues.append(check.phase+':'+(check.reason or 'INCOMPLETE'))
        if any(a.evidence_state=='CONFLICTING' or any('MISSING' in r or 'INCOMPLETE' in r or 'UNREADABLE' in r or 'CONFLICT' in r for r in a.reasons) for a in assessments):
            issues.append('LIFECYCLE_RELATIONSHIP_OR_EVIDENCE_INCOMPLETE')
        success=any(c.outcome=='COMPLETE' or
            (c.phase=='EOP_DAY' and c.outcome=='PARTIAL' and c.records > 0) for c in checks)
        outcome=('PARTIAL' if issues else 'COMPLETE_WITHIN_BOUNDS') if success else 'FAILED'
        return FreshResult(started.isoformat(),finished.isoformat(),limits.since.isoformat(),limits.until.isoformat(),
            plans,tuple(checks),outcome,tuple(sorted(set(issues))),assessments,differences,matches)
