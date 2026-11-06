"""TED public Search API v3 and explicit eForms/legacy metadata profiles."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
import xml.etree.ElementTree as ET
from functools import lru_cache

from .models import DiscoveryError, Lot, Notice, source_time, validate_notice

FIELDS = ['publication-number', 'notice-title', 'buyer-name', 'publication-date', 'classification-cpv',
          'procedure-identifier', 'notice-identifier', 'notice-version', 'form-type']
NS = {'c': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
      'b': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'}


@lru_cache(maxsize=1)
def official_codes():
    from ai_tender_intelligence.catalogue.source import load_pinned
    return {e.code: e.check_digit for e in load_pinned().entries}


def load_json(data):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise DiscoveryError('Duplicate JSON field.')
            result[k] = v
        return result
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(DiscoveryError('Invalid JSON number.')))
    except (ValueError, UnicodeError, RecursionError):
        raise DiscoveryError('Malformed public JSON.') from None


def query(codes, since: date | None, until: date | None = None):
    codes = tuple(sorted(set(codes)))
    if not 1 <= len(codes) <= 20 or any(not re.fullmatch(r'\d{8}(?:-\d)?', c) for c in codes):
        raise DiscoveryError('Supply 1–20 distinct source CPVs.')
    official = official_codes()
    if any(c[:8] not in official or (len(c) == 10 and c[-1] != official[c[:8]]) for c in codes):
        raise DiscoveryError('Query CPV is not a verified official identifier.')
    codes = tuple(sorted({c[:8] for c in codes}))
    if until and since and until < since:
        raise DiscoveryError('Invalid publication window.')
    # Country scope is performance, not buyer address. Hierarchical CPV semantics
    # retrieve candidates; they do not establish direct relevance or eligibility.
    result = 'place-of-performance = BGR AND classification-cpv IN (' + ' '.join(c[:8] for c in codes) + ')'
    if since:
        result += ' AND publication-date >= ' + since.strftime('%Y%m%d')
    if until:
        result += ' AND publication-date <= ' + until.strftime('%Y%m%d')
    return result


@dataclass(frozen=True)
class Page:
    notices: tuple[dict, ...]
    total: int
    next_page: int | None
    truncated: bool = False


class TED:
    def __init__(self, http):
        self.http = http

    def search(self, codes, since, *, until=None, page=1, limit=25):
        if type(page) is not int or type(limit) is not int or not 1 <= limit <= 100 or not 1 <= page or page * limit > 15000:
            raise DiscoveryError('Invalid bounded TED page.')
        body = {'query': query(codes, since, until), 'fields': FIELDS, 'page': page, 'limit': limit,
                'scope': 'ALL', 'paginationMode': 'PAGE_NUMBER', 'onlyLatestVersions': False}
        return self._page(body, page, limit)

    def active(self, codes, *, page=1, limit=10):
        """Current ACTIVE competition pool; no short publication-age exclusion."""
        if type(page) is not int or type(limit) is not int or not 1 <= page <= 3 or not 1 <= limit <= 25:
            raise DiscoveryError('Invalid bounded ACTIVE query.')
        expression = query(codes, None) + ' AND form-type = competition SORT BY publication-date DESC'
        body = {'query': expression, 'fields': FIELDS, 'page': page, 'limit': limit,
                'scope': 'ACTIVE', 'paginationMode': 'PAGE_NUMBER', 'onlyLatestVersions': True}
        return self._page(body, page, limit)

    def related(self, identifier, since=None, until=None, *, kind='procedure', page=1, limit=10):
        """Only documented public identity fields; never titles/private descriptions."""
        from uuid import UUID
        field = {'procedure': 'procedure-identifier', 'notice': 'notice-identifier',
                 'publication': 'publication-number'}.get(kind)
        if not field or not 1 <= page <= 2 or not 1 <= limit <= 25 or (since is not None and until is not None and until < since):
            raise DiscoveryError('Invalid bounded related-notice query.')
        try:
            if kind != 'publication':
                identifier = str(UUID(identifier))
            elif not re.fullmatch(r'\d{1,7}-\d{4}', identifier):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise DiscoveryError('Invalid official related-notice identifier.') from None
        expression = field + ' = ' + identifier
        if since:
            expression += ' AND publication-date >= ' + since.strftime('%Y%m%d')
        if until:
            expression += ' AND publication-date <= ' + until.strftime('%Y%m%d')
        expression += ' SORT BY publication-date DESC'
        body = {'query': expression, 'fields': FIELDS, 'page': page, 'limit': limit,
                'scope': 'ALL', 'paginationMode': 'PAGE_NUMBER', 'onlyLatestVersions': False}
        return self._page(body, page, limit)

    def _page(self, body, page, limit):
        data = self.http.request('POST', 'https://api.ted.europa.eu/v3/notices/search', body=body)
        result = load_json(data)
        if not isinstance(result, dict) or type(result.get('timedOut', False)) is not bool or result.get('timedOut') is True:
            raise DiscoveryError('Incomplete TED search.')
        rows, total = result.get('notices'), result.get('totalNoticeCount')
        if not isinstance(rows, list) or len(rows) > limit or type(total) is not int or total < 0:
            raise DiscoveryError('Invalid TED search envelope.')
        if total < len(rows):
            raise DiscoveryError('Inconsistent TED search count.')
        for row in rows:
            if not isinstance(row, dict) or not re.fullmatch(r'\d{1,7}-\d{4}', str(row.get('publication-number', ''))):
                raise DiscoveryError('Invalid TED notice identity.')
        if len({r['publication-number'] for r in rows}) != len(rows):
            raise DiscoveryError('Duplicate TED page identities.')
        if not rows and (page - 1) * limit < total:
            raise DiscoveryError('TED returned an incomplete page.')
        return Page(tuple(rows), total, page + 1 if page * limit < min(total, 15000) else None,
                    total > 15000 and page * limit >= 15000)

    def fetch(self, row):
        identifier = row.get('publication-number')
        if not isinstance(identifier, str) or not re.fullmatch(r'\d{1,7}-\d{4}', identifier):
            raise DiscoveryError('Invalid TED publication identifier.')
        expected = f'https://ted.europa.eu/en/notice/{identifier}/xml'
        if row.get('links', {}).get('xml', {}).get('MUL') != expected:
            raise DiscoveryError('TED XML reference missing or inconsistent.')
        data = self.http.request('GET', expected)
        return normalize_xml(data, row), data


def text(node, path):
    if node is None:
        return None
    found_all = node.findall(path, NS)
    found = next((n for n in found_all if n.get('languageID') in {'BUL', 'BG', 'bul', 'bg'}), found_all[0] if found_all else None)
    return ''.join(found.itertext()).strip() or None if found is not None else None


def texts(node, path):
    if node is None:
        return ()
    return tuple(dict.fromkeys(''.join(e.itertext()).strip() for e in node.findall(path, NS) if ''.join(e.itertext()).strip()))


def deadline(node):
    day = text(node, 'c:TenderingProcess/c:TenderSubmissionDeadlinePeriod/b:EndDate')
    clock = text(node, 'c:TenderingProcess/c:TenderSubmissionDeadlinePeriod/b:EndTime')
    if not day:
        return source_time(None)
    if not clock:
        return source_time(day)
    day_offset = day[10:]
    clock_offset = re.search(r'(Z|[+-]\d{2}:\d{2})$', clock)
    if day_offset and clock_offset and day_offset.replace('Z', '+00:00') != clock_offset[0].replace('Z', '+00:00'):
        raise DiscoveryError('Conflicting source date/time offsets.')
    # Do not turn a date offset into an assumed time offset. EndTime must carry it.
    return source_time(day[:10] + 'T' + clock)


def project(node):
    return {'title': text(node, 'b:Name'), 'description': text(node, 'b:Description'),
            'main_cpv': texts(node, 'c:MainCommodityClassification/b:ItemClassificationCode[@listName="cpv"]'),
            'additional_cpv': texts(node, 'c:AdditionalCommodityClassification/b:ItemClassificationCode[@listName="cpv"]'),
            'geography': texts(node, 'c:RealizedLocation/c:Address/b:CountrySubentityCode') +
                         texts(node, 'c:RealizedLocation/c:Address/c:Country/b:IdentificationCode')}


def eop_id(url):
    m = re.fullmatch(r'https://app\.eop\.bg/today/(\d+)/?', url or '')
    return 'eop:tender:' + str(int(m[1])) if m else None


def normalize_xml(data, row, *, retrieved_at=None):
    if len(data) > 8_000_000 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() or b'\x00' in data:
        raise DiscoveryError('Unsafe notice XML.')
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, RecursionError):
        raise DiscoveryError('Malformed notice XML.') from None
    pending, count = [(root, 0)], 0
    while pending:
        element, depth = pending.pop(); count += 1
        if depth > 64 or count > 100000:
            raise DiscoveryError('Notice XML structure exceeds bounds.')
        pending.extend((child, depth + 1) for child in element)
    identifier = str(row.get('publication-number', ''))
    if not re.fullmatch(r'\d{1,7}-\d{4}', identifier):
        raise DiscoveryError('Invalid TED publication identifier.')
    publication = source_time(row.get('publication-date'))
    retrieved = retrieved_at or datetime.now(timezone.utc).isoformat()
    identities, relationships = [], []
    source = dict(row)
    if root.tag.startswith('{' + 'urn:oasis:names:specification:ubl:schema:xsd:') and root.tag.split('}')[-1] in {'ContractNotice', 'ContractAwardNotice', 'PriorInformationNotice'}:
        customization = text(root, 'b:CustomizationID')
        if not customization or not re.fullmatch(r'eforms-sdk-1\.\d+(?:\.\d+)?', customization):
            raise DiscoveryError('Unsupported eForms customization.')
        notice_id, version = text(root, 'b:ID'), text(root, 'b:VersionID')
        if row.get('notice-identifier') and row['notice-identifier'] != notice_id:
            raise DiscoveryError('TED notice identity mismatch.')
        if row.get('notice-version') is not None and str(row['notice-version']).lstrip('0') != (version or '').lstrip('0'):
            raise DiscoveryError('TED version mismatch.')
        proc = text(root, 'b:ContractFolderID')
        if proc:
            identities.append('ted:procedure:' + proc)
        pp = project(root.find('c:ProcurementProject', NS))
        lots = []
        for node in root.findall('c:ProcurementProjectLot', NS):
            lot_id = text(node, 'b:ID')
            if not lot_id:
                raise DiscoveryError('eForms lot identifier missing.')
            values = project(node.find('c:ProcurementProject', NS))
            lots.append(Lot(lot_id, **values, deadline=deadline(node), source_fields={
                'internal_id': text(node, 'c:ProcurementProject/b:ID'), 'profile': customization}))
            # Only document references: never buyer profiles, result contract URLs,
            # recipient endpoint IDs, or unrelated organization addresses.
            for url in texts(node, 'c:TenderingTerms/c:CallForTendersDocumentReference/c:Attachment/c:ExternalReference/b:URI'):
                if eop_id(url):
                    identities.append(eop_id(url))
        for url in texts(root, 'c:TenderingTerms/c:CallForTendersDocumentReference/c:Attachment/c:ExternalReference/b:URI'):
            if eop_id(url):
                identities.append(eop_id(url))
        for e in root.iter():
            if e.tag in {'{http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1}ChangedNoticeIdentifier',
                         '{http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1}PreviousNoticeIdentifier'} and e.text:
                relationships.append(e.text)
        # Buyers are already language-keyed by Search API, not all XML organizations.
        buyer = row.get('buyer-name', {})
        if not isinstance(buyer, dict) or any(not isinstance(v, list) or any(not isinstance(b, str) for b in v) for v in buyer.values()):
            raise DiscoveryError('Malformed TED buyer languages.')
        buyers = tuple(buyer.get('bul') or buyer.get('eng') or next(iter(buyer.values()), []))
        notice = Notice('TED', identifier, version, customization, f'https://ted.europa.eu/en/notice/{identifier}/xml',
                        retrieved, pp.pop('title'), pp.pop('description'), buyers, publication, tuple(lots) or (Lot('PROCEDURE', **pp),),
                        pp['main_cpv'], pp['additional_cpv'], tuple(sorted(set(identities))), tuple(relationships),
                        {**source, 'xml_notice_id': notice_id, 'xml_root': root.tag, 'notice_type': text(root, 'b:NoticeTypeCode')})
    elif root.tag.startswith('{http://formex.publications.europa.eu/ted/schema/export/') and root.tag.endswith('}TED_EXPORT'):
        if root.get('DOC_ID') != identifier:
            raise DiscoveryError('Legacy TED identity mismatch.')
        forms = root.find('{*}FORM_SECTION')
        choices = [] if forms is None else [f for f in forms if f.tag.split('}')[-1] in {'F01_2014','F02_2014','F03_2014','F04_2014','F05_2014','F06_2014','F14_2014','F20_2014'}]
        form = next((f for f in choices if f.get('LG') == 'BG'), choices[0] if choices else None)
        if form is None:
            raise DiscoveryError('Unsupported legacy TED form.')
        obj = form.find('{*}OBJECT_CONTRACT')
        def cpvs(n, tag):
            return tuple(dict.fromkeys(e.get('CODE') for e in n.findall('{*}' + tag + '/{*}CPV_CODE') if e.get('CODE'))) if n is not None else ()
        proc = form.find('{*}PROCEDURE')
        day, clock = text(proc, '{*}DATE_RECEIPT_TENDERS'), text(proc, '{*}TIME_RECEIPT_TENDERS')
        due = source_time(day + 'T' + clock if day and clock else day)
        lots = []
        descriptions = [] if obj is None else obj.findall('{*}OBJECT_DESCR')
        for node in descriptions:
            lid = text(node, '{*}LOT_NO')
            if not lid and (obj.find('{*}LOT_DIVISION') is not None or len(descriptions) > 1):
                raise DiscoveryError('Legacy official lot identifier is unavailable.')
            lid = lid or 'PROCEDURE'  # explicit non-lot scope, never an invented lot
            lots.append(Lot(lid, text(node, '{*}TITLE'), text(node, '{*}SHORT_DESCR'), cpvs(node, 'CPV_MAIN'),
                            cpvs(node, 'CPV_ADDITIONAL'), tuple(e.get('CODE') for e in node.findall('{*}NUTS') if e.get('CODE')),
                            due, {'deadline_scope': 'PROCEDURE', 'lot_item': node.get('ITEM')}))
        urls = texts(form, '{*}CONTRACTING_BODY/{*}URL_DOCUMENT')
        identities = [eop_id(u) for u in urls if eop_id(u)]
        notice = Notice('TED', identifier, None, root.get('VERSION', '') + ':' + form.tag.split('}')[-1],
                        f'https://ted.europa.eu/en/notice/{identifier}/xml', retrieved, text(obj, '{*}TITLE'),
                        text(obj, '{*}SHORT_DESCR'), texts(form, '{*}CONTRACTING_BODY/{*}ADDRESS_CONTRACTING_BODY/{*}OFFICIALNAME'),
                        publication, tuple(lots) or (Lot('PROCEDURE', deadline=due),), cpvs(obj, 'CPV_MAIN'), cpvs(obj, 'CPV_ADDITIONAL'),
                        tuple(identities), (), {**source, 'xml_profile': root.get('VERSION'), 'legacy_form': form.tag.split('}')[-1]})
    else:
        raise DiscoveryError('Unsupported notice XML profile.')
    validate_notice(notice)
    return notice
