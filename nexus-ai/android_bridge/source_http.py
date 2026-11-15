"""Credential-free public HTTPS reader. No shell, proxies, cookies or local URLs.

DNS is checked AND the connection is pinned to a checked address. This is a
network boundary for the declarative interpreter, not a Python sandbox.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BYTES = 262144
DNS_SLOTS = threading.BoundedSemaphore(2)
SECRET = re.compile(r'AIza[A-Za-z0-9_-]{12,}|github_pat_|gh[pousr]_[A-Za-z0-9_]{12,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|\bBearer\s+\S+', re.I)
CREDENTIAL = re.compile(r'^(?:api.?key|access.?token|token|password|passwd|authorization|auth|secret|signature)$', re.I)


class SourceError(ValueError):
    """Safe, non-payload-bearing error code."""


def clean_url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 2000:
        raise SourceError('url_length')
    if any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url or SECRET.search(url):
        raise SourceError('unsafe_url')
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None:
            raise SourceError('https_only')
        if p.port not in (None, 443) or p.fragment:
            raise SourceError('url_port_or_fragment')
        host = p.hostname.encode('idna').decode('ascii').lower()
        if (len(host) > 253 or '.' not in host or host.endswith('.')
                or not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?', host)
                or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.'))
                or host.endswith(('.local', '.localhost', '.internal', '.home', '.lan'))):
            raise SourceError('invalid_host')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise SourceError('literal_ip')
        pairs = parse_qsl(p.query, keep_blank_values=True, max_num_fields=32)
        if any(CREDENTIAL.fullmatch(k) or SECRET.search(v) for k, v in pairs):
            raise SourceError('credentials_forbidden')
        return urlunsplit(('https', host, p.path or '/', urlencode(pairs), ''))
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError('invalid_url') from None


def endpoint(url):
    p = urlsplit(clean_url(url))
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


def public_addresses(host, timeout=4):
    # A stuck resolver cannot create an unbounded number of background threads.
    if not DNS_SLOTS.acquire(blocking=False):
        raise SourceError('dns_busy')
    out = queue.Queue(maxsize=1)
    def resolve():
        try:
            out.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            out.put(None)
        finally:
            DNS_SLOTS.release()
    threading.Thread(target=resolve, daemon=True, name='nexus-public-dns').start()
    try:
        addresses = out.get(timeout=timeout)
    except queue.Empty:
        raise SourceError('dns_timeout') from None
    if not addresses:
        raise SourceError('dns_failed')
    checked = []
    for family, kind, proto, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        mapped = getattr(ip, 'ipv4_mapped', None)
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or (mapped is not None and not mapped.is_global)):
            raise SourceError('non_public_address')
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise SourceError('address_family')
        checked.append((family, kind, proto, address))
    return checked[:4]


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        deadline = time.monotonic() + self.timeout
        addresses = public_addresses(self.host, min(4, self.timeout))
        for family, kind, proto, address in addresses:
            sock = socket.socket(family, kind, proto)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError('connect_timeout')
                sock.settimeout(remaining)
                sock.connect(address)  # no second DNS resolution; no proxy
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except (OSError, SourceError):
                sock.close()
        raise SourceError('https_connection_failed')


@dataclass(frozen=True)
class Document:
    url: str
    media_type: str
    body: str
    retrieved_at: str
    digest: str


def fetch(url, *, documentation=False, cancelled=lambda: False):
    original = clean_url(url)
    current = original
    deadline = time.monotonic() + 18
    for hop in range(4):
        if cancelled():
            raise SourceError('cancelled')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError('request_timeout')
        p = urlsplit(current)
        conn = PinnedHTTPS(p.hostname, timeout=min(8, remaining), context=ssl.create_default_context())
        try:
            conn.request('GET', urlunsplit(('', '', p.path, p.query, '')),
                         headers={'User-Agent': 'NEXUS-PublicReader/1.0',
                                  'Accept': 'application/json, application/rss+xml, text/*, application/xml',
                                  'Accept-Encoding': 'identity'})
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308):
                target = clean_url(urljoin(current, r.getheader('Location', '')))
                if not documentation and urlsplit(target).netloc != p.netloc:
                    raise SourceError('cross_origin_redirect')
                current = target
                continue
            if r.status != 200:
                raise SourceError('http_' + str(r.status))
            if r.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise SourceError('compressed_response')
            length = r.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                raise SourceError('response_too_large')
            media = r.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            if not (media.startswith('text/') or media in ('application/json', 'application/xml', 'application/rss+xml', 'application/atom+xml') or media.endswith('+json')):
                raise SourceError('unsupported_content_type')
            data = bytearray()
            while True:
                if cancelled():
                    raise SourceError('cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError('request_timeout')
                # r.fp may own the socket when the server sent Connection: close.
                if conn.sock is not None:
                    conn.sock.settimeout(min(4, remaining))
                part = r.read1(min(8192, MAX_BYTES + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
                if len(data) > MAX_BYTES:
                    raise SourceError('response_too_large')
            return Document(current, media, bytes(data).decode('utf-8-sig', 'replace'),
                            datetime.now(timezone.utc).isoformat(), hashlib.sha256(data).hexdigest())
        except (OSError, http.client.HTTPException):
            raise SourceError('network_error') from None
        finally:
            conn.close()
    raise SourceError('redirect_limit')
