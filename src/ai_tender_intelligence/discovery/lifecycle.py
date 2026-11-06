"""Evidence-scoped lifecycle projection. Never confirms current openness."""
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
import xml.etree.ElementTree as ET

from .models import SourceTime
from .ted import NS, deadline, text
from .fresh_models import Fact, LotAssessment, Difference
from ..matching.engine import representative, _deadline, interval_expired

EF = 'http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1'
EB = 'http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1'
X = {**NS, 'e': EF, 'z': EB}


@dataclass(frozen=True)
class Meta:
    procedure: str | None = None
    key: str | None = None
    changed: str | None = None
    sections: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    results: tuple[tuple[str, str], ...] = ()
    ordinary_lots: tuple[str, ...] = ()
    error: str | None = None


def metadata(observation, raw):
    n = observation.notice
    if n.source != 'TED' or not n.profile.startswith('eforms-sdk-1.'):
        return Meta()
    if not raw or sha256(raw).hexdigest() != observation.sha256:
        return Meta(error='SOURCE_BYTES_NOT_VERIFIED')
    if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'\x00' in raw:
        return Meta(error='UNSAFE_NOTICE_XML')
    try:
        root = ET.fromstring(raw)
        pending, count = [(root, 0)], 0
        while pending:
            node, depth = pending.pop(); count += 1
            if depth > 64 or count > 100000:
                return Meta(error='NOTICE_STRUCTURE_EXCEEDS_BOUNDS')
            pending.extend((child, depth+1) for child in node)
        identity, version = text(root, 'b:ID'), text(root, 'b:VersionID')
        proc = text(root, 'b:ContractFolderID')
        if identity != n.source_fields.get('xml_notice_id') or version != n.version or ('ted:procedure:' + (proc or '')) not in n.identities:
            return Meta(error='NOTICE_IDENTITY_INCONSISTENT')
        changes = root.find('.//e:Changes', X)
        # ContractModification/ChangedNoticeIdentifier is not a procurement Change.
        changed = text(changes, 'z:ChangedNoticeIdentifier'.replace('z:', '{'+EB+'}'))
        sections = tuple(sorted(set(e.text.strip() for e in ([] if changes is None else changes.findall('e:Change/e:ChangedSection/z:ChangedSectionIdentifier', X)) if e.text)))
        reasons = tuple(sorted(set(e.text.strip() for e in ([] if changes is None else changes.findall('e:ChangeReason/b:ReasonCode[@listName="change-corrig-justification"]', X)) if e.text)))
        results = []
        for node in root.findall('.//e:NoticeResult/e:LotResult', X):
            lid = node.find('e:TenderLot/b:ID', X)
            code = node.find('b:TenderResultCode[@listName="winner-selection-status"]', X)
            if lid is not None and lid.text and code is not None and code.text:
                results.append((lid.text.strip(), code.text.strip()))
        ordinary = []
        for node in root.findall('c:ProcurementProjectLot', X):
            lid = text(node, 'b:ID')
            systems = {e.get('listName'): (e.text or '').strip() for e in node.findall('c:TenderingProcess/c:ContractingSystem/b:ContractingSystemTypeCode', X)}
            if lid and systems.get('framework-agreement') == systems.get('dps-usage') == 'none':
                ordinary.append(lid)
        return Meta(proc, (identity or '') + '-' + (version or '').zfill(2), changed, sections,
                    reasons, tuple(results), tuple(ordinary))
    except (ET.ParseError, ValueError, TypeError, AttributeError):
        return Meta(error='NOTICE_LIFECYCLE_METADATA_UNREADABLE')


def fact(o, path, value):
    n = o.notice
    return Fact(n.source, n.original_id, n.version, o.record_id, o.sha256, n.official_url,
                n.publication.raw, n.retrieved_at, path, value)


def tendering(o):
    n = o.notice
    if n.source == 'EOP':
        return True
    if n.source_fields.get('form-type') != 'competition' or 'ContractAwardNotice' in n.source_fields.get('xml_root',''):
        return False
    if n.profile.startswith('eforms-sdk-1.'):
        return n.source_fields.get('notice_type') in {'cn-standard','cn-social','cn-desg','qu-sy','subco','pin-cfc-standard','pin-cfc-social'}
    return n.source_fields.get('legacy_form') in {'F02','F05','F12','F17','F24'}


