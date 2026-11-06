"""Shared strict/Beta finalization; P65 supersedes Beta semantic exclusion rules."""
from copy import deepcopy
from ..semantic.contract import SemanticError
from ..semantic.internal54 import report_guard
from . import validation40_offline as validation

VERSION='report-finalization65-v1'


def validate_report(reply,inputs,*,internal_beta=False):
    if type(internal_beta) is not bool:raise SemanticError('BETA62_EXPLICIT_INTERNAL_MODE_REQUIRED')
    if internal_beta:
        from .beta65 import validate
        return validate(reply,inputs)
    original=validation.validate(reply,inputs)
    report_guard(original,inputs)
    return original,None


def prepare_email(reply,inputs,job,*,execution_mode,internal_beta=False,fence,subject=None,record_review=None,recipient=None):
    """The normal runtime's existing final validation → fence → HTML → MIME path."""
    from .presentation52 import attach
    from .runtime44 import render_mail
    from ..demo41.mail import prepare
    checked,audit=validate_report(reply,inputs,internal_beta=internal_beta)
    if audit is not None and record_review is not None:
        record_review(deepcopy(audit))  # Preserve diagnostics even if later preparation fails.
    fence()  # Owned source integrity is still required before rendering/preparing.
    report={**checked,'canonical_input':inputs,'publishable':False,
            'semantic_review':'NOT_INDEPENDENTLY_VERIFIED_BETA' if internal_beta else 'REQUIRED','execution_mode':execution_mode}
    if audit:report['terra_review62']=audit
    report=attach(report);preview=render_mail(report)
    envelope={'recipient':recipient} if recipient is not None else {}
    message=prepare(preview['html'],preview['text'],job,**envelope,
        subject=subject or 'ВЪТРЕШЕН ТЕСТ — нов анализ на поръчка '+str(inputs['identity']['parent_tender']))
    return report,message,audit
