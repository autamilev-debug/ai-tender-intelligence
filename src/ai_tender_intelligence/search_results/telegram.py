"""Explicit test-only bridge into existing cards. No preview/production composition is changed."""
from datetime import datetime, timezone
from ..telegram.models import InteractionError, Tender, Lot
PAGE_DISCLAIMER = 'ℹ️ Статусът е според публикуваните данни. Проверете актуалността в TED/ЦАИС ЕОП.'
BROADER_COPY = 'По-широк сектор — възможна възможност, но не пряко съвпадение с дейността ви.'

PRODUCTION_SEARCH_ENABLED = False  # no environment switch or runtime activation in this ticket
ANALYSIS_ADMISSION_ENABLED = False


class StoredTelegramSearch:
    def __init__(self, results, *, development_preview=False):
        self.results = results
        self.development_preview = development_preview
        self.selected = {}

    def _guard(self):
        # This preparation adapter is never a production activation mechanism.
        if not self.development_preview or PRODUCTION_SEARCH_ENABLED:
            raise InteractionError('Търсенето още не е активирано за реална употреба.')

    def bind(self, user, company, search_id):
        self._guard()
        record = self.results.read(user, company, search_id)
        self.selected[(user,company)] = record.id

    def snapshot_reference(self, user, company):
        self._guard()
        reference = self.selected.get((user,company))
        if reference is None:
            raise InteractionError('Няма избрано запазено търсене.')
        return reference

    def search_results(self, user, company):
        return self.stored_results(user, company, self.snapshot_reference(user,company))

    def stored_results(self, user, company, reference):
        self._guard()
        stored = self.results.read(user,company,reference)  # every page reauthorizes, never discovery
        result = stored.result
        notice = self._notice(stored)
        states = {}
        assessments = {}
        for a in result.assessments:
            key = (a.procedure_id,a.source_lot_id)
            assessments.setdefault(key, []).append(a)
            states[key] = a.evidence_state if key not in states or states[key] == a.evidence_state else 'CONFLICTING'
        tenders=[]
        for o in result.matches.opportunities:
            lots=tuple(Lot(l.lot_id,l.title or l.lot_id,presentation_status(states.get((o.procedure_id,l.lot_id))),
                datetime.fromisoformat(l.deadline_instant) if l.deadline_instant else None,
                display_deadline(o,l,assessments.get((o.procedure_id,l.lot_id),()),stored.source_payloads)
                if not l.deadline_instant else None) for l in o.lots)
            payload = stored.source_payloads.get(o.record_id, {})
            source_lots = {l['id']: l for l in payload.get('lots', ())}
            from ..matching.geography import NUTS
            raw_places={code for l in o.lots for code in source_lots.get(l.lot_id,{}).get('geography',())}
            geography=sorted({NUTS[code] if code in NUTS else 'България (точното място не е установено)'
                              for code in raw_places if code in NUTS or code in {'BG','BGR'}})
            if not geography and any(l.geography_kind=='EXPLICIT_NATIONWIDE' for l in o.lots):
                geography=['Цяла България (изрично посочено място на изпълнение)']
            descriptions = [source_lots.get(l.lot_id, {}).get('description') for l in o.lots]
            if any(l.lot_id == 'PROCEDURE' for l in o.lots):
                descriptions.append(payload.get('description'))
            description = next((d for d in descriptions if d), None)
            if description:
                description = ' '.join(description.split())
                description = description[:450] + ('…' if len(description) > 450 else '')
            evidence = [e for l in o.lots for e in l.evidence]
            reason = ('Обхватът на позицията съответства на дейността във вашия профил.' if o.relation == 'DIRECT'
                      else 'Свързана възможност — поръчката включва дейности от вашия профил като част от по-широк проект.'
                      if o.relation == 'RELATED' else 'Предметът включва дейности, свързани с вашия профил.')
            if evidence:
                reason += ' ' + evidence[0].tender_label
            details = (description or 'Подробното описание е в официалната публикация.') + '\n' + reason
            if o.relation == 'BROADER':
                details += '\n' + BROADER_COPY
            from .value_ranges import eur_range, MISSING
            values = [(l.lot_id, eur_range(source_lots.get(l.lot_id, {}).get('source_fields', {}).get('estimated_value')))
                      for l in o.lots]
            value = '; '.join(lid + ': ' + v if len(values) > 1 else v for lid,v in values) or MISSING
            procedure_amount = eur_range(payload.get('source_fields', {}).get('procedure_estimated_value'))
            procedure_only = all(v == MISSING for _,v in values) and procedure_amount != MISSING
            if procedure_only:
                value = 'За цялата поръчка: ' + procedure_amount
            if (procedure_only or any(v != MISSING for _,v in values)) and o.relation in {'RELATED','BROADER'}:
                value += (' (за цялата поръчка, не само за съответстващите дейности)' if procedure_only else
                          ' (за цялата позиция, не само за съответстващите дейности)')
            tenders.append(Tender(o.procedure_id,o.title or o.original_id,', '.join(stored.buyers.get(o.record_id, ())) or 'Не е установен',
                ', '.join(geography) or 'Не е установено',lots,tuple(l.id for l in lots),
                value,'Не е установен',details,
                o.official_url,search_notice=notice,match_label=o.label_bg,source_reference=o.original_id))
        from ..telegram.rendering import tender_text
        if any(len(tender_text(t, datetime.now(timezone.utc)).encode('utf-16-le')) // 2 > 3500 for t in tenders):
            raise InteractionError('Търсенето е запазено. Поне една карта надхвърля поддържания размер. За показването е необходим преглед на формата. Използвайте „Меню“.')
        return tuple(tenders)

    @staticmethod
    def _notice(stored):
        result = stored.result
        partial = ('Частични резултати — не успяхме да проверим всички публикации.\n'
                   if result.outcome == 'PARTIAL' else '')
        return partial + PAGE_DISCLAIMER

    def empty_notice(self, user, company):
        self._guard()
        return ('Няма намерени подходящи поръчки в получените резултати.\n'
                + self._notice(self.results.read(user,company,self.snapshot_reference(user,company))))

    def tender(self, tender):
        # The old owner-free convenience method cannot authorize private results.
        raise InteractionError('Необходим е достъп до запазеното търсене на собственика.')


def presentation_status(state):
    # Evidence labels are deliberately not the OPEN admission status.
    supported = {'ACTIVE_AS_PUBLISHED','EXPIRED_DEADLINE_AS_PUBLISHED','CANCELLED_AS_PUBLISHED',
                 'CLOSED_AS_PUBLISHED','AWARDED_AS_PUBLISHED','SUSPENSION_REPORTED_AS_PUBLISHED','CONFLICTING'}
    return state if state in supported else 'REQUIRES_CONFIRMATION'


def deadline_label(raw):
    if not raw:
        return None
    from ..discovery.models import source_time
    value = source_time(raw)
    if value.precision == 'DATE':
        return datetime.strptime(raw[:10], '%Y-%m-%d').strftime('%d.%m.%Y') + ' (часът не е посочен)'
    if value.precision == 'LOCAL_TIME_OFFSET_UNKNOWN':
        return datetime.fromisoformat(raw).strftime('%d.%m.%Y, %H:%M') + ' (часова зона не е посочена)'
    return None


def display_deadline(opportunity, lot, assessments, payloads):
    """Published source date is display evidence, never an admission/filter date."""
    if (any(a.evidence_state == 'CONFLICTING' for a in assessments)
            or len({a.evidence_state for a in assessments}) > 1):
        return None
    if lot.deadline_raw:
        return deadline_label(lot.deadline_raw)
    values = {f.value for a in assessments for f in a.facts
              if f.record_id == opportunity.record_id and f.sha256 == opportunity.sha256
              and f.path == 'lot/' + lot.lot_id + '/deadline' and f.value}
    source = next((l for l in payloads.get(opportunity.record_id, {}).get('lots', ())
                   if l['id'] == lot.lot_id), None)
    if len(values) != 1 or source is None or source.get('deadline', {}).get('raw') not in values:
        return None
    raw = next(iter(values))
    from ..discovery.models import source_time
    parsed = source_time(raw)
    if parsed.instant:
        from ..telegram.rendering import date_text
        label = date_text(datetime.fromisoformat(parsed.instant))
    else:
        label = deadline_label(raw)
    return label + ' (публикуван срок)' if label else None
