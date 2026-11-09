"""Mock Google token exchanges through the Desktop and headless entrypoints."""

from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ai_tender_intelligence import container_runtime
from ai_tender_intelligence.beta67 import __main__ as beta
from ai_tender_intelligence.demo41 import oauth42


CREDENTIALS = {
    'GOOGLE_OAUTH_CLIENT_ID': 'synthetic.apps.googleusercontent.com',
    'GOOGLE_OAUTH_CLIENT_SECRET': 'synthetic-client-secret',
    'GOOGLE_OAUTH_REFRESH_TOKEN': 'synthetic-refresh-token',
}


def token_response(token='synthetic-access-token', **fields):
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600, **fields}


def no_browser(*args, **kwargs):
    pytest.fail('Headless authorization opened a browser')


def test_refresh_reuses_access_token_and_renews_before_expiry(monkeypatch):
    requests = []
    now = 100
    monkeypatch.setattr(oauth42.time, 'monotonic', lambda: now)
    monkeypatch.setattr(oauth42.webbrowser, 'open', no_browser)

    def google(request):
        assert request.method == 'POST' and str(request.url) == oauth42.TOKEN
        requests.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=token_response(f'access-{len(requests)}'))

    grant = oauth42.RefreshGrant.from_environment(CREDENTIALS, transport=httpx.MockTransport(google))
    assert requests == []
    assert grant.access_token() == grant.access_token() == 'access-1'
    now += 3570
    assert grant.access_token() == 'access-2'
    assert requests == [{
        'grant_type': ['refresh_token'],
        'client_id': [CREDENTIALS['GOOGLE_OAUTH_CLIENT_ID']],
        'client_secret': [CREDENTIALS['GOOGLE_OAUTH_CLIENT_SECRET']],
        'refresh_token': [CREDENTIALS['GOOGLE_OAUTH_REFRESH_TOKEN']],
    }] * 2
    grant.close()
    with pytest.raises(oauth42.OAuthFailure, match='GRANT_CLOSED'):
        grant.access_token()


@pytest.mark.parametrize('name', CREDENTIALS)
def test_container_rejects_partial_headless_configuration_even_with_desktop_file(name, monkeypatch):
    monkeypatch.setattr(oauth42.webbrowser, 'open', no_browser)
    env = dict(CREDENTIALS, DATABASE_URL='synthetic', TELEGRAM_BOT_TOKEN='synthetic',
               OPENAI_API_KEY='synthetic', GMAIL_SENDER='sender@example.invalid',
               GMAIL_OAUTH_MODE='headless', GOOGLE_CLIENT_JSON_PATH='/unused.json')
    args = SimpleNamespace(health_only=False, enable_paid_beta=True)
    bot, _ = container_runtime.commands(args, env)
    assert bot[-1] == '--enable-paid-beta'
    assert all(value not in ' '.join(bot) for value in CREDENTIALS.values())
    del env[name]
    with pytest.raises(ValueError, match='HEADLESS_CREDENTIALS_MISSING'):
        container_runtime.commands(args, env)


def test_invalid_credentials_are_sanitized():
    env = dict(CREDENTIALS, GOOGLE_OAUTH_CLIENT_ID='invalid private value')
    with pytest.raises(oauth42.OAuthFailure, match='HEADLESS_CREDENTIALS_INVALID') as failure:
        oauth42.RefreshGrant.from_environment(env)
    assert env['GOOGLE_OAUTH_CLIENT_ID'] not in str(failure.value)


@pytest.mark.parametrize(('status', 'response', 'code'), [
    (400, {'error': 'invalid_grant'}, 'REFRESH_TOKEN_EXPIRED_REVOKED_OR_INVALID'),
    (401, {'error': 'invalid_client'}, 'HEADLESS_CLIENT_REJECTED'),
    (200, token_response(scope='unexpected-scope'), 'TOKEN_RESPONSE_INVALID'),
    (200, token_response(token='bad\r\nheader'), 'TOKEN_RESPONSE_INVALID'),
])
def test_rejected_refresh_is_sanitized_and_never_retried(status, response, code, caplog):
    calls = []

    def google(request):
        calls.append(request)
        return httpx.Response(status, json=dict(response, error_description='private-echo'))

    grant = oauth42.RefreshGrant.from_environment(CREDENTIALS, transport=httpx.MockTransport(google))
    with pytest.raises(oauth42.OAuthFailure, match=code) as failure:
        grant.access_token()
    assert len(calls) == 1
    output = str(failure.value) + repr(grant) + caplog.text
    assert all(secret not in output for secret in (*CREDENTIALS.values(), 'private-echo'))


