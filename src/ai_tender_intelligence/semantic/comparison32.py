"""Pinned Sol semantic request compatibility contract."""
import json
from copy import deepcopy
from hashlib import sha256
from . import contract_v2 as task
from .contract import SemanticError
from ..classification.contract import NotSent
from ..extraction.models import canonical

MODEL='gpt-6.1-sol';EFFORT='medium'
PROMPT_REVISION='tender-verification-prompts-v3-sol32-compat'
CATEGORIES=('TECHNICAL_SPECIFICATIONS','SUBMISSION_DEADLINES')

def instructions():
    old='Use gpt-6-luna High for interpretation; Sol Medium performs later synthesis.'
    new='Use gpt-6.1-sol Medium for interpretation in this controlled comparison.'
    raw=task.instructions()
    if raw.count(old)!=1:raise NotSent('SOL32_PROMPT_COMPATIBILITY_MISMATCH')
    # Only model-role compatibility; no benchmark answers or added source hints.
    return raw.replace(old,new,1)

def request(packet):
    body=deepcopy(task.request(packet));body['model']=MODEL;body['reasoning']={'effort':EFFORT}
    body['instructions']=instructions();data=json.loads(body['input'])
    data['verification_metadata'].update(model=MODEL,reasoning_effort=EFFORT,
        prompt_revision=PROMPT_REVISION,prompt_sha256=sha256(instructions().encode()).hexdigest())
    body['input']=canonical(data).decode('utf-8')
    if len(canonical(body))>131072:raise SemanticError('VERIFICATION_REQUEST_BOUND')
    return body
