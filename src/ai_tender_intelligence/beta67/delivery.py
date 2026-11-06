"""One-use delivery capability bound to the admitted request, job and MIME."""
from dataclasses import dataclass
from ..telegram.models import InteractionError
from .store import BetaStore, Requests


@dataclass(frozen=True)
class SendPermit:
    request: str
    user: int
    company: str
    job: str
    digest: str
    recipient: str

    def verify(self,store,user,company,job,digest):
        if (type(store) is not BetaStore or (user,str(company),str(job),digest)!=
            (self.user,self.company,self.job,self.digest)):
            raise InteractionError('Невалидно разрешение за изпращане.')
        request=Requests(store).read(user,company,self.request)
        if (request['kind']!='ANALYSIS' or request['state']!='RUNNING' or str(request['job'])!=str(job)
            or request['recipient']!=self.recipient or store.recipient_for(user,company,job)!=self.recipient):
            raise InteractionError('Заявката или получателят са променени.')
        from ..analysis.control45 import validate_approval
        from ..semantic.retrieval import digest as plan_digest
        row=store.read(user,company,job);evidence=row['evidence']
        authority=evidence.get('BETA67_AUTHORITY',{})
        plan=evidence.get('AI_PLAN45',{}).get('plan')
        if (not plan or authority.get('request')!=self.request
            or authority.get('plan_digest')!=plan_digest(plan) or not plan.get('internal_beta')
            or plan.get('actor')!=user or plan.get('company')!=str(company) or plan.get('job')!=str(job)
            or 'RUN_STARTED46' not in evidence or evidence.get('TERRA',{}).get('state')!='VALIDATED'):
            raise InteractionError('Липсва потвърдена връзка между анализа и имейла.')
        validate_approval(plan,authority.get('ai_approval',{}))
        return self.recipient
