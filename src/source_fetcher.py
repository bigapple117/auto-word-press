"""참고 자료(공식 페이지 등)를 내려받아 본문 글자만 뽑는다.

검색 서비스(네이버 API HUB, Tavily 등)나 시트에 적은 주소가 여기로 들어온다.
서버 내부 주소(127.0.0.1, 클라우드 메타데이터 169.254.x 등)로는 접속하지 않는다.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
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
# 한국 통로: 해외 접속을 막은 한국 기관 사이트를 24시간 켜 둔 집 노트북(한국 가정용 인터넷)으로 읽는다.
# 노트북 → 오라클1 127.0.0.1:18081(역방향 SOCKS) ← 오라클2 127.0.0.1:18081(ssh -L, korea-relay-forward 서비스).
# 주소·IP 검사는 지금처럼 여기서 하고, 검사한 IP로만 접속을 부탁한다(socks5, 이름 풀이를 노트북에 맡기지 않음).
KOREA_PROXY = "socks5://127.0.0.1:18081"
DEAD_HOST_SECONDS = 6 * 60 * 60   # 직접 접속이 끊긴 호스트는 이 시간 동안 바로 한국 통로로(매번 수십 초 기다리지 않게)
_dead_hosts: dict[str, float] = {}
log = logging.getLogger(__name__)
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


class BlockedUrlError(SourceError):
    """보안 정책으로 거절한 주소(내부 주소, 허용 안 된 포트·형식). 대신 읽기도 하지 않는다."""


class ConnectFailedError(SourceError):
    """서버에 닿지 못함(접속 거절·시간 초과). 해외 접속 차단일 수 있어 한국 통로로 다시 읽을 만하다."""


class RelayUnavailableError(SourceError):
    """대신 읽기 서비스 자체를 지금 쓸 수 없음(한도 초과·인증 오류 등). 남은 주소도 대신 읽지 않는다."""


@dataclass(frozen=True)
class Source:
    id: str          # 글에서 출처를 가리키는 짧은 이름 (S1, S2 ...)
    url: str         # 최종 주소(리다이렉트 반영)
    title: str
    text: str        # 본문 글자(잘라낸 것)
    via: str = "direct"   # direct: 서버가 직접 읽음, gemini: 구글 서버가 대신 읽고 AI가 옮겨 적음


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
        raise BlockedUrlError(f"주소 형식이 잘못됐습니다: {url}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BlockedUrlError(f"http/https 주소만 가능합니다: {url}")
    if parsed.username or parsed.password:
        raise BlockedUrlError(f"계정 정보가 들어간 주소는 쓰지 않습니다: {url}")
    port = port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise BlockedUrlError(f"80·443 포트만 접속합니다: {url}")
    try:
        host = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BlockedUrlError(f"호스트 이름이 잘못됐습니다: {url}") from exc
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
        raise BlockedUrlError(f"내부 주소로는 접속하지 않습니다: {host}")
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


def _open(target: _Target, ip: str, timeout: urllib3.Timeout, watch: _Watch, cookie: str = "",
          proxy: str | None = None):
    """검사한 IP로만 접속한다. Host 헤더와 TLS 인증서 확인은 원래 호스트 이름으로 한다."""
    headers = {
        "Host": target.host,
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "gzip, deflate",
    }
    if cookie:
        headers["Cookie"] = cookie
    if proxy:
        from urllib3.contrib.socks import SOCKSProxyManager   # PySocks 필요(한국 통로를 쓸 때만)
        extra = ({"server_hostname": target.host, "assert_hostname": target.host,
                  "cert_reqs": "CERT_REQUIRED", "ca_certs": certifi.where()} if target.scheme == "https" else {})
        pool = SOCKSProxyManager(proxy, timeout=timeout, retries=False, maxsize=1).connection_from_host(
            ip, target.port, scheme=target.scheme, pool_kwargs=extra)
    elif target.scheme == "https":
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


_BAD_COOKIE = re.compile(r"[\x00-\x20\x7f;,]")   # 제어문자·공백·구분자가 든 쿠키는 버린다


def _fetch(url: str, deadline: float, proxy: str | None = None) -> tuple[str, str, str]:
    """(최종 주소, Content-Type, 본문 글자). 리다이렉트마다 주소·IP를 다시 검사한다."""
    current = url
    # 일부 기관(국토교통부 등)은 첫 방문에 쿠키를 주고 같은 주소로 다시 보낸다. 쿠키를 돌려주지 않으면
    # 같은 주소로 계속 돌아 '리다이렉트가 너무 많다'가 된다. 이 한 번의 읽기 안에서, 준 호스트에만 돌려준다.
    jar: dict[tuple[str, str], dict[str, str]] = {}      # (scheme, host)별: https 쿠키를 http로 보내지 않게
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
            cookie = "; ".join(f"{k}={v}" for k, v in jar.get((target.scheme, target.host), {}).items())
            resp, pool = _open(target, ip, timeout, watch, cookie, **({"proxy": proxy} if proxy else {}))
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
                for raw in resp.headers.getlist("Set-Cookie"):
                    name, sep, value = raw.split(";", 1)[0].strip().partition("=")
                    if sep and name and not _BAD_COOKIE.search(name + value):
                        jar.setdefault((target.scheme, target.host), {})[name] = value
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
    proxy: str | None = None,
) -> Source:
    """주소 하나를 읽어 Source로 돌려준다. 어떤 실패든 SourceError로 알린다(서버에 못 닿았으면 ConnectFailedError).
    proxy: 한국 통로(KOREA_PROXY) 등 SOCKS 주소. 통로 자체가 안 되면 RelayUnavailableError."""
    deadline = time.monotonic() + total_timeout
    try:
        final_url, ctype, raw = _fetch(url, deadline, proxy)
    except BlockedUrlError:
        raise
    except SourceError as exc:
        if str(exc).startswith("시간 초과"):
            if proxy and not relay_ready(proxy):
                raise RelayUnavailableError(f"한국 통로 연결 안 됨({exc})") from exc
            raise ConnectFailedError(str(exc)) from exc
        raise
    except (urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
        if proxy and not relay_ready(proxy):
            raise RelayUnavailableError(f"한국 통로 연결 안 됨({exc.__class__.__name__})") from exc
        if isinstance(exc, (urllib3.exceptions.ConnectTimeoutError, urllib3.exceptions.NewConnectionError,
                            urllib3.exceptions.ReadTimeoutError, urllib3.exceptions.ProtocolError,
                            ConnectionError, TimeoutError)):
            raise ConnectFailedError(f"접속 실패: {url} ({exc.__class__.__name__})") from exc
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


def relay_ready(proxy: str = KOREA_PROXY, timeout: float = 5) -> bool:
    """한국 통로가 노트북까지 이어져 있는지. SOCKS5 인사(인증 없음)를 주고받아 본다.
    오라클2 입구만 열리고 노트북이 꺼져 있으면 오라클1에서 연결이 끊겨 답이 오지 않는다."""
    if not proxy:
        return False
    host, port = proxy.rsplit("//", 1)[-1].rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            got = b""
            while len(got) < 2:
                chunk = sock.recv(2 - len(got))
                if not chunk:
                    return False
                got += chunk
            return got == b"\x05\x00"
    except OSError:
        return False


def _host(url: str) -> str:
    return (urlparse(url.strip()).hostname or "").lower()


def fetch_sources(
    urls: list[str],
    max_sources: int | None = None,
    fallback: Callable[[str, str], Source] | None = None,
    max_fallback_attempts: int = 3,
    korea_proxy: str | None = "auto",
    **kwargs,
) -> tuple[list[Source], list[str]]:
    """여러 주소를 읽는다. (성공한 자료, 실패 사유 목록)을 돌려준다.

    같은 주소·같은 최종 주소·본문이 같은 페이지는 한 번만 쓴다. max_sources개 모이면 멈춘다.
    fallback(url, source_id): 직접 읽기에 실패했을 때(해외 차단, 자바스크립트 페이지 등) 대신 읽는 함수.
    보안 정책으로 거절한 주소(BlockedUrlError)는 대신 읽지 않는다. 대신 읽기는 최대
    max_fallback_attempts번, RelayUnavailableError가 나면 그 뒤로는 시도하지 않는다.
    """
    relay_tries = 0
    relay_down = False
    if korea_proxy == "auto":
        korea_proxy = KOREA_PROXY
    korea = korea_proxy if korea_proxy and relay_ready(korea_proxy) else None
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
        source_id = f"S{len(sources) + 1}"
        try:
            host = _host(url)
            if korea and host and _dead_hosts.get(host, 0) > time.monotonic():
                raise ConnectFailedError(f"접속 실패: {url} (최근 접속 끊김, 바로 한국 통로로)")
            src = fetch_source(url, source_id, **kwargs)
        except BlockedUrlError as exc:
            errors.append(str(exc))
            continue
        except ValueError as exc:   # 깨진 주소(예: http://[bad)
            errors.append(f"주소 형식 오류: {url} ({exc})")
            continue
        except SourceError as exc:
            problem = str(exc)
            src = None
            if isinstance(exc, ConnectFailedError) and korea:
                # 서버에 못 닿음 = 해외 차단일 수 있음 → 한국 통로(집 노트북)로 다시 읽는다
                skipped = "바로 한국 통로로" in problem
                try:
                    src = fetch_source(url, source_id, proxy=korea, **kwargs)
                    log.info("한국 통로로 읽음: %s", url)
                    if host and not skipped:   # 직접은 안 되고 통로로는 된 호스트만, 적중 때는 기한을 늘리지 않음
                        _dead_hosts[host] = time.monotonic() + DEAD_HOST_SECONDS
                except BlockedUrlError as exc2:   # 통로로 읽다가 내부 주소로 리다이렉트 등 → 대신 읽기도 하지 않음
                    errors.append(str(exc2))
                    continue
                except RelayUnavailableError as exc2:
                    log.warning("한국 통로 안 됨(이번 자료 모으기에서는 더 안 씀): %s", exc2)
                    korea = None
                    problem += f" / 한국 통로 안 됨: {exc2}"
                except SourceError as exc2:
                    problem += f" / 한국 통로로도 실패: {exc2}"
            if src is None:
                if fallback is None or relay_down or relay_tries >= max_fallback_attempts:
                    errors.append(problem)
                    continue
                relay_tries += 1
                try:
                    src = fallback(url, source_id)
                except RelayUnavailableError as exc2:
                    relay_down = True
                    errors.append(f"{problem} / 대신 읽기 중단: {exc2}")
                    continue
                except SourceError as exc2:
                    errors.append(f"{problem} / 대신 읽기도 실패: {exc2}")
                    continue
        if src.url in {s.url for s in sources} or src.text in texts:
            continue
        texts.add(src.text)
        sources.append(src)
    return sources, errors
