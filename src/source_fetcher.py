"""참고 자료(공식 페이지 등)를 내려받아 본문 글자만 뽑는다.

검색 서비스(네이버 API HUB, Tavily 등)나 시트에 적은 주소가 여기로 들어온다.
서버 내부 주소(127.0.0.1, 클라우드 메타데이터 169.254.x 등)로는 접속하지 않는다.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import certifi
import charset_normalizer
import urllib3

USER_AGENT = "Mozilla/5.0 (compatible; jalcheck-bot/0.1; +https://jalcheck.com)"
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
DEFAULT_MAX_CHARS = 12_000
ALLOWED_PORTS = (80, 443)
DNS_TIMEOUT = 5
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 15
TOTAL_TIMEOUT = 40          # 주소 하나를 읽는 전체 시간(리다이렉트 포함)
_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dns")

_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "head",
              "nav", "footer", "form", "button", "select"}
# 본문 영역으로 보는 태그·id (있으면 메뉴 글자를 빼고 이 안만 쓴다)
# 본문 영역 후보(앞쪽일수록 우선). 있으면 메뉴 글자를 빼고 그 안만 쓴다.
_MAIN_PRIORITY = ("main", "article", "#contents", "#content", "#main-content",
                  "#maincontent", "#main_content", "#container")
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "table", "ul", "ol", "dd", "dt", "header"}


class SourceError(RuntimeError):
    """자료를 가져오지 못했을 때."""


@dataclass(frozen=True)
class Source:
    id: str          # 글에서 출처를 가리키는 짧은 이름 (S1, S2 ...)
    url: str         # 최종 주소(리다이렉트 반영)
    title: str
    text: str        # 본문 글자(잘라낸 것)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.regions: dict[str, list[str]] = {}   # 후보 이름 → 글자 (처음 만난 것만)
        self._open: list[list] = []               # [후보 이름, 태그, 같은 태그 깊이]
        self._skip_depth = 0
        self._in_title = False

    def _add(self, text: str) -> None:
        self.parts.append(text)
        for name, _tag, _depth in self._open:
            self.regions[name].append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        for region in self._open:
            if region[1] == tag:
                region[2] += 1
        element_id = (dict(attrs).get("id") or "").lower()
        for name in (tag, f"#{element_id}" if element_id else None):
            if name in _MAIN_PRIORITY and name not in self.regions:
                self.regions[name] = []
                self._open.append([name, tag, 1])
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._add("\n")
        for region in self._open:
            if region[1] == tag:
                region[2] -= 1
        self._open = [r for r in self._open if r[2] > 0]

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self._add(data)


def _clean_lines(parts: list[str]) -> str:
    lines = []
    for line in "".join(parts).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def html_to_text(html: str) -> tuple[str, str]:
    """HTML에서 (제목, 본문 글자)를 뽑는다. 본문 영역이 충분히 길면 그 안만 쓴다."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    region_texts = [_clean_lines(parser.regions[n]) for n in _MAIN_PRIORITY if n in parser.regions]
    if region_texts:
        # 본문 상자가 있으면 메뉴가 섞인 페이지 전체로 돌아가지 않는다.
        # 충분히 긴 상자 중 우선순위가 가장 높은 것, 없으면 가장 긴 상자(짧으면 뒤에서 거절됨).
        long_enough = [t for t in region_texts if len(t) >= 200]
        text = long_enough[0] if long_enough else max(region_texts, key=len)
    else:
        text = _clean_lines(parser.parts)
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    return title, text


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str      # IDNA 변환된 호스트 이름
    port: int
    path: str      # 경로 + 쿼리


