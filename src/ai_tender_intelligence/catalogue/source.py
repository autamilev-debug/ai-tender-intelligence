"""Hash-anchored parsing of the unchanged official main vocabulary only."""
from ai_tender_intelligence.assets import root as asset_root

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = asset_root()
REFERENCE = ROOT / 'reference-data/cpv/2008'
PINNED_SHA256 = '542fb7a8f35f5d0e377fd0ee621e78d0804705578c4c913dc90ec012310e59ff'
# Canonical entry digest derived from those verified source bytes. This checks
# complete DB reads too, so a missing row is not mistaken for verified absence.
PINNED_ENTRIES_SHA256 = '318b6bea850fa8249b46db60f8d798569c24c9bf0a1182b971740e65d7397b57'
SOURCE_URL = 'https://ted.europa.eu/documents/d/ted/cpv_2008_xml'
RELEASE = 'CPV 2008 main vocabulary'
VERSION_ID = 'CPV2008-' + PINNED_SHA256[:32]
IDENTIFIER = re.compile(r'[0-9]{8}-[0-9]\Z')
NORMALIZED = re.compile(r'[0-9]{8}\Z')


class CatalogueError(ValueError):
    """Fixed public reason codes only: no SQL, file paths or connection values."""

    REASONS = {'SOURCE_UNAVAILABLE','PROVENANCE_INVALID','SOURCE_SIZE_MISMATCH','SOURCE_HASH_MISMATCH',
        'UNSAFE_XML','MALFORMED_XML','INVALID_STRUCTURE','INVALID_IDENTIFIER','DUPLICATE_CODE',
        'INVALID_DESCRIPTION','INCONSISTENT_LANGUAGES','RECORD_COUNT_MISMATCH',
        'IMPORT_PERMISSION_REQUIRED','ACTIVE_REPLACEMENT_REFUSED','STORED_VERSION_MISMATCH',
        'CATALOGUE_UNAVAILABLE','INVALID_CODE_FORMAT','CODE_NOT_FOUND','CHECK_DIGIT_MISMATCH',
        'INVALID_CODE_SET','DATABASE_OPERATION_FAILED'}

    def __init__(self, reason):
        if reason not in self.REASONS:
            raise ValueError('Unknown catalogue error code')
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CPVEntry:
    code: str
    check_digit: str
    description_bg: str

    @property
    def identifier(self):
        return self.code + '-' + self.check_digit


@dataclass(frozen=True)
class VerifiedCatalogue:
    entries: tuple[CPVEntry, ...]
    source_sha256: str
    source_bytes: int
    version_id: str = VERSION_ID
    release: str = RELEASE
    source_url: str = SOURCE_URL


def entries_digest(entries):
    rows = [(e.code,e.check_digit,e.description_bg) for e in sorted(entries,key=lambda e:e.code)]
    return sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def _parse_xml(raw: bytes, expected_languages=None) -> tuple[CPVEntry, ...]:
    # Do not execute the supplied DTD/CSS or fetch entities. Pinning alone is
    # not a reason to relax parser protection in unit tests or later revisions.
    upper = raw.upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        raise CatalogueError('UNSAFE_XML')
    try:
        root = ET.fromstring(raw.decode('utf-8', errors='strict'))
    except (ET.ParseError, UnicodeError):
        raise CatalogueError('MALFORMED_XML') from None
    if root.tag != 'CPV_CODE' or root.attrib or (root.text and root.text.strip()):
        raise CatalogueError('INVALID_STRUCTURE')
    entries, seen = [], set()
    languages = frozenset(expected_languages) if expected_languages is not None else None
    for row in root:
        if (row.tag != 'CPV' or set(row.attrib) != {'CODE'} or (row.text and row.text.strip())
                or (row.tail and row.tail.strip())):
            raise CatalogueError('INVALID_STRUCTURE')
        identifier = row.attrib['CODE']
        if not IDENTIFIER.fullmatch(identifier):
            raise CatalogueError('INVALID_IDENTIFIER')
        code, digit = identifier.split('-')
        if code in seen:
            raise CatalogueError('DUPLICATE_CODE')
        seen.add(code)
        texts = {}
        for item in row:
            if (item.tag != 'TEXT' or set(item.attrib) != {'LANG'} or len(item)
                    or (item.tail and item.tail.strip())):
                raise CatalogueError('INVALID_STRUCTURE')
            lang = item.attrib['LANG']
            if not re.fullmatch(r'[A-Z]{2}', lang) or lang in texts:
                raise CatalogueError('INCONSISTENT_LANGUAGES')
            texts[lang] = item.text
        if languages is None:
            languages = frozenset(texts)
        if frozenset(texts) != languages:
            raise CatalogueError('INCONSISTENT_LANGUAGES')
        bg = texts.get('BG')
        if not bg or not bg.strip() or any(ord(c) < 32 for c in bg):
            raise CatalogueError('INVALID_DESCRIPTION')
        entries.append(CPVEntry(code, digit, bg))
    if not entries:
        raise CatalogueError('RECORD_COUNT_MISMATCH')
    return tuple(sorted(entries, key=lambda e: e.code))


def load_pinned(source_path: Path | None = None) -> VerifiedCatalogue:
    """Read existing local bytes once, hash before parsing; no configurable pin.

    A test/copy path is permitted only when it has the identical reviewed bytes.
    The public CLI cannot change the expected checksum or provenance record.
    """
    try:
        provenance = json.loads((REFERENCE / 'PROVENANCE.json').read_text(encoding='utf-8'))
        members = [m for m in provenance['members'] if m['path'] == 'cpv_2008.xml']
        inspection = provenance['local_main_xml_inspection']
        if (len(members) != 1 or members[0]['sha256'] != PINNED_SHA256
                or provenance['official_download_url'] != SOURCE_URL
                or type(members[0]['bytes']) is not int or not 0 < members[0]['bytes'] <= 16 * 1024 * 1024
                or type(inspection['unique_main_codes']) is not int
                or inspection['unique_main_codes'] <= 0 or 'BG' not in inspection['languages']):
            raise CatalogueError('PROVENANCE_INVALID')
        expected_size = members[0]['bytes']
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CatalogueError):
            raise
        raise CatalogueError('PROVENANCE_INVALID') from None
    try:
        with Path(source_path or REFERENCE / 'cpv_2008.xml').open('rb') as handle:
            raw = handle.read(expected_size + 1)
    except OSError:
        raise CatalogueError('SOURCE_UNAVAILABLE') from None
    if len(raw) != expected_size:
        raise CatalogueError('SOURCE_SIZE_MISMATCH')
    if sha256(raw).hexdigest() != PINNED_SHA256:
        raise CatalogueError('SOURCE_HASH_MISMATCH')
    entries = _parse_xml(raw, inspection['languages'])
    if len(entries) != inspection['unique_main_codes']:
        raise CatalogueError('RECORD_COUNT_MISMATCH')
    if entries_digest(entries) != PINNED_ENTRIES_SHA256:
        raise CatalogueError('SOURCE_HASH_MISMATCH')
    return VerifiedCatalogue(entries, PINNED_SHA256, len(raw))
