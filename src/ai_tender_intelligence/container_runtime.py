"""One foreground Beta process plus the existing liveness API; no restart/replay."""
import argparse
import os
import runpy
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


def port(environ):
    value=environ.get('PORT','8000')
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError('PORT must be an integer between 1 and 65535.')
    return str(int(value))


def commands(args,environ):
    http=[sys.executable,'-m','uvicorn','ai_tender_intelligence.api:create_app','--factory',
          '--host','0.0.0.0','--port',port(environ),'--workers','1']
    if args.health_only:
        if args.enable_paid_beta:raise ValueError('Health-only mode cannot enable paid execution.')
        return [http]
    missing=[name for name in ('DATABASE_URL','TELEGRAM_BOT_TOKEN') if not environ.get(name)]
    if missing:raise ValueError('Container startup requires '+', '.join(missing)+'.')
    bot=[sys.executable,'-m','ai_tender_intelligence.beta67','--connect']
    if args.enable_paid_beta:
        from .demo41.oauth42 import OAuthFailure, RefreshGrant, oauth_mode
        required=('OPENAI_API_KEY','GMAIL_SENDER')
        if any(not environ.get(name) for name in required):
            raise ValueError('Paid Beta requires OPENAI_API_KEY and GMAIL_SENDER.')
        bot.append('--enable-paid-beta')
        try:
            if oauth_mode(environ)=='headless':
                RefreshGrant.from_environment(environ).close()
            elif environ.get('GOOGLE_CLIENT_JSON_PATH'):
                bot+=['--client-json',environ['GOOGLE_CLIENT_JSON_PATH']]
            else:
                raise OAuthFailure('OAUTH_EXTERNAL_DESKTOP_FILE_REQUIRED')
        except OAuthFailure as error:
            raise ValueError('Gmail configuration failed ('+str(error)+').') from None
    return [bot,http]


def supervise(programs,grace=25):
    children=[];stopping=False;previous={}
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
    for signum in (signal.SIGTERM,signal.SIGINT):
        previous[signum]=signal.signal(signum,stop)
    result=0
    try:
        for program in programs:
            children.append(subprocess.Popen(program))
        print('Container started: single-worker mode; migrations are never automatic.',flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                # An unexpected child exit must not leave a misleading healthy API.
                result=2
                print('Application process exited; stopping container without restart.',flush=True)
                break
            time.sleep(.2)
    finally:
        deadline=time.monotonic()+grace
        for child in children:
            if child.poll() is None:child.send_signal(signal.SIGINT)
        for child in children:
            try:child.wait(timeout=max(.1,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                print('Shutdown grace expired; persisted attempts must not be replayed.',flush=True)
                child.kill();child.wait();result=2
        for signum,handler in previous.items():signal.signal(signum,handler)
        print('Container stopped; no automatic replay.',flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--health-only',action='store_true',help='Offline liveness server; no bot or providers')
    mode.add_argument('--check',action='store_true',help='Read-only database readiness; no external APIs')
    mode.add_argument('--setup',action='store_true',help='Explicit administrator schema/catalogue setup')
    mode.add_argument('--offline-check',action='store_true',help='Verify installed image assets without networking')
    mode.add_argument('--probe',action='store_true')
    parser.add_argument('--enable-paid-beta',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.enable_paid_beta and any((args.check,args.setup,args.offline_check,args.probe)):
            raise ValueError('Paid activation is only valid for the foreground bot.')
        if args.probe:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+port(os.environ)+'/health/live',timeout=2) as response:
                return 0 if response.status==200 else 1
        if args.offline_check:
            runpy.run_path('/opt/checks/check_installed_package.py',run_name='__main__');return 0
        if args.setup:
            from .bootstrap import main as setup
            return setup(['--apply'])
        if args.check:
            from .beta67.__main__ import main as beta
            return beta(['--check'])
        programs=commands(args,os.environ)
        if not args.health_only:
            from .beta67.__main__ import main as beta
            if beta(['--check'])!=0:return 2
        return supervise(programs)
    except (Exception,KeyboardInterrupt) as error:
        if type(error) is ValueError:
            # All ValueErrors created here contain only constant configuration names.
            print(str(error),file=sys.stderr)
        else:print('Container operation failed ('+type(error).__name__+'); credentials are not logged.',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