def _parse(url: str) -> _Target:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceError(f"주소 형식이 잘못됐습니다: {url}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SourceError(f"http/https 주소만 가능합니다: {url}")
    if parsed.username or parsed.password:
        raise SourceError(f"계정 정보가 들어간 주소는 쓰지 않습니다: {url}")
    port = port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise SourceError(f"80·443 포트만 접속합니다: {url}")
    try:
        host = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SourceError(f"호스트 이름이 잘못됐습니다: {url}") from exc
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return _Target(parsed.scheme, host, port, path)


def _resolve_public(host: str, port: int, timeout: float) -> str:
    """호스트를 IP로 바꾸고, 모든 IP가 공인 주소일 때만 그중 하나를 돌려준다."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
        try:
            infos = future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise SourceError(f"주소 조회 시간 초과: {host}") from exc
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise SourceError(f"주소를 찾을 수 없습니다: {host}") from exc
        addresses = list(dict.fromkeys(ipaddress.ip_address(i[4][0]) for i in infos))
    if not addresses or any(not ip.is_global for ip in addresses):
        raise SourceError(f"내부 주소로는 접속하지 않습니다: {host}")
    addresses.sort(key=lambda ip: ip.version)  # IPv4 먼저
    return str(addresses[0])


class _Watch:
    """감시 타이머가 끊을 소켓 목록. 타이머가 이미 끝난 뒤 맺어진 연결은 그 자리에서 끊는다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: list = []
        self.expired = False

    def add(self, sock) -> None:
        with self._lock:
            if not self.expired:
                self._sockets.append(sock)
                return
        _shutdown(sock)
        raise SourceError("시간 초과: 연결이 제한 시간 뒤에 맺어졌습니다")

    def cut(self) -> None:
        with self._lock:
            self.expired = True
            sockets = list(self._sockets)
        for sock in sockets:
            _shutdown(sock)


def _shutdown(sock) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _recording(base: type, watch: _Watch) -> type:
    """연결이 맺어지는 순간 실제 소켓을 감시 목록에 올리는 연결 클래스."""

    class Recording(base):
        def connect(self):
            super().connect()
            watch.add(self.sock)

    return Recording


def _open(target: _Target, ip: str, timeout: urllib3.Timeout, watch: _Watch):
    """검사한 IP로만 접속한다. Host 헤더와 TLS 인증서 확인은 원래 호스트 이름으로 한다."""
    headers = {
        "Host": target.host,
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "gzip, deflate",
    }
    if target.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            ip, target.port, timeout=timeout, retries=False, maxsize=1,
            server_hostname=target.host, assert_hostname=target.host,
            cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
        )
    else:
        pool = urllib3.HTTPConnectionPool(ip, target.port, timeout=timeout, retries=False, maxsize=1)
    pool.ConnectionCls = _recording(pool.ConnectionCls, watch)
    try:
        resp = pool.urlopen("GET", target.path, headers=headers, redirect=False,
                            preload_content=False, retries=False, assert_same_host=False)
    except BaseException:
        pool.close()
        raise
    return resp, pool


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=[\"']?([A-Za-z0-9_\-]+)", content_type)
    encoding = match.group(1) if match else None
    if not encoding:
        meta = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', body[:4096])
        encoding = meta.group(1).decode("ascii") if meta else None
    if not encoding:
        best = charset_normalizer.from_bytes(body).best()
        encoding = best.encoding if best else "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _fetch(url: str, deadline: float) -> tuple[str, str, str]:
    """(최종 주소, Content-Type, 본문 글자). 리다이렉트마다 주소·IP를 다시 검사한다."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        target = _parse(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError(f"시간 초과: {url}")
        ip = _resolve_public(target.host, target.port, min(DNS_TIMEOUT, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError(f"시간 초과: {url}")
        # 알려진 한계: TCP 연결 뒤 TLS 협상은 urllib3가 같은 connect 제한을 다시 적용하므로
        # 최악의 경우 전체 제한을 CONNECT_TIMEOUT만큼 넘길 수 있다(무한 대기는 아님).
        timeout = urllib3.Timeout(connect=min(CONNECT_TIMEOUT, remaining),
                                  read=min(READ_TIMEOUT, remaining))
        # 감시 타이머: 전체 시간이 다 되면 기록해 둔 소켓을 끊어 막혀 있는 읽기(헤더·본문)를 깨운다.
        watch = _Watch()
        watchdog = threading.Timer(max(deadline - time.monotonic(), 0), watch.cut)
        watchdog.daemon = True
        watchdog.start()
        try:
            resp, pool = _open(target, ip, timeout, watch)
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            watchdog.cancel()
            if time.monotonic() >= deadline:
                raise SourceError(f"시간 초과: {current}") from exc
            raise
        except BaseException:
            watchdog.cancel()
            raise
        try:
            if time.monotonic() >= deadline:   # 헤더를 받다가 감시 타이머에 끊긴 경우
                raise SourceError(f"시간 초과: {current}")
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise SourceError(f"이동할 주소가 없는 리다이렉트: {current}")
                current = urljoin(current, location)   # 리다이렉트 본문은 읽지 않는다
                continue
            if resp.status != 200:
                raise SourceError(f"응답 코드 {resp.status}: {current}")
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text/plain" not in ctype:
                raise SourceError(f"웹 페이지가 아닙니다({ctype or '형식 모름'}): {current}")
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.stream(16384, decode_content=True):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise SourceError(f"페이지가 너무 큽니다: {current}")
                if time.monotonic() > deadline:
                    raise SourceError(f"시간 초과: {current}")
                chunks.append(chunk)
            if time.monotonic() >= deadline:   # 끊겨서 '끝난 것처럼' 보이는 잘린 본문
                raise SourceError(f"시간 초과: {current}")
            return current, ctype, _decode(b"".join(chunks), ctype)
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise SourceError(f"시간 초과: {current}") from exc
            raise
        finally:
            watchdog.cancel()
            resp.close()
            pool.close()
    raise SourceError(f"리다이렉트가 너무 많습니다: {url}")



def fetch_source(
    url: str,
    source_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    total_timeout: float = TOTAL_TIMEOUT,
) -> Source:
    """주소 하나를 읽어 Source로 돌려준다. 어떤 실패든 SourceError로 알린다."""
    deadline = time.monotonic() + total_timeout
    try:
        final_url, ctype, raw = _fetch(url, deadline)
    except SourceError:
        raise
    except (urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
        raise SourceError(f"접속 실패: {url} ({exc.__class__.__name__})") from exc
    if "html" in ctype:
        try:
            title, text = html_to_text(raw)
        except (AssertionError, ValueError, IndexError) as exc:   # 표준 파서가 깨진 HTML에서 내는 오류
            raise SourceError(f"HTML을 읽지 못했습니다: {final_url}") from exc
    else:
        title, text = "", re.sub(r"[ \t]+", " ", raw).strip()
    if len(text) < 200:
        raise SourceError(f"본문이 거의 없습니다(자바스크립트로 그리는 페이지일 수 있음): {final_url}")
    return Source(id=source_id, url=final_url, title=title or final_url, text=text[:max_chars])


def fetch_sources(
    urls: list[str], max_sources: int | None = None, **kwargs
) -> tuple[list[Source], list[str]]:
    """여러 주소를 읽는다. (성공한 자료, 실패 사유 목록)을 돌려준다.

    같은 주소·같은 최종 주소·본문이 같은 페이지는 한 번만 쓴다. max_sources개 모이면 멈춘다.
    """
    sources: list[Source] = []
    errors: list[str] = []
    seen: set[str] = set()
    texts: set[str] = set()
    for url in urls:
        if max_sources is not None and len(sources) >= max_sources:
            break
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            src = fetch_source(url, f"S{len(sources) + 1}", **kwargs)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        if src.url in {s.url for s in sources} or src.text in texts:
            continue
        texts.add(src.text)
        sources.append(src)
    return sources, errors