def test_token_transport_error_does_not_expose_exception_text():
    def google(request):
        raise httpx.ReadTimeout('private transport data')

    grant = oauth42.RefreshGrant.from_environment(CREDENTIALS, transport=httpx.MockTransport(google))
    with pytest.raises(oauth42.OAuthFailure, match='TOKEN_EXCHANGE_UNCONFIRMED') as failure:
        grant.access_token()
    assert failure.value.__suppress_context__


@pytest.mark.parametrize('offline', [False, True])
def test_desktop_scope_pkce_and_offline_token_contract(offline):
    client = {'client_id': CREDENTIALS['GOOGLE_OAUTH_CLIENT_ID'], 'client_secret': 'synthetic'}
    redirect = 'http://127.0.0.1:32100/callback'
    params = parse_qs(urlsplit(oauth42.authorize_url(client, redirect, 'state', 'verifier', offline=offline)).query)
    assert params['scope'] == [oauth42.SCOPE]
    assert params['access_type'] == ['offline' if offline else 'online']
    assert params['code_challenge_method'] == ['S256'] and params['prompt'] == ['consent']
    assert params['include_granted_scopes'] == ['false']

    def google(request):
        assert parse_qs(request.content.decode())['code_verifier'] == ['verifier']
        return httpx.Response(200, json=token_response(scope=oauth42.SCOPE, refresh_token='issued-test-token'))

    grant = oauth42.exchange(client, redirect, 'verifier', 'code',
                             transport=httpx.MockTransport(google), offline=offline)
    assert grant.access_token() == 'synthetic-access-token'
    assert grant.refresh_token == ('issued-test-token' if offline else None)
    assert 'issued-test-token' not in repr(grant)


def test_offline_consent_requires_an_actual_refresh_token():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=token_response(scope=oauth42.SCOPE)))
    with pytest.raises(oauth42.OAuthFailure, match='REFRESH_TOKEN_NOT_ISSUED'):
        oauth42.exchange({'client_id': 'synthetic', 'client_secret': 'synthetic'}, 'redirect',
                         'verifier', 'code', offline=True, transport=transport)


def test_paid_beta_headless_entrypoint_supplies_existing_sender_transport(monkeypatch):
    for name, value in dict(CREDENTIALS, GMAIL_OAUTH_MODE='headless', OPENAI_API_KEY='test-key',
                            TELEGRAM_BOT_TOKEN='synthetic-token',
                            DATABASE_URL='postgresql+psycopg://user@localhost/test').items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(beta.mail, 'SENDER', 'sender@example.test')
    monkeypatch.setattr(oauth42, 'authorize', no_browser)
    requests = []

    def google(request):
        requests.append(request)
        return httpx.Response(200, json=token_response())

    grant = oauth42.RefreshGrant.from_environment(CREDENTIALS, transport=httpx.MockTransport(google))
    monkeypatch.setattr(oauth42.RefreshGrant, 'from_environment', lambda: grant)
    session = SimpleNamespace(scalar=lambda statement, *args: None if 'rolsuper' in str(statement) else 'present')
    db = SimpleNamespace(settings=SimpleNamespace(url='postgresql+psycopg://user@localhost/test'),
                         transaction=lambda: nullcontext(session), close=lambda: None)
    monkeypatch.setattr(beta, 'Database', lambda settings: db)
    monkeypatch.setattr(beta, 'Access', lambda database: SimpleNamespace(registered=lambda: {1}))
    monkeypatch.setattr(beta, 'preflight', lambda *args: None)
    for name in ('PublicHTTP', 'TelegramHTTP'):
        monkeypatch.setattr(beta, name, lambda *args: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(beta, 'Preview', lambda *args, **kwargs: SimpleNamespace(run=lambda: None))
    clients = []

    def coordinator(store, flow_factory, gmail_factory, **kwargs):
        assert kwargs['enabled'] and len(requests) == 1
        clients.append(gmail_factory('recipient@example.test'))
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(beta, 'Coordinator', coordinator)
    assert beta.main(['--connect', '--enable-paid-beta']) == 0
    assert len(requests) == len(clients) == 1
    assert clients[0].recipient == 'recipient@example.test'
    assert '/users/sender%40example.test/messages/send' in clients[0].endpoint
    assert clients[0].token == 'synthetic-access-token'
