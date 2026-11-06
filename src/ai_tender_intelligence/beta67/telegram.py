"""Existing profile wizard and tender cards with invited-user Beta actions."""
import re
from dataclasses import replace
from datetime import timedelta
from html import escape
from ..telegram.interaction import Interaction
from ..telegram.models import InteractionError, Button
from ..telegram.search_preview import SearchOnlyInteraction, LiveOwnerSearch, SEARCH_WAIT
from ..analysis.sources import procedure_mapping
from ..classification.contract import NotSent
from . import service
from ..persistence.identity import freeze_users,PublicTelegramUsers


class Search(LiveOwnerSearch):
    def __init__(self,results,profiles,allowed,limits_factory):
        super().__init__(results,profiles,None,limits_factory);self.allowed=freeze_users(allowed)
    def start_search(self,user,company,request_key):
        # Reuse the existing source/search/snapshot implementation without its
        # old single-owner composition restriction or shared mutable owner.
        if user not in self.allowed:raise InteractionError('Невалидно действие.')
        c=self.profiles.company(user,company)
        if c.classification!='ready' or self.profiles.classification_pending(user,company):
            raise InteractionError('Профилът още се подготвя. Изчакайте CPV класификацията.')
        waiting=None
        def announce():
            nonlocal waiting
            if self.feedback:waiting=self.feedback(user,SEARCH_WAIT)
        try:
            stored=self.results.execute(user,company,self.limits_factory(),request_key='beta67-search:'+request_key,before_fetch=announce)
            self.bind(user,company,stored.id)
        except Exception:
            if waiting:self.feedback(user,'Търсенето не беше завършено. Опитайте отново от менюто.',waiting)
            raise InteractionError('Търсенето не беше завършено. Опитайте отново от менюто.') from None
        if waiting:self.feedback(user,'Търсенето приключи.',waiting)


