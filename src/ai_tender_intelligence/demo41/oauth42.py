"""Desktop consent and headless refresh for the Gmail send-only scope."""
import base64,json,secrets,time,webbrowser
import os
from dataclasses import dataclass
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlencode,urlparse,parse_qs
import httpx
from .mail import SENDER

SCOPE='https://www.googleapis.com/auth/gmail.send'
AUTH='https://accounts.google.com/o/oauth2/v2/auth'
TOKEN='https://oauth2.googleapis.com/token'
REFRESH_VARIABLES=('GOOGLE_OAUTH_CLIENT_ID','GOOGLE_OAUTH_CLIENT_SECRET','GOOGLE_OAUTH_REFRESH_TOKEN')


class OAuthFailure(Exception):pass


@dataclass(repr=False)
class Grant:
    token:str
    expires_at:float
    refresh_token:str|None=None

    def access_token(self):
        if self.token is None or time.monotonic()>=self.expires_at:
            raise OAuthFailure('OAUTH_DESKTOP_CONSENT_EXPIRED')
        return self.token

    def close(self):
        self.token=None
        self.refresh_token=None


def oauth_mode(environ):
    mode=environ.get('GMAIL_OAUTH_MODE') or 'desktop'
    if mode not in {'desktop','headless'}:
        raise OAuthFailure('OAUTH_MODE_MUST_BE_DESKTOP_OR_HEADLESS')
    return mode


def _credential(value):
    return isinstance(value,str) and 0<len(value)<=4096 and all(33<=ord(c)<=126 for c in value)


class RefreshGrant:
    """Cache one access token in the single Beta worker process."""

    def __init__(self,credentials,*,transport=None):
        self._credentials=credentials
        self._transport=transport
        self._grant=None

    @classmethod
    def from_environment(cls,environ=None,*,transport=None):
        source=os.environ if environ is None else environ
        client_id,secret,refresh=(source.get(name) for name in REFRESH_VARIABLES)
        if not all((client_id,secret,refresh)):
            raise OAuthFailure('OAUTH_HEADLESS_CREDENTIALS_MISSING')
        if not all(map(_credential,(client_id,secret,refresh))) or not client_id.endswith('.apps.googleusercontent.com'):
            raise OAuthFailure('OAUTH_HEADLESS_CREDENTIALS_INVALID')
        return cls({'client_id':client_id,'client_secret':secret,'refresh_token':refresh},transport=transport)

    def access_token(self):
        if not self._credentials:
            raise OAuthFailure('OAUTH_HEADLESS_GRANT_CLOSED')
        if self._grant and time.monotonic()<self._grant.expires_at:
            return self._grant.token
        if self._grant:
            self._grant.close()
            self._grant=None
        status,data=_token_request({'grant_type':'refresh_token',**self._credentials},self._transport)
        if status!=200:
            error=data.get('error')
            if error=='invalid_grant':
                raise OAuthFailure('OAUTH_REFRESH_TOKEN_EXPIRED_REVOKED_OR_INVALID')
            if error in ('invalid_client','unauthorized_client'):
                raise OAuthFailure('OAUTH_HEADLESS_CLIENT_REJECTED')
            raise OAuthFailure('OAUTH_REFRESH_REJECTED')
        # Google's refresh response may omit scope. It inherits the original grant.
        self._grant=_access_grant(data,scope_optional=True)
        return self._grant.token

    def close(self):
        if self._grant:
            self._grant.close()
        self._credentials.clear()


def load_client(path,repository):
    path=Path(path).resolve();repository=Path(repository).resolve()
    if path==repository or repository in path.parents or path.suffix.lower()!='.json' or path.stat().st_size>65536:
        raise OAuthFailure('OAUTH_EXTERNAL_DESKTOP_FILE_REQUIRED')
    try:data=json.loads(path.read_text(encoding='utf-8'))['installed']
    except Exception:raise OAuthFailure('OAUTH_DESKTOP_CLIENT_INVALID') from None
    if (not isinstance(data.get('client_id'),str) or not data['client_id'].endswith('.apps.googleusercontent.com')
        or not isinstance(data.get('client_secret'),str) or not data['client_secret']
        or data.get('token_uri',TOKEN)!=TOKEN):raise OAuthFailure('OAUTH_DESKTOP_CLIENT_INVALID')
    return data


