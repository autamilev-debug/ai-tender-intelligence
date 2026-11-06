"""Container configuration/process controls; no providers, OAuth or database I/O."""
from types import SimpleNamespace
import signal
import pytest
from ai_tender_intelligence import container_runtime as runtime
from ai_tender_intelligence.persistence.database import DatabaseSettings,PersistenceError


def options(**kwargs):
    return SimpleNamespace(health_only=False,enable_paid_beta=False,**kwargs)


def test_default_bot_command_never_enables_paid_work():
    commands=runtime.commands(options(),{'DATABASE_URL':'synthetic','TELEGRAM_BOT_TOKEN':'synthetic'})
    assert len(commands)==2 and '--connect' in commands[0]
    assert '--enable-paid-beta' not in commands[0]
    assert commands[1][-2:]==['--workers','1']
    assert '--install' not in str(commands) and '--setup' not in str(commands)


def test_explicit_paid_flag_preserves_existing_oauth_path():
    args=options();args.enable_paid_beta=True
    env=dict(DATABASE_URL='synthetic',TELEGRAM_BOT_TOKEN='synthetic',OPENAI_API_KEY='synthetic',
             GMAIL_SENDER='sender@example.invalid',GOOGLE_CLIENT_JSON_PATH='/run/secrets/oauth.json')
    commands=runtime.commands(args,env)
    assert commands[0][-3:]==['--enable-paid-beta','--client-json','/run/secrets/oauth.json']
    assert 'synthetic' not in str(commands)


@pytest.mark.parametrize('value',['0','65536','x','8000;echo','１２３'])
def test_invalid_port_fails_before_process_start(value):
    with pytest.raises(ValueError):runtime.port({'PORT':value})


def test_health_only_does_not_require_or_start_integrations():
    args=options();args.health_only=True
    commands=runtime.commands(args,{'PORT':'8123'})
    assert len(commands)==1 and 'uvicorn' in commands[0] and '8123' in commands[0]
    args.enable_paid_beta=True
    with pytest.raises(ValueError):runtime.commands(args,{})


def test_missing_configuration_is_sanitized(monkeypatch,capsys):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','do-not-echo-this')
    assert runtime.main([])==2
    assert 'do-not-echo-this' not in capsys.readouterr().err


def test_remote_database_requires_explicit_exact_host_and_safe_query():
    url='postgresql+psycopg://user@postgres/test'
    with pytest.raises(PersistenceError):DatabaseSettings(url)
    with pytest.raises(PersistenceError):DatabaseSettings(url,allowed_host='different')
    configured=DatabaseSettings.from_environment({'DATABASE_URL':url,'P5_DATABASE_HOST':'postgres'})
    assert configured.allowed_host=='postgres'
    assert DatabaseSettings(url+'?sslmode=require',allowed_host='postgres')
    for query in ('?options=unsafe','?sslmode=invalid','?sslmode=require&host=other'):
        with pytest.raises(PersistenceError):DatabaseSettings(url+query,allowed_host='postgres')
    assert DatabaseSettings('postgresql+psycopg://user@127.0.0.1/test')


def test_child_failure_stops_sibling_without_restart(monkeypatch):
    class Child:
        def __init__(self,done):self.done=done;self.signals=[]
        def poll(self):return 2 if self.done else None
        def send_signal(self,sig):self.signals.append(sig)
        def wait(self,timeout=None):self.done=True;return 0
    failed,other=Child(True),Child(False)
    children=iter((failed,other));started=[]
    def launch(command):started.append(command);return next(children)
    monkeypatch.setattr(runtime.subprocess,'Popen',launch)
    monkeypatch.setattr(runtime.signal,'signal',lambda *a:signal.SIG_DFL)
    assert runtime.supervise([['bot'],['health']])==2
    assert len(started)==2 and other.signals==[signal.SIGINT]