class BetaInteraction(Interaction):
    def __init__(self,*a,coordinator,**kw):
        super().__init__(*a,**kw);self.allowed_users=PublicTelegramUsers();self.coordinator=coordinator;self.event_ids={}
    def bind_publisher(self,publish):SearchOnlyInteraction.bind_publisher(self,publish)
    def _message(self,user,text,*a,**kw):
        self.session_notified.add(user)  # Real internal Beta, not a synthetic UI.
        kw.pop('demo',None)
        return super()._message(user,text,*a,**kw)
    def handle(self,event):
        if event.user_id in self.allowed_users:self.event_ids[event.user_id]=event.update_id
        return super().handle(event)
    def _start(self,user):
        self.services.profiles.register(user)
        for c in self.services.profiles.companies(user):
            if self.services.profiles.classification_pending(user,c.id):
                self.classification_watch[(user,c.id)]=(self.clock.now(),False,False)
        return super()._start(user)
    def _menu(self,user,company=None):
        messages=super()._menu(user,company)
        current=company or self.selected.get(user)
        if not current:return messages
        result=[]
        for m in messages:
            rows=[]
            for row in m.keyboard:
                buttons=[]
                for button in row:
                    a=self.actions.get(button.callback_data)
                    if a and a.name=='menu' and a.target=='2':continue
                    if a and a.name=='menu' and a.target=='1':button=replace(button,text='Моите доклади')
                    buttons.append(button)
                if buttons:rows.append(tuple(buttons))
            rows.insert(1,(self._button(user,'Моят профил','view_profile',company=current),))
            result.append(replace(m,keyboard=tuple(rows)))
        return result
    def _wizard_prompt(self,user,**kw):
        messages=super()._wizard_prompt(user,**kw)
        if self.wizards[user].step=='email':
            messages=[replace(m,text='Въведете имейл за Бета докладите. Проверете адреса внимателно при потвърждението.') for m in messages]
        return messages
    def _text(self,user,text):
        w=self.wizards.get(user)
        if w and w.mode=='field' and w.step=='email':
            w.email=self._email(text);w.step='confirm_email'
            w.base_revision=self.services.profiles.company(user,w.company).revision
            return [self._message(user,'Потвърдете новия имейл: '+w.email,[[
                self._button(user,'Потвърди имейл','confirm_email',company=w.company),
                self._button(user,'Отказ','cancel',company=w.company)]])]
        match=re.fullmatch(r'/analyze44 ([1-9]|1[0-5])',text)
        if text.startswith('/analyze44'):
            if not match:raise InteractionError('Изберете „Подробен анализ“ или /analyze44 и номер от последното търсене.')
            company=self.selected.get(user)
            if not company:raise InteractionError('Първо изберете фирма и направете търсене.')
            reference=self.services.search.snapshot_reference(user,company)
            return self._analyze(user,company,reference,int(match[1])-1,'telegram:'+str(self.event_ids[user]))
        return super()._text(user,text)
    def _dispatch(self,user,a):
        if a.name=='view_profile':
            from ..telegram.rendering import profile_review_parts
            fields=self.services.profiles.company(user,a.company).fields
            return [self._message(user,text,html=html) for text,html in profile_review_parts(fields)]+self._menu(user,a.company)
        if a.name=='confirm_email':
            w=self.wizards.get(user)
            if not w or w.step!='confirm_email' or w.company!=a.company:raise InteractionError('Невалидно потвърждение.')
            self.services.profiles.edit_fields(user,w.company,email=w.email,expected_revision=w.base_revision)
            self.wizards.pop(user)
            return [self._message(user,'Имейлът е потвърден и запазен.')]+self._menu(user)
        if a.name=='deep':
            sid,tender=a.target.split('/',1);snap=self.snapshots.get(sid)
            if (snap is None or snap.owner!=user or snap.company!=a.company or not snap.stored_reference
                or self.clock.now()>=snap.created+timedelta(minutes=30)):
                raise InteractionError('Резултатите са изтекли. Направете ново търсене.')
            tenders=self.services.search.stored_results(user,a.company,snap.stored_reference)
            index=next((i for i,t in enumerate(tenders) if t.id==tender),None)
            if index is None:raise InteractionError('Невалидна поръчка.')
            return self._analyze(user,a.company,snap.stored_reference,index,'button:'+a.operation_id)
        if a.name in {'historical','retry','received','not_received','save','remove'}:
            raise InteractionError('Повторно изпращане и повторни анализи не са включени в този Бета тест.')
        return super()._dispatch(user,a)
    def _analyze(self,user,company,reference,index,key):
        stored=self.services.search.results.read(user,company,reference)
        opportunities=stored.result.matches.opportunities
        if not 0<=index<len(opportunities):raise InteractionError('Няма такава поръчка.')
        o=opportunities[index];payload=stored.source_payloads.get(o.record_id,{})
        try:parent,single=procedure_mapping(payload,o)
        except NotSent:raise InteractionError('Позициите на тази поръчка не могат да бъдат потвърдени.') from None
        relevant={l.lot_id for l in o.lots}
        lots=[l.get('source_fields',{}).get('tenderId') for l in payload.get('lots',[]) if l['id'] in relevant]
        if relevant=={'PROCEDURE'} or single:lots=[]
        selection={'search_id':reference,'record_id':o.record_id,'parent':parent,'lots':lots}
        self.coordinator.submit(user,company,selection,key)
        return [self._message(user,service.STARTED)]
    def _card(self,user,company,tender,snapshot,**kw):
        message=super()._card(user,company,tender,snapshot,**kw)
        rows=[(self._button(user,'Подробен анализ','deep',snapshot+'/'+tender.id,company),)]
        rows.extend(tuple(b for b in row if b.url) for row in message.keyboard if any(b.url for b in row))
        return replace(message,keyboard=tuple(rows))
    def _menu_action(self,user,company,choice,**kw):
        if choice in {1,2}:
            entries=self.coordinator.store.entries(user,company)
            labels={'EMAIL_ACCEPTED':'Имейлът е изпратен','MOCK_EMAIL_ACCEPTED':'Само тестово изпращане',
                    'FAILED':'Не е завършен','EMAIL_UNKNOWN':'Изпращането не е потвърдено',
                    'EMAIL_REJECTED':'Изпращането е отказано'}
            text='\n'.join(str(e['created_at'].date())+' — '+labels.get(e['state'],'Обработва се') for e in entries)
            return [self._message(user,text or 'Все още няма доклади за тази фирма.')]+self._menu(user)
        return super()._menu_action(user,company,choice,**kw)
    def poll(self):
        messages=[];finished_profiles=set()
        for (user,cid),(started,edit,slow) in list(self.classification_watch.items()):
            try:
                if self.services.profiles.classification_pending(user,cid):
                    self.coordinator.classify(user,cid);continue
                self.classification_watch.pop((user,cid),None)
                finished_profiles.add((user,str(cid)))
                good=self.services.profiles.classification_result(user,cid)=='success'
                messages.append(self._message(user,'Профилът е готов. Можете да търсите поръчки.' if good else
                    'Обработката на дейностите не беше завършена. Свържете се с организатора на Бета теста.'))
            except InteractionError:self.classification_watch.pop((user,cid),None)
        for event in self.coordinator.drain():
            user,state=event[:2]
            if state=='CPV_FAILED':
                self.classification_watch.pop((user,event[2]),None)
                if (user,event[2]) not in finished_profiles:
                    messages.append(self._message(user,'Обработката на дейностите не беше завършена. Свържете се с организатора на Бета теста.'))
            if state in {'PROCESSING','COMPLETED','FAILED'}:
                messages.append(self._message(user,getattr(service,state)))
        return messages
