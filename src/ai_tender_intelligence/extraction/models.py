"""Reviewable resource choices and content/configuration identities."""
from dataclasses import dataclass, asdict
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import json
import platform,unicodedata


class ExtractionError(ValueError):
    """Fixed safe code, never parser exception text or source content."""


@dataclass(frozen=True)
class Limits:
    pages: int = 100
    sheets: int = 20
    rows: int = 5000
    columns: int = 200
    cells: int = 20000
    blocks: int = 4000
    characters: int = 500000
    output_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        ceilings = dict(pages=100, sheets=20, rows=5000, columns=200,
                        cells=20000, blocks=4000, characters=500000, output_bytes=4*1024*1024)
        if any(type(getattr(self, k)) is not int or not 1 <= getattr(self, k) <= v for k, v in ceilings.items()):
            raise ExtractionError('INVALID_EXTRACTION_LIMITS')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(kind, limits=Limits()):
    root = Path(__file__).parent
    implementation = sha256(b''.join((root/n).read_bytes() for n in
        ['models.py', 'engine.py', 'formats.py', 'worker.py', 'runner.py']) +
        b''.join((root.parent/'acquisition'/n).read_bytes() for n in ['validation.py','validate_worker.py'])).hexdigest()
    identity = {'version': 'deterministic-evidence-v1', 'format': kind,
                'implementation_sha256': implementation, 'limits': asdict(limits),
                'python':platform.python_version(),'unicode':unicodedata.unidata_version,
                'libraries': {n: version(n) for n in ['pypdf', 'pdfplumber', 'pdfminer.six', 'openpyxl', 'xlrd', 'olefile']}}
    return sha256(canonical(identity)).hexdigest(), identity
