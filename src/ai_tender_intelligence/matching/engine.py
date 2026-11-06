"""Deterministic proposal-v1 rules; no model calls, qualification or live status."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re

from ..catalogue.validation import ancestor_candidates
from ..classification.retrieval import terms, query_operations, OPERATIONS
from ..discovery.models import MAX_DISPLAYED_TENDERS, source_time
from ..telegram.models import REGIONS, CATEGORIES
from .geography import geography, MAPPING
from .models import (VERSION, LABELS, Profile, Observation, Evidence, LotMatch,
                     Opportunity, MatchResult, MatchingError)

ORDER = {'DIRECT': 0, 'RELATED': 1, 'BROADER': 2}
GENERIC = terms('общи услуги работи дейности системи материали оборудване изделия конструкции обекти') | OPERATIONS
GENERIC = GENERIC.union(*(terms(name) for name in REGIONS), terms('София столица град област България'))
GENERIC = GENERIC | terms('мрежи мрежа')  # A network alone supplies no subject/domain.


def precision(code):
    return max(2, len(code.rstrip('0')))


def shared(a, b):
    return tuple(sorted({min(x, y, key=lambda t: (len(t), t)) for x in a for y in b
                         if x == y or (min(len(x), len(y)) >= 5 and
                         (x.startswith(y) or y.startswith(x)) and
                         max(x, y, key=len)[min(len(x), len(y)):] in
                         {'а','и','ите','ът','та','то','те','ен','на','ни','но','н','ове'})}))


def operations_in(text):
    # Matching-only noun inflections; the classifier/retriever stays unchanged.
    result = set(query_operations(text))
    patterns = {'ремонт': r'ремонт(?:и|ите|ът|а)?', 'монтаж': r'монтаж(?:и|ите|ът|а)?',
                'разработ': r'разработк(?:а|и|ата|ите)', 'поддръж': r'поддръжк(?:а|и|ата|ите)'}
    for operation, pattern in patterns.items():
        if re.search(r'\b' + pattern + r'\b', text, re.I):
            result.add(operation)
    if re.search(r'\bмонтажни\s+работи\b', text, re.I):
        result.add('монтаж')
    return frozenset(result)


def activity_parts(text):
    positive, negative = [], []
    # Bounded explicit clause handling, not general Bulgarian negation inference.
    for clause in re.split(r'[;.!?\n,]|\bно\b', text, flags=re.I):
        clause = clause.strip()
        if clause.lower().startswith('не само '):
            clause = clause[8:]
        parts = re.split(r'\b(?:без|не)\b', clause, maxsplit=1, flags=re.I)
        positive.append(parts[0])
        if len(parts) == 2:
            negative.append(parts[1])
    value = ' '.join(positive)
    return frozenset(t for t in terms(value) - GENERIC if not t.isdigit()), operations_in(value), terms(' '.join(negative))


def current_scope_text(value):
    """Withhold explicitly retrospective sentences, not general tense inference."""
    sentences = re.split(r'(?<=[.!?])\s+|\n', value)
    return '\n'.join(s for s in sentences if not (
        re.search(r'\b(?:са извършвани|са извършени|е извършен|са изпълнени)\b', s, re.I)
        and not re.search(r'\b(?:ще|предвижда|предмет|включва)\b', s, re.I)))


def support(profile, lot, notice, entries):
    # Parent text/CPVs can contain siblings' activities; use only the actual lot.
    whole = lot.id == 'PROCEDURE'
    text = current_scope_text('\n'.join(filter(None, (lot.title, lot.description,
                                    notice.title if whole else None, notice.description if whole else None))))
    # An umbrella utility abbreviation does not state a particular construction
    # component. Literal scope elsewhere in the passage remains available.
    text_subjects, text_ops, _ = activity_parts(re.sub(r'\bвик\b', '', text, flags=re.I))
    excluded_scope = terms(' '.join(re.findall(
        r'\b(?:не се предвижда|не включва|не включват|изключва)\b[^.!?\n]*', text, re.I)))
    codes = set(lot.main_cpv + lot.additional_cpv)
    if whole:
        codes.update(notice.main_cpv + notice.additional_cpv)
    primary = set(lot.main_cpv + (notice.main_cpv if whole else ()))
    title_subjects, title_ops, _ = activity_parts(lot.title or (notice.title if whole else '') or '')
    found = []
    for tc in sorted(codes & entries.keys()):
        target = entries[tc]
        target_subjects, target_ops, _ = activity_parts(target.description_bg)
        for cc in sorted(profile.cpv):
            company = entries[cc]
            ancestors_c = {cc} | {c for c in ancestor_candidates(cc) if c in entries}
            ancestors_t = {tc} | {c for c in ancestor_candidates(tc) if c in entries}
            common = ancestors_c & ancestors_t
            if not common:
                continue
            parent = max(common, key=lambda c: (precision(c), c))
            lineage = cc in ancestors_t or tc in ancestors_c
            for category, activity in sorted(profile.activities):
                subjects, operations, excluded = activity_parts(activity)
                if shared(subjects, excluded_scope):
                    continue
                label_terms = terms(target.description_bg)
                if shared(excluded, label_terms) or shared(excluded, terms(text)):
                    continue
                company_topics = activity_parts(company.description_bg)[0]
                if precision(cc) >= 4 and company_topics and not shared(subjects, company_topics):
                    continue  # A saved code never invents a company's actual activity.
                label_overlap = shared(subjects, target_subjects)
                anchors = set(label_overlap) | set(shared(subjects, company_topics))
                text_overlap = shared(anchors or subjects, text_subjects)
                primary_overlap = any(shared(subjects, activity_parts(entries[c].description_bg)[0])
                                      for c in primary if c in entries)
                # A secondary code cannot manufacture a component absent from
                # actual current lot scope (including a retrospective mention).
                if tc not in primary and not text_overlap:
                    continue
                effective_ops = text_ops or target_ops
                compatible = not effective_ops or bool(operations & effective_ops)
                if cc == tc and (precision(tc) >= 5 or (precision(tc) == 4 and text_overlap)) and label_overlap and compatible:
                    relation, rule = 'DIRECT', 'EXACT_SPECIFIC_CPV_ACTIVITY'
                elif lineage and precision(parent) >= 5 and label_overlap and text_overlap and operations & target_ops and compatible:
                    relation, rule = 'DIRECT', 'LINEAGE_WITH_EXPLICIT_ACTIVITY_AND_NOTICE'
                elif precision(parent) >= 5 and label_overlap and text_overlap:
                    relation, rule = 'RELATED', 'NEAR_HIERARCHY_WITH_TOPIC_EVIDENCE'
                elif text_overlap and (label_overlap or operations & text_ops):
                    relation, rule = 'BROADER', 'SECTOR_WITH_EXPLICIT_TOPIC_NOT_SECTOR_ALONE'
                else:
                    continue
                if relation == 'DIRECT' and tc not in primary and not primary_overlap:
                    relation, rule = 'RELATED', 'SECONDARY_CPV_WITH_EXPLICIT_COMPONENT_SCOPE'
                elif (relation == 'BROADER' and tc in primary and precision(tc) >= 5 and precision(cc) >= 5
                        and label_overlap and shared(subjects, title_subjects) and operations & title_ops):
                    relation, rule = 'DIRECT', 'PRIMARY_CPV_AND_EXPLICIT_ACTIVITY_TITLE'
                explanation = {
                    'DIRECT': 'Официалният CPV и потвърдената дейност подкрепят пряко съвпадение.',
                    'RELATED': 'Близка CPV връзка и предметни термини сочат свързана възможност; точният обхват остава несигурен.',
                    'BROADER': 'Секторна връзка с конкретни предметни термини; това не означава изпълнение на всички дейности в сектора.'}[relation]
                found.append((relation, Evidence(category, company.identifier, target.identifier,
                    company.description_bg, target.description_bg, parent, label_overlap or text_overlap,
                    text_overlap, rule, explanation)))
    if not found:
        return None
    best = min(ORDER[r] for r, _ in found)
    return tuple((r, e) for r, e in found if ORDER[r] == best)


def _version_key(observation):
    n = observation.notice
    if n.source == 'TED' and (n.version or '').isdigit():
        return (1, int(n.version))
    if n.source == 'EOP' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', n.version or ''):
        return (2, n.version)
    return (0, '')


def representative(group):
    """Coherent observation selection, explicitly NOT current lifecycle resolution."""
    buckets = defaultdict(list)
    for observation in group:
        buckets[(observation.notice.source, observation.notice.original_id)].append(observation)
    survivors, warnings = [], []
    for bucket in buckets.values():
        keys = {_version_key(o) for o in bucket}
        if len(keys) > 1 and any(k[0] == 0 for k in keys):
            warnings.append('INCOMPARABLE_SOURCE_VERSIONS')
            continue
        latest = [o for o in bucket if _version_key(o) == max(keys)]
        if len({o.sha256 for o in latest}) > 1:
            warnings.append('CONFLICTING_BYTES_SAME_SOURCE_VERSION')
            continue
        survivors.append(min(latest, key=lambda o: o.record_id))
    # Do not revive an older alternative after the latest identity was quarantined.
    if warnings:
        return None, tuple(sorted(set(warnings)))
    selected = max(survivors, key=lambda o: ((o.notice.publication.raw or '')[:10],
        o.notice.source == 'TED', o.notice.original_id, o.sha256))
    if len(survivors) > 1:
        warnings.append('OTHER_OBSERVATIONS_NOT_MERGED_CURRENT_STATE_UNRESOLVED')
    return selected, tuple(warnings)


def _deadline(lot):
    value = lot.deadline
    if value.raw is None:
        return None
    parsed = source_time(value.raw)
    if parsed.precision == 'INSTANT' and parsed.instant == value.instant:
        return datetime.fromisoformat(parsed.instant)
    return None


def interval_expired(lot, now):
    """An upper bound, never a fabricated instant/timezone for a missing value.

    ISO UTC offsets are strictly less than24h. Even a date-only deadline's
    latest possible instant precedes date+2days UTC. Ambiguous near dates stay.
    """
    raw = lot.deadline.raw
    if not raw:
        return False
    parsed = source_time(raw)
    if parsed.precision == 'DATE':
        upper = datetime.fromisoformat(raw[:10]).replace(tzinfo=timezone.utc) + timedelta(days=2)
    elif parsed.precision == 'LOCAL_TIME_OFFSET_UNKNOWN':
        upper = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc) + timedelta(days=1)
    else:
        return False
    return upper <= now


def match(profile: Profile, observations: tuple[Observation, ...], catalogue, *, now):
    if now.tzinfo is None:
        raise MatchingError('An explicit timezone-aware evaluation instant is required.')
    now = now.astimezone(timezone.utc)
    if not 1 <= len(profile.activities) <= 2 or len({c for c, _ in profile.activities}) != len(profile.activities) or any(not t.strip() or len(t) > 2000 for _, t in profile.activities):
        raise MatchingError('A complete approved activity context is required.')
    if any(c not in CATEGORIES for c, _ in profile.activities):
        raise MatchingError('Unknown company category.')
    if not profile.regions <= set(REGIONS):
        raise MatchingError('Unknown company oblast.')
    catalogue.validate_set(profile.cpv)
    if any(not re.fullmatch(r'\d{8}', c) for c in profile.cpv):
        raise MatchingError('Use normalized approved company assignments.')
    grouped = defaultdict(list)
    for observation in observations:
        grouped[observation.procedure_id].append(observation)
    ranked, diagnostics = [], []
    for pid, group in sorted(grouped.items()):
        observed, warnings = representative(group)
        if observed is None:
            diagnostics.extend((pid, w) for w in warnings)
            continue
        n = observed.notice
        if n.source == 'TED' and (n.source_fields.get('form-type') not in {None, 'competition'} or 'ContractAwardNotice' in n.source_fields.get('xml_root', '')):
            diagnostics.append((pid, 'LATEST_OBSERVATION_NOT_TENDERING'))
            continue
        matches, priorities = [], []
        for lot in sorted(n.lots, key=lambda l: l.id):
            if n.source == 'EOP' and n.profile == 'EOP-DAILY-JSON-observed-2026-10-28' and lot.source_fields.get('isCancelled') == 'Да':
                diagnostics.append((pid, 'CANCELLED_IN_OFFICIAL_OBSERVATION:' + lot.id))
                continue
            due = _deadline(lot)
            if (due and due <= now) or interval_expired(lot, now):
                diagnostics.append((pid, 'OBSERVED_DEADLINE_EXPIRED:' + lot.id))
                continue
            geo = geography(lot, profile.regions)
            if not geo:
                diagnostics.append((pid, 'GEOGRAPHY_UNKNOWN_OR_NONOVERLAP:' + lot.id))
                continue
            supporting = support(profile, lot, n, catalogue.entries)
            if not supporting:
                diagnostics.append((pid, 'NO_SUPPORTED_RELEVANCE:' + lot.id))
                continue
            relation = supporting[0][0]
            evidence = tuple(e for _, e in supporting)
            kind, oblasts, geographic_rank, geo_warnings = geo
            lot_warnings = ['CURRENT_AVAILABILITY_NOT_VERIFIED', *geo_warnings]
            if kind == 'EXACT_OBLAST':
                lot_warnings.append('SOURCE_NUTS_EDITION_NOT_ESTABLISHED')
            if not due:
                lot_warnings.append('DEADLINE_INSTANT_NOT_ESTABLISHED')
            unknown = (set(lot.main_cpv + lot.additional_cpv) - catalogue.entries.keys())
            if unknown:
                lot_warnings.append('UNRECOGNIZED_SOURCE_CPV_IGNORED')
            soon = bool(due and timedelta(0) < due - now <= timedelta(days=7))
            matches.append(LotMatch(lot.id, lot.title, relation, LABELS[relation], evidence, kind, oblasts,
                lot.deadline.raw, due.isoformat() if due else None,
                soon, 'Крайният срок е в следващите седем дни.' if soon else None, tuple(sorted(set(lot_warnings)))))
            strength = max(len(e.activity_terms) for e in evidence)  # no category-count boost
            priorities.append((ORDER[relation], geographic_rank, -min(strength, 5), due is None,
                               due.timestamp() if due else float('inf')))
        if not matches:
            continue
        best = min(priorities)
        relation = min(matches, key=lambda l: ORDER[l.relation]).relation
        references = tuple(sorted(set((o.notice.source, o.notice.original_id, o.notice.version,
                                       o.notice.official_url, o.sha256) for o in group),
                                  key=lambda r: (r[0], r[1], r[2] or '', r[3], r[4])))
        result = Opportunity(pid, n.source, n.original_id, n.version, n.profile, observed.record_id, observed.sha256,
            n.official_url, n.title, n.publication.raw, n.retrieved_at, observed.last_seen,
            relation, LABELS[relation], tuple(matches), references, tuple(sorted(set(warnings))))
        ranked.append((best + (n.source, n.original_id, n.version or '', observed.sha256, pid), result))
    selected = tuple(r for _, r in sorted(ranked, key=lambda item: item[0])[:MAX_DISPLAYED_TENDERS])
    return MatchResult(profile.company_id, profile.revision, now.isoformat(), VERSION, catalogue.version_id,
                       MAPPING, selected, tuple(sorted(set(diagnostics))))
