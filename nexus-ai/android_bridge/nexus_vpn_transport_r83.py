"""NEXUS R8.3: HTTPS transport through Android's *system* VPN (no proxy URL).

Does not create a VPN: Android decides whether Termux is included in the VPN.
Uses OS routing and certificate validation.  A failed first request can be retried
using IPv4 only, useful for VPN providers with broken IPv6 routing.
"""
from __future__ import annotations

import http.client
import logging
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

LOG = logging.getLogger('nexus.vpn')
HOSTS = frozenset({'generativelanguage.googleapis.com', 'openrouter.ai'})


def _ipv4_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                            source_address=None):
    host, port = address
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    last_error = None
    for family, socktype, proto, _, addr in infos:
        s = None
        try:
            s = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                s.settimeout(timeout)
            if source_address:
                s.bind(source_address)
            s.connect(addr)
            return s
        except OSError as exc:
            last_error = exc
            if s is not None:
                s.close()
    raise last_error or OSError('No IPv4 address available')


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _ipv4_create_connection


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        # HTTPSConnection retains default SSL verification, SNI, and hostname checks.
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


def _vpn_opener(ipv4_only: bool):
    # An Android TUN/VPN is an OS route, not an HTTP(S) proxy.
    # Disable accidental proxy environment variables, but DO NOT bypass Android VPN.
    handler = _IPv4HTTPSHandler() if ipv4_only else urllib.request.HTTPSHandler()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (500, 502, 503, 504)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False
        if isinstance(reason, ssl.SSLError):
            return isinstance(reason, ssl.SSLEOFError)
        if isinstance(reason, (TimeoutError, ConnectionError, socket.gaierror, OSError)):
            return True
        return any(s in str(reason).lower() for s in ('timed out', 'remote end closed', 'connection reset', 'temporarily'))
    return isinstance(exc, (http.client.RemoteDisconnected, ConnectionError,
                            TimeoutError, socket.gaierror, ssl.SSLEOFError, OSError))


def open_vpn(req, timeout=18):
    """Return an HTTP response; caller must close it. Does not retry HTTP 4xx.

    Retries only on connection failures; the caller's request must be idempotent
    or safe to repeat. NEXUS POST calls generate text, not external actions.
    """
    host = (urlsplit(req.full_url).hostname or '').lower()
    if host not in HOSTS:
        # Leave all non-cloud URLs and local M5StickC/Termux traffic untouched.
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        budget = float(timeout)
    except (TypeError, ValueError):
        budget = 18.0
    budget = max(3.0, min(budget, 35.0))
    force4 = os.getenv('NEXUS_VPN_IPV4', '').lower() in ('1', 'true', 'yes')
    # Keep calls bounded. First attempt has time to complete a normal Gemini reply.
    first = min(10.0, budget)
    second = max(2.0, min(10.0, budget - first))
    # If original budget is small, don't increase it substantially in total.
    if budget < first + second:
        first = max(2.0, budget * .65)
        second = max(1.0, budget - first)

    start = time.monotonic()
    for attempt, (ipv4, wait) in enumerate(((force4, first), (True, second)), 1):
        try:
            response = _vpn_opener(ipv4).open(req, timeout=wait)
            LOG.info('NEXUS VPN: success attempt=%d ip=%s elapsed=%.2fs',
                     attempt, 'IPv4' if ipv4 else 'auto', time.monotonic()-start)
            return response
        except (urllib.error.HTTPError, urllib.error.URLError, http.client.RemoteDisconnected,
                ConnectionError, TimeoutError, OSError, ssl.SSLError) as exc:
            if not _retryable(exc):
                raise
            LOG.warning('NEXUS VPN: retryable=%s attempt=%d ip=%s elapsed=%.2fs',
                        type(exc).__name__, attempt, 'IPv4' if ipv4 else 'auto',
                        time.monotonic()-start)
            if attempt == 2:
                raise urllib.error.URLError(
                    'Gemini HTTPS недоступен через текущий VPN: '
                    + type(exc).__name__ + '. Проверь включение Termux в VPN '
                    'и попробуй другой сервер VPN.'
                ) from exc
            time.sleep(0.2)
    raise AssertionError('unreachable')
