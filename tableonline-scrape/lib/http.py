"""Polite HTTP client and raw-response storage.

Principle 4: one concurrent request per host, 1-2s delay, a real User-Agent
carrying a contact address, exponential backoff on 429/503. This runs
overnight — speed is irrelevant and a block is expensive.

Principle 6: raw responses are never overwritten. store_raw() writes a
sibling file rather than clobbering an existing capture, so parsing stays a
separate, re-runnable step over an immutable corpus.
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CONTACT = os.environ.get("CRAWL_CONTACT_EMAIL", "alex@letsumai.com")
USER_AGENT = (
    "Mozilla/5.0 (compatible; LaughAppResearchBot/1.0; +mailto:%s) "
    "httpx" % CONTACT
)

DEFAULT_DELAY = (1.0, 2.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: dict
    elapsed: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class _HostState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_allowed: float = 0.0


class PoliteClient:
    """Serialises requests per host and paces them.

    Used single-threaded by every phase today; the per-host lock means turning
    a phase concurrent later still keeps one request in flight per host.
    """

    def __init__(self, delay: tuple[float, float] = DEFAULT_DELAY,
                 timeout: float = 30.0, max_retries: int = 4,
                 follow_redirects: bool = True, headers: dict | None = None,
                 force_ipv4: bool = False):
        self.delay = delay
        self.max_retries = max_retries
        self._hosts: dict[str, _HostState] = {}
        self._hosts_lock = threading.Lock()
        # A host with both A and AAAA records is reached over IPv6 by default,
        # so an API key restricted to the machine's IPv4 address is rejected
        # for an address nobody thought to whitelist. Binding to 0.0.0.0
        # forces IPv4, making the source address predictable.
        transport = httpx.HTTPTransport(local_address="0.0.0.0") \
            if force_ipv4 else None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
            headers={"User-Agent": USER_AGENT,
                     "Accept-Language": "en,fi;q=0.8,et;q=0.6",
                     **(headers or {})},
        )

    def _state(self, host: str) -> _HostState:
        with self._hosts_lock:
            return self._hosts.setdefault(host, _HostState())

    def request(self, method: str, url: str, **kw) -> Response:
        host = urlsplit(url).netloc.lower()
        st = self._state(host)
        with st.lock:
            backoff = 2.0
            last_err = None
            for attempt in range(self.max_retries + 1):
                wait = st.next_allowed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                started = time.monotonic()
                try:
                    r = self._client.request(method, url, **kw)
                    elapsed = time.monotonic() - started
                    st.next_allowed = time.monotonic() + random.uniform(*self.delay)
                    if r.status_code in RETRY_STATUSES and attempt < self.max_retries:
                        # Honour Retry-After when the server sends one.
                        ra = r.headers.get("retry-after")
                        sleep_for = backoff
                        if ra and ra.isdigit():
                            sleep_for = max(backoff, float(ra))
                        time.sleep(sleep_for)
                        backoff *= 2
                        continue
                    return Response(str(r.url), r.status_code, r.text,
                                    dict(r.headers), elapsed)
                except (httpx.TransportError, httpx.HTTPError) as exc:
                    last_err = f"{type(exc).__name__}: {exc}"
                    st.next_allowed = time.monotonic() + random.uniform(*self.delay)
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        backoff *= 2
                        continue
            return Response(url, 0, "", {}, 0.0, error=last_err or "exhausted retries")

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def store_raw(path: Path, content: str) -> Path:
    """Write content to path without ever overwriting a differing capture.

    Identical content is a no-op (re-runs stay clean). Different content lands
    beside the original as name.2.ext, name.3.ext, ... so a re-scrape can be
    diffed against what we parsed the first time.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return path
    if path.read_text(encoding="utf-8", errors="replace") == content:
        return path
    n = 2
    while True:
        alt = path.with_name(f"{path.stem}.{n}{path.suffix}")
        if not alt.exists():
            alt.write_text(content, encoding="utf-8")
            return alt
        if alt.read_text(encoding="utf-8", errors="replace") == content:
            return alt
        n += 1