def assess(observations, fresh_ids, raw_xml, *, now, uncertain_procedures=frozenset(), active_ids=frozenset()):
    """Return derived matching views, per-lot evidence and supported differences.

    Complete, explicitly linked eForms changes are consolidated notices by the
    official contract. We select their own body; never splice old/sibling facts.
    """
    groups = defaultdict(list)
    for o in observations:
        groups[o.procedure_id].append(o)
    views, assessments, differences = [], [], []
    for pid, group in sorted(groups.items()):
        current = [o for o in group if o.record_id in fresh_ids]
        candidates = [o for o in current if tendering(o)]
        metas = {o.record_id: metadata(o, raw_xml.get(o.sha256)) for o in group}
        if not candidates:
            for o in current:
                for lot in o.notice.lots:
                    codes = [code for lid,code in metas[o.record_id].results if lid==lot.id]
                    facts = [fact(o, 'notice/root', o.notice.source_fields.get('xml_root'))]
                    facts.extend(fact(o,'eForms/BT-142/'+lot.id,code) for code in codes)
                    state = ('RESULT_RECORDED_UNVERIFIED' if codes else 'UNVERIFIED')
                    assessments.append(LotAssessment(pid, lot.id, state, False,
                        ('NO_FRESH_TENDERING_BODY',), tuple(facts)))
            continue
        selected, projection_warnings = representative(candidates)
        # Preserve the stored-matcher's changed-byte protection for the selected identity/version.
        if selected:
            same = [o for o in group if (o.notice.source, o.notice.original_id, o.notice.version) ==
                    (selected.notice.source, selected.notice.original_id, selected.notice.version)]
            if len({o.sha256 for o in same}) > 1:
                selected, projection_warnings = None, ('CONFLICTING_BYTES_SAME_SOURCE_VERSION',)
        if selected is None:
            for o in candidates:
                for lot in o.notice.lots:
                    assessments.append(LotAssessment(pid, lot.id, 'CONFLICTING', False, projection_warnings, (fact(o, 'notice/sha256', o.sha256),)))
            continue
        n, meta = selected.notice, metas[selected.record_id]
        common_reasons = list(projection_warnings)
        context_lots = set()
        uncertain = pid in uncertain_procedures
        if any(metas[o.record_id].error for o in current):
            uncertain = True
            common_reasons.append('RELATED_LIFECYCLE_METADATA_UNREADABLE')
        if uncertain:
            common_reasons.append('RELATED_RETRIEVAL_INCOMPLETE')
        if meta.error:
            for lot in n.lots:
                assessments.append(LotAssessment(pid, lot.id, 'UNVERIFIED', False, (meta.error,), (fact(selected, 'notice/sha256', selected.sha256),)))
            continue
        if meta.changed:
            targets = [o for o in group if o.record_id != selected.record_id and
                       meta.changed in {metas[o.record_id].key, o.notice.original_id}]
            if len(targets) != 1 or metas[targets[0].record_id].procedure != meta.procedure:
                common_reasons.append('CHANGE_TARGET_MISSING_OR_PROCEDURE_CONFLICT')
                uncertain = True
            else:
                parent = targets[0]
                parent_lots = {l.id: l for l in parent.notice.lots}
                new_lots = {l.id: l for l in n.lots}
                if not parent_lots.keys() <= new_lots.keys():
                    common_reasons.append('CONSOLIDATED_CHANGE_INVENTORY_INCOMPLETE')
                    uncertain = True
                    for missing in sorted(parent_lots.keys() - new_lots.keys()):
                        assessments.append(LotAssessment(pid,missing,'UNVERIFIED',False,
                            ('LOT_MISSING_FROM_CHANGED_BODY_NOT_CONFIRMED_CLOSED',),
                            (fact(parent,'lot/'+missing+'/present','true'),fact(selected,'changed-body/lot/'+missing,None))))
                if parent.record_id not in fresh_ids:
                    common_reasons.append('CHANGE_TARGET_ONLY_HISTORICAL_NOT_REFETCHED')
                    uncertain = True
                for lid in sorted(parent_lots.keys() & new_lots.keys()):
                    before, after = parent_lots[lid], new_lots[lid]
                    for name, a, b in [('deadline', before.deadline.raw, after.deadline.raw),
                                       ('main_cpv', ','.join(before.main_cpv), ','.join(after.main_cpv)),
                                       ('performance', ','.join(before.geography), ','.join(after.geography))]:
                        if a != b:
                            differences.append(Difference(pid, lid, name, a, b, parent.record_id, selected.record_id, True))
                common_reasons.append('EXPLICIT_LINKED_CONSOLIDATED_CHANGE')
        if any(reason in {'susp-review', 'cancel', 'cancel-intent'} for reason in meta.reasons):
            # A notice cancellation is not procedure cancellation; suspension and
            # resumption share contexts that are not universal current-state codes.
            common_reasons.append('NOTICE_CANCELLATION_OR_SUSPENSION_CONTEXT_NOT_CURRENT_STATUS')
            known_lots = {lot.id for lot in n.lots}
            if 'cancel' not in meta.reasons and meta.sections and set(meta.sections) <= known_lots:
                context_lots.update(meta.sections)
            else:
                uncertain = True
        # Follow only exact verified change ancestry; no sibling/version field splicing.
        chain, cursor = {selected.record_id}, selected
        while metas[cursor.record_id].changed:
            cm = metas[cursor.record_id]
            parents = [o for o in group if cm.changed in {metas[o.record_id].key,o.notice.original_id}]
            if (len(parents) != 1 or parents[0].record_id in chain
                    or metas[parents[0].record_id].procedure != meta.procedure
                    or parents[0].record_id not in fresh_ids or metas[parents[0].record_id].error):
                uncertain = True
                common_reasons.append('CHANGE_ANCESTRY_NOT_FULLY_SUPPORTED')
                break
            parent = parents[0]
            if not {l.id for l in parent.notice.lots} <= {l.id for l in cursor.notice.lots}:
                uncertain = True
                common_reasons.append('CHANGE_ANCESTRY_LOT_INVENTORY_INCOMPLETE')
            if set(metas[parent.record_id].reasons) & {'susp-review','cancel','cancel-intent'}:
                uncertain = True
                common_reasons.append('EARLIER_REVIEW_OR_CANCELLATION_CONTEXT_NOT_RESOLVED')
            chain.add(parent.record_id)
            cursor = parent
        # An unexplained newer same-procedure observation is not a complete state.
        chosen_day = (n.publication.raw or '')[:10]
        for other in current:
            om = metas[other.record_id]
            if other.record_id not in chain and om.changed and om.changed not in {meta.key, n.original_id}:
                common_reasons.append('OTHER_CHANGE_CHAIN_NOT_RESOLVED')
                uncertain = True
            if other.notice.source != n.source and om.results:
                common_reasons.append('CROSS_SOURCE_LOT_STATUS_IDENTITY_NOT_VERIFIED')
                uncertain = True
        kept = []
        if n.source == 'EOP':
            previous = [o for o in group if o.notice.source=='EOP' and o.notice.original_id==n.original_id
                        and (o.notice.version or '') < (n.version or '')]
            if previous:
                prior=max(previous,key=lambda o:o.notice.version or '')
                old_lots={l.id:l for l in prior.notice.lots}
                for current_lot in n.lots:
                    old=old_lots.get(current_lot.id)
                    if old and old.source_fields.get('tenderId') is not None and old.source_fields.get('tenderId')==current_lot.source_fields.get('tenderId'):
                        for name,a,b in [('deadline',old.deadline.raw,current_lot.deadline.raw),
                                         ('isCancelled',old.source_fields.get('isCancelled'),current_lot.source_fields.get('isCancelled'))]:
                            if a!=b:differences.append(Difference(pid,current_lot.id,name,a,b,prior.record_id,selected.record_id,True))
        for lot in n.lots:
            reasons, facts, states = list(common_reasons), [fact(selected, 'lot/'+lot.id+'/deadline', lot.deadline.raw)], set()
            if n.source == 'EOP' and n.profile == 'EOP-DAILY-JSON-observed-2026-10-28':
                value = lot.source_fields.get('isCancelled')
                facts.append(fact(selected, 'daily-json/lot/isCancelled', value))
                reasons.append('EOP_DAILY_EXPORT_NOT_LATER_CORRECTION_COMPLETE')
                if value == 'Да':
                    states.add('CANCELLED_AS_PUBLISHED')
            for other in current:
                om = metas[other.record_id]
                if not meta.procedure or om.procedure != meta.procedure or om.error:
                    continue
                for lid, code in om.results:
                    if lid != lot.id:
                        continue
                    facts.append(fact(other, 'eForms/BT-142/'+lid, code))
                    if (other.notice.publication.raw or '')[:10] < chosen_day:
                        reasons.append('RESULT_PRECEDES_NEWER_TENDERING_OBSERVATION')
                        states.add('UNRESOLVED_REOPENING')
                    elif other.notice.source_fields.get('notice_type') == 'can-modif':
                        reasons.append('CONTRACT_MODIFICATION_NOT_NEW_TENDERING_RESULT')
                    elif code in {'clos-nw', 'selec-w'} and lid in meta.ordinary_lots:
                        states.add('CLOSED_AS_PUBLISHED' if code == 'clos-nw' else 'AWARDED_AS_PUBLISHED')
                        due = _deadline(lot)
                        if code == 'selec-w' and due and due > now:
                            states.add('FUTURE_DEADLINE_AND_AWARD_CONFLICT')
                    elif code == 'open-nw':
                        states.add('ONGOING_AT_PUBLICATION_NOT_CURRENT_OPEN')
                    else:
                        reasons.append('RESULT_SCOPE_OR_CODE_NOT_SUFFICIENT_FOR_CLOSURE')
                        states.add('UNINTERPRETED_RESULT_SCOPE_OR_CODE')
            terminal = states & {'CANCELLED_AS_PUBLISHED', 'CLOSED_AS_PUBLISHED', 'AWARDED_AS_PUBLISHED'}
            conflict = len(terminal) > 1 or bool(terminal and states - terminal)
            due = _deadline(lot)
            expired = bool(due and due <= now) or interval_expired(lot, now)
            if conflict:
                state, exclude = 'CONFLICTING', False
                reasons.append('CONFLICTING_OFFICIAL_OBSERVATIONS')
            elif 'susp-review' in meta.reasons and lot.id in context_lots:
                state, exclude = 'SUSPENSION_REPORTED_AS_PUBLISHED', False
                facts.append(fact(selected, 'eForms/BT-140/scoped/'+lot.id, 'susp-review'))
            elif uncertain or lot.id in context_lots or 'UNINTERPRETED_RESULT_SCOPE_OR_CODE' in states:
                state, exclude = 'UNVERIFIED', False
                if terminal:reasons.append('PUBLISHED_CLOSURE_EVIDENCE_WITH_INCOMPLETE_CURRENT_CHAIN')
            elif terminal:
                state, exclude = next(iter(terminal)), True
            elif expired:
                state, exclude = 'EXPIRED_DEADLINE_AS_PUBLISHED', True
            elif (due and due > now and selected.record_id in active_ids and n.source == 'TED'
                    and tendering(selected) and lot.id in meta.ordinary_lots and not states):
                state, exclude = 'ACTIVE_AS_PUBLISHED', False
                facts.append(fact(selected, 'TED_SEARCH/ACTIVE/onlyLatestVersions', 'true'))
                reasons.append('ACTIVE_LATEST_COMPETITION_AND_FUTURE_LOT_DEADLINE_AS_COLLECTED')
            elif due and due > now:
                state, exclude = 'FUTURE_DEADLINE_UNVERIFIED', False
            else:
                state, exclude = 'UNVERIFIED', False
                reasons.append('DEADLINE_INSTANT_NOT_ESTABLISHED')
            reasons.append('NO_VERIFIED_COMPLETE_CURRENT_OPEN_STATUS_CONTRACT')
            withhold = uncertain or conflict or lot.id in context_lots or 'UNINTERPRETED_RESULT_SCOPE_OR_CODE' in states
            assessments.append(LotAssessment(pid, lot.id, state, exclude, tuple(sorted(set(reasons))), tuple(facts), withhold))
            if not exclude:
                # Deliberate ephemeral view only: original source observations
                # stay immutable; uncertain past deadlines/cancel flags cannot
                # accidentally be re-applied by the unchanged stored matcher.
                fields = dict(lot.source_fields)
                if withhold:
                    fields.pop('isCancelled', None)
                kept.append(replace(lot, deadline=SourceTime() if withhold else lot.deadline, source_fields=fields))
        if kept:
            views.append(replace(selected, notice=replace(n, lots=tuple(kept))))
    return tuple(views), tuple(assessments), tuple(differences)
