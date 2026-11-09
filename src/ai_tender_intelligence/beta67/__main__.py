"""Explicit local three-account open-registration Beta. Help/check never reads API/OAuth credentials."""
from ai_tender_intelligence.assets import root as asset_root
import argparse
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import text
from ..persistence.database import Database,DatabaseSettings
from ..telegram.preview import Preview,UTCClock
from ..telegram.transport import TelegramHTTP,PreviewSettings
from ..telegram.search_preview import preflight,DisabledPort
from ..discovery.http import PublicHTTP
from ..discovery.fresh import FreshSearch
from ..discovery.fresh_models import SearchLimits
from ..discovery.ted import TED
from ..discovery.eop import EOP
from ..search_results.service import SearchResults
from ..acquisition.http import DocHTTP
from ..acquisition.store import EvidenceStore
from ..analysis.sources import FreshSources
from ..analysis.admission45 import EOPAdmission
from ..analysis.runtime import RuntimeWorkflow
from ..analysis.provider import RuntimeProvider
from ..classification.openai_provider import OpenAISettings
from ..demo41 import oauth42,mail
from ..demo41.mail import SenderGmail
from ..persistence.identity import PublicTelegramUsers
from .schema import install
from .store import Access,Profiles,BetaStore
from .service import Coordinator
from .telegram import Search,BetaInteraction
from .classification import Luna,LunaWorker

ROOT=asset_root()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--install',action='store_true')
    mode.add_argument('--connect',action='store_true')
    parser.add_argument('--enable-paid-beta',action='store_true')
    parser.add_argument('--client-json')
    a=parser.parse_args(argv)
    if not any((a.check,a.install,a.connect)):parser.print_help();return 0
    db=public=coordinator=client=grant=None
    try:
        db=Database(DatabaseSettings.from_environment())
        if a.install:
            install(db)
            print('Additive Beta schema/invitations installed. No jobs or external calls.');return 0
        allowed=Access(db).registered()
        from sqlalchemy.engine import make_url
        preflight(db,next(iter(allowed),1),make_url(db.settings.url).database)
        with db.transaction() as s:
            if s.scalar(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user")):raise ValueError('RESTRICTED_RUNTIME_ROLE_REQUIRED')
            for table in ('beta67_runs','beta67_requests','document_extractions'):
                if s.scalar(text('SELECT to_regclass(:name)'),{'name':'public.'+table}) is None:
                    raise ValueError('BETA67_SCHEMA_NOT_INSTALLED')
        if a.check:
            print('Three-account open-registration Beta readiness passed. No live API calls or sending.');return 0
        profiles=Profiles(db,allowed);store=BetaStore(db,allowed);clock=UTCClock()
        settings=OpenAISettings()
        if a.enable_paid_beta:
            mail._recipient(mail.SENDER)
            if mail.SENDER.endswith('.invalid'):raise ValueError('GMAIL_SENDER_REQUIRED')
            settings=OpenAISettings(True,os.environ.pop('OPENAI_API_KEY',None))
            if oauth42.oauth_mode(os.environ)=='headless':
                grant=oauth42.RefreshGrant.from_environment()
                grant.access_token()
            else:
                if not a.client_json:raise oauth42.OAuthFailure('OAUTH_EXTERNAL_DESKTOP_FILE_REQUIRED')
                grant=oauth42.authorize(Path(a.client_json),ROOT)
        token=PreviewSettings.from_environment({'TELEGRAM_BOT_TOKEN':os.environ.pop('TELEGRAM_BOT_TOKEN',''),
                    'TELEGRAM_ALLOWED_USER_IDS':'1'}).token
        public=PublicHTTP()
        def limits():
            today=clock.now().date()
            return SearchLimits(today-timedelta(days=3),today-timedelta(days=1))
        results=SearchResults(db,allowed,FreshSearch(db,allowed,TED(public),EOP(public),clock=clock.now))
        search=Search(results,profiles,allowed,limits)
        def flow_factory(user,company):
            Access(db).guard(user)
            http=DocHTTP()
            sources=FreshSources(SearchResults(db,allowed),EvidenceStore(db,allowed),http,
                admission=EOPAdmission(http),http_factory=DocHTTP)
            original_close=sources.close
            def close_sources():
                try:original_close()
                finally:http.close()
            sources.close=close_sources
            return (RuntimeWorkflow(store,sources,RuntimeProvider(),internal_beta=True),
                    RuntimeProvider(settings=settings))
        def gmail_factory(recipient):
            if grant is None:raise ValueError('BETA67_GMAIL_AUTHORIZATION_REQUIRED')
            try:
                token=grant.access_token()
            except oauth42.OAuthFailure as error:
                print('Gmail authorization failed ('+str(error)+').')
                raise
            return SenderGmail(token,recipient=recipient)
        def luna_factory(store,requests,user,company,ident,intent):
            return LunaWorker(store,requests,user,company,ident,intent,Luna(settings))
        coordinator=Coordinator(store,flow_factory,gmail_factory,enabled=a.enable_paid_beta,luna_factory=luna_factory)
        ports=SimpleNamespace(profiles=profiles,classifications=profiles,search=search,
                              history=DisabledPort(),analyses=DisabledPort(),deliveries=DisabledPort())
        ui=BetaInteraction(ports,clock,set(allowed) or {1},coordinator=coordinator)
        client=TelegramHTTP(token);token=None
        print('Three-account open-registration internal Beta running. One processing slot; no model/send retries. Ctrl+C stops.')
        Preview(client,ui,clock,PublicTelegramUsers(),banner='',demo_banner='').run()
        return 0
    except oauth42.OAuthFailure as error:
        print('Beta stopped during Gmail authorization ('+str(error)+').')
        return 2
    except (Exception,KeyboardInterrupt) as error:
        print('Beta stopped safely ('+type(error).__name__+'). No automatic replay; inspect persisted job diagnostics.')
        return 2
    finally:
        if coordinator:coordinator.close()
        if grant:grant.close()
        if client:client.close()
        if public:public.close()
        if db:db.close()


if __name__=='__main__':raise SystemExit(main())
