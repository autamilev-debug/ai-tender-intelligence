"""Shared local source readiness and search progress adapters."""
from sqlalchemy import select,text
from ..catalogue.validation import CatalogueReader
from ..persistence.profiles import PostgresProfiles
from ..persistence.schema import searches
from ..discovery.storage import notices
from ..search_results import telegram as bridge_module
from .interaction import Interaction
from .models import InteractionError
from .transport import PreviewError


BANNER = '🧪 Локален тест — реални официални източници. Подробният анализ и имейлът са изключени.\n'

REQUIREMENT = 'За този локален тест е необходим съществуващ готов бизнес профил с одобрени CPV кодове в избраната развойна база. Няма да бъде стартирана платена AI класификация.'

DISABLED = 'Тази операция не е включена в локалния тест за търсене. Подробният анализ, имейлът и промените по профили са изключени.'

SEARCH_WAIT = ('🔎 <b>Търсим подходящи обществени поръчки за вашата фирма.</b>\n\n'
               'Проверката може да отнеме известно време. Моля, изчакайте — резултатите ще се появят тук, когато търсенето приключи.')

def check_gates():
    if bridge_module.PRODUCTION_SEARCH_ENABLED or bridge_module.ANALYSIS_ADMISSION_ENABLED:
        raise PreviewError('Local search requires the production and analysis gates to remain disabled.')

def preflight(db,owner,expected_database):
    check_gates()
    with db.transaction() as s:
        actual=s.scalar(text('SELECT current_database()'))
        if actual != expected_database or actual.lower() in {'p5_luna_eval','p5_luna_v3_batch'}:
            raise PreviewError('Development database identity mismatch; retained data was not migrated.')
        if (s.scalar(text('SELECT version_num FROM alembic_version'))!='0004_searches'
                or s.scalar(text('SELECT version_num FROM discovery_schema_version'))!='d0001_discovery'):
            raise PreviewError('Development schema must already be at0004_searches/d0001_discovery; startup never migrates.')
        s.execute(select(searches.c.id).limit(0));s.execute(select(notices.c.id).limit(0))
    CatalogueReader(db).snapshot()
    profiles=PostgresProfiles(db,frozenset({owner}))
    companies=profiles.companies(owner)
    return sum(c.classification=='ready' and bool(c.cpv) and not profiles.classification_pending(owner,c.id) for c in companies)

class DisabledPort:
    """No simulated completion/saving/email and no paid-provider implementation."""
    def __getattr__(self,name):
        def reject(*a,**k):raise InteractionError(DISABLED)
        return reject

class LiveOwnerSearch(bridge_module.StoredTelegramSearch):
    def __init__(self,results,profiles,owner,limits_factory):
        super().__init__(results,development_preview=True)
        self.profiles,self.owner,self.limits_factory=profiles,owner,limits_factory
        self.feedback = None

    def start_search(self,user,company,request_key):
        check_gates()
        if type(user) is not int or user!=self.owner:raise InteractionError('Невалидно действие.')
        c=self.profiles.company(user,company)
        if c.classification!='ready' or not c.cpv or self.profiles.classification_pending(user,company):
            raise InteractionError(REQUIREMENT)
        waiting = None
        def announce():
            nonlocal waiting
            if self.feedback:
                waiting = self.feedback(user,SEARCH_WAIT)
        try:
            try:
                stored=self.results.execute(user,company,self.limits_factory(),request_key='telegram-search:'+request_key,
                                            before_fetch=announce)
                self.bind(user,company,stored.id)
            except InteractionError:
                raise
            except ValueError:
                raise InteractionError('Търсенето не е достъпно. Проверете готовността на профила и одобрените CPV кодове; няма да бъде стартирана AI класификация.') from None
        except BaseException:
            if waiting:
                self.feedback(user,'Търсенето не беше завършено. Използвайте менюто за нова заявка.',waiting)
            raise
        else:
            if waiting:
                self.feedback(user,'✅ Търсенето приключи.',waiting)

class SearchOnlyInteraction(Interaction):
    """A capability boundary over the existing state machine, not another one."""
    def bind_publisher(self,publish):
        def feedback(user,text,previous=None):
            message=self._message(user,text,edit_of=previous.id if previous else None,html=True)
            publish([message])
            return message
        self.services.search.feedback=feedback

    def _start(self,user):
        if not self.services.profiles.companies(user):
            return [self._message(user,REQUIREMENT)]
        return super()._start(user)

    def _dispatch(self,user,a):
        if a.name in {'new','edit','confirm','deep','retry','historical','received','not_received'}:
            raise InteractionError(REQUIREMENT if a.name=='new' else DISABLED)
        return super()._dispatch(user,a)
