"""Prepared MIME and one-shot Gmail API transport. No OAuth/key lookup on import."""
import base64,json,re,os
from urllib.parse import quote
from email.message import EmailMessage
from email.policy import SMTP
from email.parser import BytesParser
from hashlib import sha256
import httpx
from ..telegram.models import TEST_RECIPIENT,InteractionError

SENDER=os.environ.get('GMAIL_SENDER','sender@example.invalid')
ENDPOINT='https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
REVIEWED_SEND_DIGEST=None  # Separate exact owner delivery approval required.


def prepare(html,plain,run_id,*,subject=None,recipient=TEST_RECIPIENT):
    _recipient(recipient)
    subject=subject or 'ВЪТРЕШЕН ТЕСТ — исторически анализ на поръчка ЕОП564566'
    message=EmailMessage(policy=SMTP);message['From']=SENDER;message['To']=recipient
    message['Subject']=subject;message['Message-ID']='<p41-'+str(run_id)+'@ai-tender.local.invalid>'
    message.set_content(plain);message.add_alternative(html,subtype='html')
    # Stable MIME for owner approval and retry prevention, not a Gmail idempotency promise.
    message.set_boundary('p41-'+str(run_id));raw=message.as_bytes()
    return {'subject':subject,'html':html,'plain':plain,'mime':raw,'mime_sha256':sha256(raw).hexdigest()}


def _recipient(value):
    if not isinstance(value,str) or len(value)>254 or not re.fullmatch(r'[^@\s<>;,]+@[^@\s<>;,]+\.[^@\s<>;,]+',value):
        raise InteractionError('Невалиден имейл адрес за отчета.')
    return value


def check_message(raw,*,recipient=TEST_RECIPIENT):
    _recipient(recipient)
    message=BytesParser(policy=SMTP).parsebytes(raw)
    if (message.get_all('To')!=[recipient] or message.get_all('From')!=[SENDER]
        or any(message.get_all(k) for k in ['Cc','Bcc','Resent-To','Resent-Cc','Resent-Bcc'])
        or not message.is_multipart()):raise InteractionError('Разрешен е само подготвеният вътрешен тестов имейл.')


class GmailFailure(Exception):
    def __init__(self,state):self.state=state;super().__init__('Gmail test submission failed safely.')


class Gmail:
    endpoint=ENDPOINT
    def __init__(self,token,*,transport=None,recipient=TEST_RECIPIENT):
        if not isinstance(token,str) or not token or any(c.isspace() for c in token):raise InteractionError('Липсва валиден временен Gmail токен.')
        if transport is not None and not isinstance(transport,httpx.MockTransport):raise ValueError('Only mocked transport injection allowed')
        self.token=token;self.transport=transport;self.is_mock=isinstance(transport,httpx.MockTransport)
        self.recipient=_recipient(recipient)
        self.response=None
    def submit(self,raw):
        check_message(raw,recipient=self.recipient)
        try:
            with httpx.Client(transport=self.transport,timeout=httpx.Timeout(30,connect=5),trust_env=False,follow_redirects=False) as client:
                with client.stream('POST',self.endpoint,headers={'Authorization':'Bearer '+self.token},
                    json={'raw':base64.urlsafe_b64encode(raw).decode()}) as response:
                    if response.status_code in {400,401,403,404,429}:raise GmailFailure('EMAIL_REJECTED')
                    if response.status_code!=200:raise GmailFailure('EMAIL_UNKNOWN')
                    body=b''
                    for block in response.iter_bytes():
                        body+=block
                        if len(body)>65536:raise GmailFailure('EMAIL_UNKNOWN')
                    data=json.loads(body)
                    if (not isinstance(data,dict) or not isinstance(data.get('id'),str) or 'error' in data
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',data['id'])):raise GmailFailure('EMAIL_UNKNOWN')
                    self.response=data
                    return data['id']
        except GmailFailure:raise
        except Exception:raise GmailFailure('EMAIL_UNKNOWN') from None
        finally:self.token=None


def send(store,user,company,run_id,mime_hash,gmail,*,_beta_permit=None):
    # Mock success is stored distinctly; it never proves a real external send.
    recipient=TEST_RECIPIENT
    if _beta_permit is not None:
        from ..beta67.delivery import SendPermit
        if type(_beta_permit) is not SendPermit:raise InteractionError('Невалидно разрешение за изпращане.')
        recipient=_beta_permit.verify(store,user,company,run_id,mime_hash)
        if gmail.recipient!=recipient:raise InteractionError('Невалиден получател.')
    elif not gmail.is_mock and REVIEWED_SEND_DIGEST!=mime_hash:
        raise InteractionError('Изпращането изисква изрично одобрение на точния имейл.')
    row=store.read(user,company,run_id);check_message(bytes(row['mime']),recipient=recipient)
    row=store.reserve_send(user,company,run_id,mime_hash)  # Commit before external side effect.
    try:
        ident=gmail.submit(bytes(row['mime']))
        store.sent(user,company,run_id,'MOCK_EMAIL_ACCEPTED' if gmail.is_mock else 'EMAIL_ACCEPTED',ident)
    except GmailFailure as error:
        store.sent(user,company,run_id,error.state)
        raise InteractionError('Изпращането е отхвърлено или резултатът е неизвестен. Няма автоматично повторение.') from None
    return store.read(user,company,run_id)


class SenderGmail(Gmail):
    """Bind Google's authenticated userId to the configured sending account."""
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.endpoint='https://gmail.googleapis.com/gmail/v1/users/'+quote(_recipient(SENDER),safe='')+'/messages/send'
