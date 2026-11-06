"""Fixed official HTTP destinations, pinned public IP connection and redacted diagnostics."""
import contextvars,ipaddress,json,logging,re,socket,time
from urllib.parse import urlsplit,unquote,parse_qs
import httpcore,httpx
from .models import AcquisitionError,Bounds

METHODS={'GetPublishedTenderDetails','GetPublishedLots','GetPublishedTenderExportsByTenderId',
         'GetPublishedTenderExportDocument','GetPublicTenderAnnouncementsByTenderId',
         'RetrieveTenderAnnouncementDocuments','GetSignedUrlByDocumentId'}
_ACTIVE=contextvars.ContextVar('document_http',default=False)
class Redact(logging.Filter):
    def filter(self,r):
        if _ACTIVE.get():r.msg='Official document transport event';r.args=()
        return True
for _name in ('httpx','httpcore.connection','httpcore.http11','httpcore.http2','httpcore.proxy'):
    logging.getLogger(_name).addFilter(Redact())


def allowed(url):
    try:
        u=urlsplit(url)
        if (u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment
                or any(ord(c)<33 or ord(c)==127 for c in url) or '\\' in url):raise ValueError()
        path=unquote(u.path)
        if path!=u.path or '..' in path:raise ValueError()
        if u.netloc not in {u.hostname,u.hostname+':443'}:raise ValueError()
        if u.hostname=='service.eop.bg' and not u.query and path.removeprefix('/NX1Service.svc/') in METHODS and path.startswith('/NX1Service.svc/'):
            return 'SERVICE'
        if u.hostname=='ted.europa.eu' and not u.query and re.fullmatch(r'/en/notice/\d{1,7}-\d{4}/xml',path):return 'TED'
        if u.hostname=='storage.eop.bg' and re.fullmatch(r'/[a-z0-9][a-z0-9-]{0,62}/[a-fA-F0-9-]{36}',path):
            if len(url)>10000:raise ValueError()
            return 'FILE'
    except (ValueError,TypeError,AttributeError):pass
    raise AcquisitionError('FORBIDDEN_SOURCE_URL')

class PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        try:
            rows=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
            ips=list(dict.fromkeys(r[4][0] for r in rows))
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise AcquisitionError('PRIVATE_SOURCE_ADDRESS')
            # Connect to the checked IP; TLS/SNI remains the original official host.
            return super().connect_tcp(ips[0],port,timeout,local_address,socket_options)
        except OSError:raise AcquisitionError('SOURCE_DNS_OR_CONNECTION_FAILED') from None

class DocHTTP:
    def __init__(self,bounds=Bounds(),*,transport=None,sleep=time.sleep):
        self.bounds,self.sleep,self.calls,self.bytes=bounds,sleep,0,0
        self.start=time.monotonic()
        self.pool=None if transport else httpcore.ConnectionPool(network_backend=PublicBackend(),retries=0,max_connections=2)
        self.client=httpx.Client(transport=transport,trust_env=False,follow_redirects=False,timeout=20) if transport else None
    def close(self):
        if self.pool:self.pool.close()
        if self.client:self.client.close()
    def request(self,method,url,*,body=None):
        kind=allowed(url)
        if (method,kind) not in {('POST','SERVICE'),('GET','FILE'),('GET','TED')}:raise AcquisitionError('FORBIDDEN_SOURCE_OPERATION')
        limit=self.bounds.file_bytes if kind=='FILE' else min(self.bounds.file_bytes,8_000_000)
        token=_ACTIVE.set(True)
        try:
            for attempt in range(3):
                if self.calls>=self.bounds.requests or time.monotonic()-self.start>self.bounds.seconds:raise AcquisitionError('HTTP_COLLECTION_BOUND')
                self.calls+=1
                try:
                    headers={'Accept-Encoding':'identity','Content-Type':'application/json','User-Agent':'AI-Tender-Intelligence/evidence-dev-1'}
                    context=(self.client.stream(method,url,json=body,headers=headers) if self.client else
                             self.pool.stream(method,url,headers=headers,content=json.dumps(body).encode() if body is not None else b'',
                             extensions={'timeout':{'connect':5,'read':20,'write':10,'pool':5}}))
                    with context as r:
                        status=r.status_code if self.client else r.status
                        hs=dict(r.headers) if self.client else {k.decode().lower():v.decode('latin1') for k,v in r.headers}
                        if status in {429,500,502,503,504}:
                            if attempt==2:raise AcquisitionError('SOURCE_TEMPORARY_FAILURE')
                            try:delay=float(hs.get('retry-after',2**attempt))
                            except ValueError:raise AcquisitionError('UNSUPPORTED_RETRY_AFTER') from None
                            if not 0<=delay<=10:raise AcquisitionError('RETRY_BOUND')
                            self.sleep(delay);continue
                        if status in {401,403}:raise AcquisitionError('SOURCE_RESTRICTED')
                        if status==404:raise AcquisitionError('SOURCE_MISSING')
                        if status!=200:raise AcquisitionError('SOURCE_HTTP_FAILED')
                        if hs.get('content-encoding','identity') not in {'','identity'}:raise AcquisitionError('UNSUPPORTED_HTTP_ENCODING')
                        try:
                            length=int(hs['content-length']) if 'content-length' in hs else None
                            if length is not None and not 0<=length<=limit:raise AcquisitionError('DOWNLOAD_SIZE_BOUND')
                        except ValueError:raise AcquisitionError('INVALID_CONTENT_LENGTH') from None
                        data=bytearray()
                        for chunk in (r.iter_bytes() if self.client else r.iter_stream()):
                            data.extend(chunk);self.bytes+=len(chunk)
                            if len(data)>limit or self.bytes>self.bounds.total_bytes or time.monotonic()-self.start>self.bounds.seconds:raise AcquisitionError('DOWNLOAD_SIZE_OR_TIME_BOUND')
                        if length is not None and len(data)!=length:raise AcquisitionError('TRUNCATED_DOWNLOAD')
                        media=hs.get('content-type','').split(';',1)[0].strip()
                        return bytes(data),media if re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+',media) else 'unknown'
                except (httpx.TransportError,httpcore.NetworkError,httpcore.TimeoutException,httpcore.ProtocolError,OSError):
                    if attempt==2:raise AcquisitionError('SOURCE_TRANSPORT_FAILED') from None
                    self.sleep(2**attempt)
        finally:_ACTIVE.reset(token)
        raise AcquisitionError('SOURCE_INCOMPLETE')
