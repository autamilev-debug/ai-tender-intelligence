"""Foreground Desktop OAuth: PKCE/state/loopback, gmail.send only, memory-only grant."""
import base64,json,secrets,time,webbrowser
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


class OAuthFailure(Exception):pass


@dataclass(repr=False)
class Grant:
    token:str
    expires_at:float
    def close(self):self.token=None


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


def authorize_url(client,redirect,state,verifier):
    challenge=base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip('=')
    return AUTH+'?'+urlencode({'client_id':client['client_id'],'redirect_uri':redirect,'response_type':'code',
        'scope':SCOPE,'state':state,'code_challenge':challenge,'code_challenge_method':'S256',
        'access_type':'online','prompt':'consent','include_granted_scopes':'false','login_hint':SENDER})


def callback(path,state):
    parsed=urlparse(path);data=parse_qs(parsed.query)
    if parsed.path!='/callback' or len(data.get('state',[]))!=1 or not secrets.compare_digest(data['state'][0],state):
        raise OAuthFailure('OAUTH_CALLBACK_STATE_INVALID')
    if 'error' in data:raise OAuthFailure('OAUTH_OWNER_DID_NOT_AUTHORIZE')
    if len(data.get('code',[]))!=1 or not data['code'][0]:raise OAuthFailure('OAUTH_CALLBACK_CODE_INVALID')
    return data['code'][0]


def exchange(client,redirect,verifier,code,*,transport=None):
    if transport is not None and not isinstance(transport,httpx.MockTransport):raise OAuthFailure('OAUTH_MOCK_ONLY_INJECTION')
    try:
        with httpx.Client(transport=transport,timeout=20,follow_redirects=False,trust_env=False) as session:
            with session.stream('POST',TOKEN,data={'grant_type':'authorization_code','code':code,
                'redirect_uri':redirect,'client_id':client['client_id'],'client_secret':client['client_secret'],
                'code_verifier':verifier}) as response:
                if response.status_code!=200:raise OAuthFailure('OAUTH_TOKEN_EXCHANGE_REJECTED')
                raw=b''
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>65536:raise OAuthFailure('OAUTH_TOKEN_RESPONSE_INVALID')
                data=json.loads(raw)
        if (not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not data['access_token']
            or data.get('token_type','').lower()!='bearer' or type(data.get('expires_in')) is not int
            or not 60<data['expires_in']<=86400 or set(data.get('scope','').split())!={SCOPE}):
            raise OAuthFailure('OAUTH_TOKEN_RESPONSE_INVALID')
        return Grant(data['access_token'],time.monotonic()+data['expires_in']-30)
    except OAuthFailure:raise
    except Exception:raise OAuthFailure('OAUTH_TOKEN_EXCHANGE_UNCONFIRMED') from None


def authorize(path,repository):
    client=load_client(path,repository);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    result={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # Callback codes/URLs never enter logs.
        def do_GET(self):
            try:result['code']=callback(self.path,state);status=200;body=b'Authorization received. Return to the PowerShell demo window.'
            except OAuthFailure as error:
                status=400;body=b'Authorization was not accepted.'
                if str(error)=='OAUTH_OWNER_DID_NOT_AUTHORIZE':result['error']=str(error)
            self.send_response(status);self.send_header('Content-Type','text/plain');self.end_headers();self.wfile.write(body)
    with HTTPServer(('127.0.0.1',0),Handler) as server:
        server.timeout=1;redirect='http://127.0.0.1:'+str(server.server_port)+'/callback'
        if not webbrowser.open(authorize_url(client,redirect,state,verifier)):raise OAuthFailure('OAUTH_BROWSER_NOT_OPENED')
        deadline=time.monotonic()+600
        while not result and time.monotonic()<deadline:server.handle_request()
        if 'code' not in result:raise OAuthFailure(result.get('error','OAUTH_CONSENT_TIMEOUT'))
    return exchange(client,redirect,verifier,result.pop('code'))
