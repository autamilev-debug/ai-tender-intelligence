"""Credential-free, bounded, fixed-destination public HTTP; no redirects/proxies."""
import ipaddress
import socket
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from .models import DiscoveryError


def allowed_url(url: str):
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.fragment or p.query:
        raise DiscoveryError('Unapproved public source URL.')
    paths = {
        'api.ted.europa.eu': r'/v3/notices/search',
        'ted.europa.eu': r'/en/notice/\d{1,7}-\d{4}/xml',
        'storage.eop.bg': r'/open-data-\d{4}-\d{2}-\d{2}/[^/]+\.json',
    }
    import re
    if p.hostname not in paths or not re.fullmatch(paths[p.hostname], p.path):
        raise DiscoveryError('Unapproved public source URL.')
    return p.hostname


def public_dns(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise DiscoveryError('Public source address rejected.')
    except OSError:
        raise DiscoveryError('Public source DNS unavailable.') from None


class PublicHTTP:
    # Reviewable engineering bounds, not provider SLA/capacity claims.
    def __init__(self, *, transport=None, resolve=public_dns, sleep=time.sleep, max_bytes=8_000_000):
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=httpx.Timeout(20, connect=5, pool=5), headers={'User-Agent': 'AI-Tender-Intelligence/metadata-1'})
        self.resolve, self.sleep, self.max_bytes = resolve, sleep, max_bytes

    def close(self):
        self.client.close()

    def request(self, method, url, *, body=None):
        host = allowed_url(url)
        if (method, host) not in {('POST', 'api.ted.europa.eu'), ('GET', 'ted.europa.eu'), ('GET', 'storage.eop.bg')}:
            raise DiscoveryError('Unsupported public source operation.')
        deadline = time.monotonic() + 75
        for attempt in range(3):
            self.resolve(host)
            try:
                with self.client.stream(method, url, json=body) as response:
                    if response.status_code in {429, 500, 502, 503, 504}:
                        delay = 2 ** attempt
                        retry = response.headers.get('Retry-After')
                        if retry:
                            try:
                                delay = float(retry)
                            except ValueError:
                                try:
                                    delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                                except (ValueError, TypeError, OverflowError):
                                    raise DiscoveryError('Invalid source retry instruction.') from None
                        if attempt == 2 or not math.isfinite(delay) or delay > 10 or delay < 0 or time.monotonic() + delay >= deadline:
                            raise DiscoveryError('Public source temporarily unavailable; retry later.')
                        self.sleep(delay)
                        continue
                    if response.status_code != 200:
                        raise DiscoveryError('Public source request failed.')
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes or time.monotonic() >= deadline:
                            raise DiscoveryError('Public source response exceeds bounds.')
                    return bytes(data)
            except httpx.TransportError:
                if attempt == 2 or time.monotonic() + 4 >= deadline:
                    raise DiscoveryError('Public source transport unavailable.') from None
                self.sleep(2 ** attempt)
        raise DiscoveryError('Public source request incomplete.')
