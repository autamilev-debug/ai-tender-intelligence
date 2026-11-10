"""Bounded ordered blocks and conservative, nonsemantic readability assessment."""
import re
from .models import Limits, ExtractionError, canonical
from ..acquisition.validation import inspect
from ..acquisition.models import Bounds, AcquisitionError


class BoundReached(Exception):
    pass


class Builder:
    def __init__(self, limits):
        self.limits = limits
        self.blocks, self.units, self.flags = [], [], []
        self.characters = self.cells = 0
        self.partial = False

    def flag(self, code, *, gap=False):
        if code not in self.flags:
            self.flags.append(code)
        self.partial |= gap

    def clean(self, text):
        text = str(text).replace('\r\n', '\n').replace('\r', '\n')
        cleaned = re.sub(r'https?://[^\s<>"\']+', lambda m: m[0].split('?')[0].split('#')[0], text)
        if cleaned != text:
            self.flag('SOURCE_URL_QUERY_REDACTED')
        return cleaned

    def add(self, kind, locator, **content):
        if not locator:
            raise ExtractionError('SOURCE_LOCATOR_MISSING')
        item = {'kind': kind, 'locator': locator, **content}
        size = len(canonical(item))
        if len(self.blocks) >= self.limits.blocks or self.characters + size > self.limits.characters:
            raise BoundReached()
        self.characters += size
        self.blocks.append(item)

    def cell(self):
        self.cells += 1
        if self.cells > self.limits.cells:
            raise BoundReached()


def usable(text):
    text = ''.join(text.split())
    if not text:
        return False
    return (len(text) >= 10 and sum(c.isalnum() for c in text) / len(text) >= .35
            and text.count('\ufffd') / len(text) <= .02
            and sum(ord(c) < 32 for c in text) / len(text) <= .01
            and '(cid:' not in text)


def extract(raw, kind, limits=Limits()):
    """Called inside the constrained process; raw input must still pass validation."""
    builder = Builder(limits)
    from . import formats
    try:
        validation = inspect(raw, 'original.' + kind.lower(), Bounds())
        if not validation.get('valid') or validation['kind'] != kind:
            raise ExtractionError('SOURCE_FORMAT_MISMATCH')
        if kind == 'DOC':
            return {'status': 'UNSUPPORTED', 'blocks': [], 'units': [],
                    'flags': ['LEGACY_DOC_SAFE_EXTRACTOR_UNAVAILABLE'], 'readability': 'NOT_ESTABLISHED'}
        functions = {'PDF': formats.pdf, 'DOCX': formats.docx, 'XLSX': formats.xlsx,
                     'XLS': formats.xls, 'XML': formats.structured_xml, 'JSON': formats.structured_json}
        if kind not in functions:
            return {'status': 'UNSUPPORTED', 'blocks': [], 'units': [],
                    'flags': ['UNSUPPORTED_EXTRACTION_FORMAT'], 'readability': 'NOT_ESTABLISHED'}
        try:
            functions[kind](raw, builder)
        except BoundReached:
            builder.flag('EXTRACTION_RESOURCE_TRUNCATION', gap=True)
        except Exception:
            builder.flag('DOCUMENT_PARSER_FAILED', gap=True)
        def payload_text(node):
            if isinstance(node,dict):
                for key,item in node.items():
                    if key in {'text','value','formula_text'} and item is not None:yield str(item)
                    elif key not in {'locator','source'}:yield from payload_text(item)
            elif isinstance(node,list):
                for item in node:yield from payload_text(item)
        texts=list(payload_text(builder.blocks))
        meaningful=any(t.strip() for t in texts)
        if any(t.count('\ufffd')/max(1,len(t))>.02 for t in texts):builder.flag('UNREADABLE_CHARACTER_MAPPING',gap=True)
        status = ('UNREADABLE' if not meaningful else 'PARTIAL' if builder.partial else 'EXTRACTED')
        result = {'status': status, 'blocks': builder.blocks, 'units': builder.units,
                  'flags': builder.flags, 'readability': 'HEURISTIC_CHECKS_ONLY',
                  'table_coverage': 'STRUCTURAL_CANDIDATES_ONLY_NO_SEMANTIC_GUARANTEE'}
        if not meaningful:builder.flag('EMPTY_EXTRACTED_CONTENT',gap=True)
        # Serialization overhead/cells are bounded separately from raw text.
        while len(canonical(result)) > limits.output_bytes and result['blocks']:
            result['blocks'].pop()
            builder.flag('EXTRACTION_OUTPUT_TRUNCATION', gap=True)
            result['status'] = 'PARTIAL' if result['blocks'] else 'UNREADABLE'
        if len(canonical(result)) > limits.output_bytes:
            return {'status': 'UNREADABLE', 'blocks': [], 'units': [], 'flags': ['EXTRACTION_OUTPUT_BOUND']}
        return result
    except (AcquisitionError, ExtractionError):
        return {'status': 'UNREADABLE', 'blocks': [], 'units': [], 'flags': ['SOURCE_VALIDATION_FAILED']}
