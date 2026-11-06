"""Versioned submission framing; historical semantic v1/v2 payloads unchanged."""
from ai_tender_intelligence.assets import root as asset_root
import json
from hashlib import sha256
from pathlib import Path
from . import contract_v2 as v2
from .contract import SemanticError
from .submission import guide
from ..extraction.models import canonical

VERSION='tender-submission-verification-v3'
PROMPT_VERSION='submission-evidence-v4'
PROMPT_FILE=asset_root()/'prompts/submission_evidence_v4.md'
PROMPT_SHA256='cf801296a82fedcc8eb227c50d0a91c0593c9a36bc4ee29cc8bbac1d4692586b'
RESPONSE_SCHEMA=v2.RESPONSE_SCHEMA

def instructions():
    raw=PROMPT_FILE.read_bytes()
    if sha256(raw).hexdigest()!=PROMPT_SHA256:raise SemanticError('SUBMISSION_PROMPT_HASH_MISMATCH')
    return raw.decode('utf-8')

def request(packet):
    if packet['category']!='SUBMISSION_DEADLINES':raise SemanticError('SUBMISSION_CATEGORY_REQUIRED')
    body=v2.request(packet);body['instructions']=instructions();data=json.loads(body['input'])
    data['verification_metadata'].update(schema_revision=VERSION,prompt_revision=PROMPT_VERSION,prompt_sha256=PROMPT_SHA256)
    data['submission_coverage']=guide(packet);body['input']=canonical(data).decode('utf-8')
    if len(canonical(body))>131072:raise SemanticError('VERIFICATION_REQUEST_BOUND')
    return body
