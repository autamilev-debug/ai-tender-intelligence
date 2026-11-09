"""One background processing slot over the existing P66 analysis pipeline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from queue import SimpleQueue, Empty
import logging
from copy import deepcopy
from ..analysis.control45 import validate_approval
from ..semantic.retrieval import digest
from ..demo41 import mail
from ..telegram.models import InteractionError
from .store import Requests
from .delivery import SendPermit

STARTED='Анализът започна.\n\nПолучихме заявката ви и подготвяме документацията. Обработката обикновено отнема около 15–20 минути, но е възможно и повече. Ще получите имейл, когато докладът е готов.'
PROCESSING='Анализът се обработва.\n\nДокументите се анализират. Не е необходимо да изчаквате в Telegram.'
COMPLETED='Анализът е готов.\n\nБета докладът е изпратен на посочения имейл адрес.'
FAILED='Обработката не беше завършена или изпращането не е потвърдено. Няма автоматично повторение. Свържете се с организатора на Бета теста.'


class Coordinator:
    def __init__(self,store,flow_factory,gmail_factory,*,enabled=False,luna_factory=None,executor=None):
        if type(enabled) is not bool:raise ValueError('BETA67_EXPLICIT_ACTIVATION_REQUIRED')
        self.store,self.requests=store,Requests(store)
        self.flow_factory,self.gmail_factory,self.luna_factory=flow_factory,gmail_factory,luna_factory
        self.enabled=enabled;self.events=SimpleQueue()
        self.executor=executor or ThreadPoolExecutor(max_workers=1,thread_name_prefix='p5-beta')
    def _enabled(self):
        if not self.enabled:raise InteractionError('Платената Бета обработка не е активирана.')
    def submit(self,user,company,selection,key):
        self._enabled()
        ident=self.requests.reserve(user,company,key)
        try:self.executor.submit(self._analysis,user,company,deepcopy(selection),ident)
        except Exception:
            self.requests.finish(user,company,ident,False,{'code':'BACKGROUND_START_FAILED'})
            raise InteractionError(FAILED) from None
        return ident
    def _analysis(self,user,company,selection,ident):
        flow=row=None;ok=False;diagnostic={'kind':'ANALYSIS','selection':deepcopy(selection)}
        try:
            flow,provider=self.flow_factory(user,company)
            original=flow.sources.prepare;announced=False
            def prepare(*a,**kw):
                nonlocal announced
                result=original(*a,**kw)
                if not announced:self.events.put((user,'PROCESSING'));announced=True
                return result
            flow.sources.prepare=prepare
            row=flow.run(user,company,selection,'beta67:'+ident)
            self.requests.bind(user,company,ident,row)
            p=row['evidence']['AI_PLAN45']['plan']
            if (not p.get('internal_beta') or p['selection']!=selection or str(p['job'])!=str(row['id'])
                or p['actor']!=user or p['company']!=str(company)):
                raise ValueError('BETA67_PLAN_MISMATCH')
            approval={'explicit_owner_approval':True,'actor':user,'company':str(company),'job':str(row['id']),
                'maximum_requests':9,'gmail_allowed':False,'plan_digest':digest(p),
                'expires_at':(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()}
            validate_approval(p,approval)
            flow.store.stage(user,company,row['id'],'BETA67_AUTHORITY',{
                'request':ident,'plan_digest':digest(p),'ai_approval':approval,'maximum_sends':1})
            flow.sources.admit(user,company,selection)  # fresh official OPEN; never bypass admission
            flow.provider=provider
            row=flow.execute_prepared(user,company,row['id'],approval)
            permission=self.requests.read(user,company,ident)
            recipient=flow.store.recipient_for(user,company,row['id'])
            if permission['recipient']!=recipient:raise ValueError('BETA67_RECIPIENT_CHANGED')
            permit=SendPermit(ident,user,str(company),str(row['id']),row['mime_sha256'],recipient)
            gmail=self.gmail_factory(recipient)
            row=mail.send(flow.store,user,company,row['id'],row['mime_sha256'],gmail,_beta_permit=permit)
            ok=row['state'] in {'EMAIL_ACCEPTED','MOCK_EMAIL_ACCEPTED'}
            diagnostic.update(job=str(row['id']),state=row['state'],gmail_id=row['gmail_id'],
                              mime_sha256=row['mime_sha256'],gmail_response=gmail.response)
        except BaseException as error:
            logging.getLogger(__name__).warning('Beta analysis request %s stopped: %s',ident,type(error).__name__)
            diagnostic['error_type']=type(error).__name__
            value=str(error)
            if value and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789:' for c in value):diagnostic['code']=value
            if row is not None:
                diagnostic['job']=str(row['id'])
                try:self.store.failed(user,company,row['id'])
                except Exception:pass
        finally:
            if flow is not None:
                try:flow.sources.close()
                except Exception:pass
            try:self.requests.finish(user,company,ident,ok,diagnostic)
            except Exception:
                ok=False  # uncertain persistence never emits success
                logging.getLogger(__name__).error('Beta request %s finalization requires operator review',ident)
            self.events.put((user,'COMPLETED' if ok else 'FAILED'))
    def classify(self,user,company):
        if not self.enabled or self.luna_factory is None:return False
        intent=self.store.profiles.pending_intent(user,company)
        if intent is None:return False
        try:ident=self.requests.reserve(user,company,'intent:'+intent.id,intent=intent.id)
        except InteractionError:return False
        try:self.executor.submit(self._classify,user,company,ident,intent.id)
        except Exception:
            self.requests.finish(user,company,ident,False,{'code':'BACKGROUND_START_FAILED'})
        return True
    def _classify(self,user,company,ident,intent):
        ok=False;diagnostic={'kind':'CPV'}
        try:
            worker=self.luna_factory(self.store,self.requests,user,company,ident,intent)
            outcome=worker.run_once();ok=outcome=='succeeded';diagnostic['outcome']=outcome
        except BaseException as error:diagnostic['error_type']=type(error).__name__
        finally:
            try:self.requests.finish(user,company,ident,ok,diagnostic)
            finally:self.events.put((user,'CPV_FINISHED' if ok else 'CPV_FAILED',str(company),str(intent)))
    def drain(self):
        result=[]
        while True:
            try:result.append(self.events.get_nowait())
            except Empty:return result
    def close(self):self.executor.shutdown(wait=True,cancel_futures=False)
