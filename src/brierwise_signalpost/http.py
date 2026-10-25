"""Polite HTTP access: retries with backoff, spacing per host, size and time limits, safe URLs, request accounting."""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import __version__

USER_AGENT = "BrierwiseSignalpost/%s (company research agent; contact brierwise@proton.me)" % __version__
RETRY_STATUS = {429, 500, 502, 503, 504}
# Seconds between two requests to the same host. Official APIs tolerate more; company sites get one request a second.
SPACING = {"data.brreg.no": 0.05, "pam-stilling-feed.nav.no": 0.2}
DEFAULT_SPACING = 1.0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Response:
    url: str
    final_url: str
    status: int                     # 0 means no HTTP answer (network error, refused URL, timeout)
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    retrieved_at: str = ""
    error: str = ""
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("Content-Type") or self.headers.get("content-type") or ""
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def public_host(host: str | None) -> bool:
    """True when every address of the host is a global (public) address: no loopback, private or link-local targets."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        except ValueError:
            return False
    return bool(infos)


def safe_url(url: str) -> str:
    """Empty string when the URL may be fetched, otherwise the reason."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "scheme not allowed"
    if parts.username or parts.password:
        return "credentials in URL"
    if parts.port not in (None, 80, 443):
        return "port not allowed"
    if not public_host(parts.hostname):
        return "host is not public"
    return ""


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to public http(s) targets, at most five times."""
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = safe_url(newurl)
        if reason:
            raise urllib.error.HTTPError(newurl, 451, "redirect refused: " + reason, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, timeout: float = 20.0, retries: int = 2, max_bytes: int = 3_000_000):
        self.timeout = timeout
        self.retries = retries
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._next = {}
        self._opener = urllib.request.build_opener(_SafeRedirect)
        self.requests = Counter()       # per host, every attempt that went to the network
        self.failures = Counter()

    def _wait(self, host: str) -> None:
        gap = SPACING.get(host, DEFAULT_SPACING)
        with self._lock:
            start = max(time.monotonic(), self._next.get(host, 0.0))
            self._next[host] = start + gap
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def get(self, url: str, accept: str = "*/*", headers: dict | None = None, check_url: bool = True,
            timeout: float | None = None, retries: int | None = None) -> Response:
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        if check_url:
            reason = safe_url(url)
            if reason:
                return Response(url, url, 0, retrieved_at=now_iso(), error="refused: " + reason)
        host = urlsplit(url).hostname or ""
        head = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip, deflate"}
        head.update(headers or {})
        last = None
        for attempt in range(retries + 1):
            self._wait(host)
            t0 = time.monotonic()
            with self._lock:
                self.requests[host] += 1
            try:
                req = urllib.request.Request(url, headers=head)
                with self._opener.open(req, timeout=timeout) as r:
                    body = r.read(self.max_bytes + 1)[: self.max_bytes]
                    hdrs = dict(r.headers.items())
                    body = _decompress(body, hdrs.get("Content-Encoding", ""))
                    return Response(url, r.geturl(), r.status, hdrs, body, now_iso(), "",
                                    int((time.monotonic() - t0) * 1000))
            except urllib.error.HTTPError as e:
                try:
                    body = e.read(200_000)
                except Exception:
                    body = b""
                hdrs = dict(e.headers.items()) if e.headers else {}
                body = _decompress(body, hdrs.get("Content-Encoding", ""))
                last = Response(url, e.geturl() or url, e.code, hdrs, body, now_iso(), "HTTP %d" % e.code,
                                int((time.monotonic() - t0) * 1000))
                if e.code not in RETRY_STATUS:
                    break
                retry_after = hdrs.get("Retry-After", "")
                pause = float(retry_after) if retry_after.isdigit() else 1.5 * (attempt + 1)
                if attempt < retries:
                    time.sleep(min(pause, 10.0))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
                last = Response(url, url, 0, {}, b"", now_iso(), "network: %s" % str(e)[:160],
                                int((time.monotonic() - t0) * 1000))
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
        with self._lock:
            self.failures[host] += 1
        return last


def _decompress(body: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if encoding == "gzip":
            return gzip.decompress(body)
        if encoding == "deflate":
            return zlib.decompress(body)
    except (OSError, zlib.error, EOFError):
        return body
    return body
