"""Hash-pinned runtime Markdown; schema stays authoritative in contract.py."""
from ai_tender_intelligence.assets import root as asset_root
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

PROMPT_VERSION = 'cpv-prompts-v3'
CONTRACT_VERSION = 'cpv-classification-v2'
SCHEMA_SHA256 = '8c358c0e985f164987df078edef33a5a13946c70acc1a58bb74156b8213d7d60'
FILES = {'shared_rules.md': 'a00c0d10ff4aff073511f7abc3de8cc7363f7f6fc5db4075e361d5f4d0570109', 'luna_cpv_classification.md': '1e65bf4b907ca9a9950b21f4b4baf59b0395f4d1f8311670e00723bbbde723a8'}
ROOT = asset_root() / 'prompts'
MAX_FILE_BYTES = 16384


class PromptError(ValueError):
    """Fixed code only; never file content, business text or credentials."""
    def __init__(self,reason):
        self.reason=reason
        super().__init__(reason)


@dataclass(frozen=True,repr=False)
class PromptBundle:
    version: str
    sha256: str
    instructions: str
    root: Path


def load_prompts(schema,contract_version,root=None):
    canonical=json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if contract_version!=CONTRACT_VERSION or sha256(canonical).hexdigest()!=SCHEMA_SHA256:
        raise PromptError('PROMPT_SCHEMA_INCOMPATIBLE')
    root=ROOT if root is None else Path(root)
    parts=[]
    for filename,expected in FILES.items():
        try:
            with (root/filename).open('rb') as file:raw=file.read(MAX_FILE_BYTES+1)
        except OSError:
            raise PromptError('PROMPT_UNAVAILABLE') from None
        if len(raw)>MAX_FILE_BYTES or sha256(raw).hexdigest()!=expected:
            raise PromptError('PROMPT_HASH_MISMATCH')
        try:text=raw.decode('utf-8')
        except UnicodeError:raise PromptError('PROMPT_ENCODING_INVALID') from None
        if (f'<!-- prompt-set: {PROMPT_VERSION} -->' not in text
                or f'<!-- contract: {CONTRACT_VERSION} -->' not in text):
            raise PromptError('PROMPT_VERSION_INCOMPATIBLE')
        parts.append(text)
    instructions='\n\n'.join(parts)
    return PromptBundle(PROMPT_VERSION,sha256(instructions.encode('utf-8')).hexdigest(),instructions,root)