def authorize_url(client,redirect,state,verifier,*,offline=False):
    challenge=base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip('=')
    return AUTH+'?'+urlencode({'client_id':client['client_id'],'redirect_uri':redirect,'response_type':'code',
        'scope':SCOPE,'state':state,'code_challenge':challenge,'code_challenge_method':'S256',
        'access_type':'offline' if offline else 'online','prompt':'consent',
        'include_granted_scopes':'false','login_hint':SENDER})


def callback(path,state):
    parsed=urlparse(path);data=parse_qs(parsed.query)
    if parsed.path!='/callback' or len(data.get('state',[]))!=1 or not secrets.compare_digest(data['state'][0],state):
        raise OAuthFailure('OAUTH_CALLBACK_STATE_INVALID')
    if 'error' in data:raise OAuthFailure('OAUTH_OWNER_DID_NOT_AUTHORIZE')
    if len(data.get('code',[]))!=1 or not data['code'][0]:raise OAuthFailure('OAUTH_CALLBACK_CODE_INVALID')
    return data['code'][0]


def _token_request(form,transport):
    if transport is not None and not isinstance(transport,httpx.MockTransport):raise OAuthFailure('OAUTH_MOCK_ONLY_INJECTION')
    try:
        with httpx.Client(transport=transport,timeout=20,follow_redirects=False,trust_env=False) as session:
            with session.stream('POST',TOKEN,data=form) as response:
                status=response.status_code
                raw=b''
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>65536:raise OAuthFailure('OAUTH_TOKEN_RESPONSE_INVALID')
        try:data=json.loads(raw)
        except (ValueError,UnicodeError):data=None
        if not isinstance(data,dict):
            if status==200:raise OAuthFailure('OAUTH_TOKEN_RESPONSE_INVALID')
            data={}
        return status,data
    except OAuthFailure:raise
    except Exception:raise OAuthFailure('OAUTH_TOKEN_EXCHANGE_UNCONFIRMED') from None


def _access_grant(data,*,scope_optional=False):
    scope=data.get('scope')
    scope_valid=(isinstance(scope,str) and set(scope.split())=={SCOPE}) or (scope_optional and 'scope' not in data)
    token_type=data.get('token_type')
    lifetime=data.get('expires_in')
    if (not _credential(data.get('access_token')) or not isinstance(token_type,str) or token_type.lower()!='bearer'
        or type(lifetime) is not int or not 60<lifetime<=86400 or not scope_valid):
        raise OAuthFailure('OAUTH_TOKEN_RESPONSE_INVALID')
    return Grant(data['access_token'],time.monotonic()+lifetime-30)


def exchange(client,redirect,verifier,code,*,transport=None,offline=False):
    status,data=_token_request({'grant_type':'authorization_code','code':code,
        'redirect_uri':redirect,'client_id':client['client_id'],'client_secret':client['client_secret'],
        'code_verifier':verifier},transport)
    if status!=200:raise OAuthFailure('OAUTH_TOKEN_EXCHANGE_REJECTED')
    grant=_access_grant(data)
    if offline:
        if not _credential(data.get('refresh_token')):
            grant.close()
            raise OAuthFailure('OAUTH_REFRESH_TOKEN_NOT_ISSUED')
        grant.refresh_token=data['refresh_token']
    return grant


def authorize(path,repository,*,offline=False):
    client=load_client(path,repository);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    result={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # Callback codes/URLs never enter logs.
        def do_GET(self):
            try:result['code']=callback(self.path,state);status=200;body=b'Authorization received. Return to your local terminal.'
            except OAuthFailure as error:
                status=400;body=b'Authorization was not accepted.'
                if str(error)=='OAUTH_OWNER_DID_NOT_AUTHORIZE':result['error']=str(error)
            self.send_response(status);self.send_header('Content-Type','text/plain');self.end_headers();self.wfile.write(body)
    with HTTPServer(('127.0.0.1',0),Handler) as server:
        server.timeout=1;redirect='http://127.0.0.1:'+str(server.server_port)+'/callback'
        if not webbrowser.open(authorize_url(client,redirect,state,verifier,offline=offline)):raise OAuthFailure('OAUTH_BROWSER_NOT_OPENED')
        deadline=time.monotonic()+600
        while not result and time.monotonic()<deadline:server.handle_request()
        if 'code' not in result:raise OAuthFailure(result.get('error','OAUTH_CONSENT_TIMEOUT'))
    return exchange(client,redirect,verifier,result.pop('code'),offline=offline)
