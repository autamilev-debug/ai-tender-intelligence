"""Official public daily JSON downloads; deliberately no NX1 service dependency."""
from datetime import date, datetime, timezone
from urllib.parse import quote
import re

from .models import DiscoveryError, Lot, Notice, source_time, validate_notice
from .ted import load_json


def daily_url(day: date):
    if not date(2020, 1, 1) <= day < datetime.now(timezone.utc).date():
        raise DiscoveryError('EOP daily export requires a completed day since 2020.')
    # Exact filename template from the official public open-data UI (2026-10-28).
    filename = 'Автоматично генерирани данни за поръчки, публикувани в ЦАИС ЕОП на ' + day.strftime('%d.%m.%Y') + '.json'
    return f'https://storage.eop.bg/open-data-{day.isoformat()}/' + quote(filename, safe='')


class EOP:
    def __init__(self, http):
        self.http = http

    def fetch_day(self, day):
        url = daily_url(day)
        data = self.http.request('GET', url)
        return normalize_daily(data, day, url), data

    def fetch_day_partial(self, day):
        """Preserve valid whole notice groups; never salvage selected lots."""
        url = daily_url(day)
        data = self.http.request('GET', url)
        notices, rejected = normalize_daily_partial(data, day, url)
        return notices, data, rejected


def normalize_daily_partial(data, day, url=None):
    if len(data) > 8_000_000:
        raise DiscoveryError('EOP daily export exceeds bounds.')
    rows = load_json(data)
    if not isinstance(rows, list) or len(rows) > 10000:
        raise DiscoveryError('Unsupported EOP daily envelope.')
    groups = {}
    for row in rows:
        # An unbound row could belong to any group: fail rather than omit a lot.
        if not isinstance(row, dict) or type(row.get('noticeId')) is not int or row['noticeId'] <= 0:
            raise DiscoveryError('EOP notice group identity unavailable.')
        groups.setdefault(row['noticeId'], []).append(row)
    import json
    notices, rejected = [], 0
    for group in groups.values():
        try:
            notices.extend(normalize_daily(json.dumps(group, ensure_ascii=False).encode(), day, url))
        except DiscoveryError:
            rejected += 1
    return tuple(notices), rejected


def normalize_daily(data, day, url=None, *, retrieved_at=None):
    if len(data) > 8_000_000:
        raise DiscoveryError('EOP daily export exceeds bounds.')
    try:
        rows = load_json(data.decode('utf-8-sig'))
    except UnicodeError:
        raise DiscoveryError('Malformed EOP export encoding.') from None
    if not isinstance(rows, list) or len(rows) > 10000:
        raise DiscoveryError('Unsupported EOP daily envelope.')
    groups = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('isLot') not in {'Да', 'Не'}:
            raise DiscoveryError('Unsupported EOP procurement record.')
        number, notice_id, tender_id = row.get('uniqueProcurementNumber'), row.get('noticeId'), row.get('tenderId')
        if not isinstance(number, str) or not re.fullmatch(r'\d{5}-\d{4}-\d{4,}', number) or type(notice_id) is not int or notice_id <= 0 or type(tender_id) is not int or tender_id <= 0:
            raise DiscoveryError('EOP official identity missing.')
        groups.setdefault((number, notice_id), []).append(row)
    notices = []
    for (number, notice_id), group in groups.items():
        parents = [r for r in group if r['isLot'] == 'Не']
        if len(parents) != 1:
            raise DiscoveryError('EOP parent inventory inconsistent.')
        parent = parents[0]
        children = [r for r in group if r['isLot'] == 'Да']
        lots = []
        for row in children or [parent]:
            lid = row.get('lotIdentifier') if children else 'PROCEDURE'
            if not isinstance(lid, str) or not lid.strip():
                raise DiscoveryError('EOP lot identifier missing.')
            cpv, geo = row.get('mainCpvCode'), row.get('executionPlaceNuts')
            lots.append(Lot(lid, row.get('lotTenderName') or row.get('subject'), None,
                            (cpv,) if cpv else (), (), (geo,) if geo else (), source_time(row.get('submissionDeadline')),
                            {'tenderId': row['tenderId'], 'isCancelled': row.get('isCancelled'),
                             'noticeType': row.get('noticeType'), 'description_unavailable': True}))
        ids = ['eop:number:' + number] + ['eop:tender:' + str(r['tenderId']) for r in group]
        # Explicit official publication link, not title/text similarity. It binds
        # one TED source notice even where an eForms procedure UUID is unavailable.
        ted_link = parent.get('linkToOjEu') or ''
        match = re.fullmatch(r'https://ted\.europa\.eu/udl\?uri=TED:NOTICE:(\d{1,7}-\d{4}):TEXT:[A-Z]{2,3}:HTML', ted_link)
        if match:
            ids.append('ted:notice:' + match[1])
        notice = Notice('EOP', str(notice_id), day.isoformat(), 'EOP-DAILY-JSON-observed-2026-10-28',
                        'https://app.eop.bg/today/' + str(parent['tenderId']),
                        retrieved_at or datetime.now(timezone.utc).isoformat(), parent.get('subject'), None,
                        (parent['buyerName'],) if parent.get('buyerName') else (), source_time(parent.get('publicationDate')),
                        tuple(lots), (parent['mainCpvCode'],) if parent.get('mainCpvCode') else (), (), tuple(sorted(set(ids))), (),
                        {'export_day': day.isoformat(), 'export_url': url or daily_url(day), 'records': group,
                         'freshness': 'DAILY_OPENING_RECORDS_NOT_LATER_CORRECTION_COMPLETE',
                         'version_kind': 'EXPORT_DAY_NOT_NOTICE_REVISION',
                         'offsets': 'NOT_ESTABLISHED', 'lotsCount_raw': parent.get('lotsCount')})
        validate_notice(notice)
        notices.append(notice)
    return tuple(notices)
