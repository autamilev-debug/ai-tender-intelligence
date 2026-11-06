"""Conservative current EOP public admission; no historical or universal status claim."""
import re
from datetime import datetime,timezone
from ..classification.contract import NotSent
from ..acquisition.sources import EOPDocuments

# Official NX1 public proxy observed 2026-11-04. These are namespace-specific.
PROFILE='EOP-public-participation-2026-11-04'


def instant(value):
    m=re.fullmatch(r'/Date\((-?[0-9]+)\)/',value or '')
    if not m:raise NotSent('ADMISSION45_OFFICIAL_TIME_UNVERIFIABLE')
    try:return datetime.fromtimestamp(int(m[1])/1000,timezone.utc)
    except (ValueError,OverflowError,OSError):raise NotSent('ADMISSION45_OFFICIAL_TIME_UNVERIFIABLE') from None


def open_detail(detail,identity,parent,now):
    if (not isinstance(detail,dict) or type(detail.get('TenderId')) is not int
        or detail['TenderId']!=identity or detail.get('ParentTender')!=parent):
        raise NotSent('ADMISSION45_IDENTITY_NOT_VERIFIED')
    if (type(detail.get('PublishedTenderStatus')) is not int or detail['PublishedTenderStatus']!=1
        or type(detail.get('PublishedTenderParticipationStatus')) is not int
        or detail['PublishedTenderParticipationStatus']!=1):
        raise NotSent('ADMISSION45_PARTICIPATION_NOT_OPEN')
    status=detail.get('ExplicitTenderStatus')
    if type(status) is not int or status not in {0,2,4,6}:raise NotSent('ADMISSION45_SUSPENDED_TERMINAL_OR_UNKNOWN')
    if detail.get('HasContinuousEvaluation') is not False or detail.get('IsFrameworkAgreement') is not False:
        raise NotSent('ADMISSION45_UNSUPPORTED_PROCEDURE')
    start,end=instant(detail.get('OfferPhaseStartDate')),instant(detail.get('OfferPhaseEndDate'))
    if not start<=now<end:raise NotSent('ADMISSION45_SUBMISSION_WINDOW_NOT_OPEN')
    return {'tender_id':identity,'parent':parent,'published_status':1,'participation_status':1,
            'explicit_status':status,'submission_start':start.isoformat(),'submission_deadline':end.isoformat()}


class EOPAdmission:
    def __init__(self,http,*,clock=lambda:datetime.now(timezone.utc)):
        self.api=EOPDocuments(http);self.clock=clock

    def verify(self,parent,lots):
        if type(parent) is not int or parent<=0 or len(lots)>10 or len(set(lots))!=len(lots) or any(type(i) is not int or i<=0 for i in lots):
            raise NotSent('ADMISSION45_SELECTION_INVALID')
        offset=len(self.api.receipts);now=self.clock()
        args={'tenderId':parent,'ianaTimeZone':'Europe/Sofia'}
        detail=self.api.call('GetPublishedTenderDetails',args)
        parent_fact=open_detail(detail,parent,None,now)
        rows=self.api.rows('GetPublishedLots',args)
        ids=[r.get('TenderId') for r in rows]
        if any(type(i) is not int or i<=0 for i in ids) or len(ids)!=len(set(ids)):
            raise NotSent('ADMISSION45_AMBIGUOUS_LOT_INVENTORY')
        facts=[]
        if not lots:
            if rows or detail.get('ChildTenders')!=[]:raise NotSent('ADMISSION45_EXPLICIT_LOT_SELECTION_REQUIRED')
        else:
            if not set(lots)<=set(ids):raise NotSent('ADMISSION45_SELECTED_LOT_NOT_IN_OFFICIAL_PARENT')
            for lot in lots:
                row=next(r for r in rows if r['TenderId']==lot)
                # Public lot-summary status semantics are not a universal enum.
                # Non-neutral/ambiguous summary values cannot be overridden by a
                # parent OPEN field. Identity-bearing child evidence is required.
                if type(row.get('ExplicitTenderStatus')) is not int or row['ExplicitTenderStatus']!=0:raise NotSent('ADMISSION45_LOT_SUMMARY_STATUS_REQUIRES_CONFIRMATION')
                child=self.api.call('GetPublishedTenderDetails',{'tenderId':lot,'ianaTimeZone':'Europe/Sofia'})
                facts.append(open_detail(child,lot,parent,now))
        return {'profile':PROFILE,'state':'OPEN','parent':parent,'lots':list(lots),
            'verified_at':self.clock().isoformat(),'official_reference':'https://app.eop.bg/today/'+str(parent),
            'parent_fact':parent_fact,'lot_facts':facts,'source_receipts':self.api.receipts[offset:],
            'meaning':'CURRENT_PUBLIC_SUBMISSION_STATE_AT_ACCEPTANCE_NOT_REALTIME_GUARANTEE'}
