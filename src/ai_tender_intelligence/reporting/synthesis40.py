"""One experimental Terra email from the unchanged, corrected P39 corpus."""
from ai_tender_intelligence.assets import root as asset_root
import json
from copy import deepcopy
from hashlib import sha256
from html import escape
from pathlib import Path

from ..classification.contract import Reply, NotSent
from ..extraction.models import canonical
from ..semantic.completeness39 import require_complete
from ..semantic.retrieval import digest
from .contract import organization_input
from . import synthesis36, validation39
from .render import DISCLAIMER, safe_url, stamp

ROOT = asset_root()
PROMPT = ROOT / 'prompts/terra_bulgarian_email_p40.md'
VERSION = 'P40-corrected-findings-email-v1'


def request(inputs):
    view = organization_input(inputs)
    require_complete(inputs['findings'])
    view.update(organization_input_revision=VERSION, schema_revision=validation39.VERSION,
                sections=[k for k, _ in synthesis36.SECTIONS],
                analysis_provenance=inputs['analysis_provenance'], finding_reuse39=inputs['finding_reuse39'],
                evaluation_priorities=['MATERIAL_COMPLETENESS', 'FACTUAL_ACCURACY', 'CLARITY', 'CUSTOMER_USEFULNESS'])
    body = dict(model='gpt-6.1-terra', reasoning={'effort': 'medium'}, store=False, service_tier='default',
                instructions=PROMPT.read_text(encoding='utf-8'), input=canonical(view).decode(),
                text={'format': {'type': 'json_schema', 'name': 'bulgarian_email_p40', 'strict': True,
                                 'schema': validation39.Synthesis.model_json_schema()}})
    if len(canonical(body)) > 524288: raise NotSent('P40_REQUEST_RESOURCE_BOUND')
    return body

