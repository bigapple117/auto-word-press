"""AI가 제안한 '신청·조회 바로가기' 링크를 검증한다.

통과 조건(둘 다):
1. 공식 사이트(.go.kr, 정부24, 정책브리핑, 주요 공공기관)
2. 출처가 확인됨: 우리가 읽은 자료의 주소이거나, 자료 본문에 그 주소가 글자 그대로 적혀 있음
   (본문에 'www.hometax.go.kr'처럼 도메인만 있으면 그 사이트 첫 화면 주소만 인정)
AI가 짐작해서 만든 깊은 주소(본문에 없는 경로)는 막는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse, urlunparse

from src.content_schema import ActionLink
from src.source_fetcher import Source
from src.source_finder import is_official

MAX_LINKS = 4
# 본문을 이 문자들로 나눈 덩어리 하나하나를 주소 후보로 본다(덩어리 일부를 잘라 다른 주소를 만들지 않음)
_SPLIT = re.compile(r"[\s()\[\]{}<>\"'“”‘’，、。·…]+")
_ASCII_RUN = re.compile(r"^[\x21-\x7e]+")   # 보이는 ASCII 전체를 덩어리째(|·^·역슬래시 등에서 잘리지 않게)
_HOST = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
_URL = re.compile(rf"^(?:https?://)?({_HOST})(?::(\d{{1,5}}))?([/?#;][^\s]*)?$", re.I)


@dataclass(frozen=True)
class CheckedLink:
    label: str
    url: str
    purpose: str
    note: str | None


def _normalize(url: str) -> str | None:
    """'www.x.go.kr/a' → 'https://www.x.go.kr/a'. http는 https로, #뒤는 버림. 형식이 틀리면 None."""
    url = (url or "").strip()
    m = _URL.match(url)
    if not m or "@" in url.split("?", 1)[0] or "\\" in url:
        return None
    if m.group(2) and int(m.group(2)) not in (80, 443):
        return None
    try:
        p = urlparse(url if re.match(r"^https?://", url, re.I) else "https://" + url)
    except ValueError:
        return None
    if not p.hostname or p.username or p.password:
        return None
    return urlunparse(("https", p.hostname.lower(), p.path or "/", p.params, p.query, ""))


def _key(url: str) -> str:
    """비교용: 호스트(www 제외)+경로 그대로(빈 경로만 '/')+;매개변수+?쿼리."""
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path or '/'}" + (f";{p.params}" if p.params else "") + (f"?{p.query}" if p.query else "")


def _is_home(url: str) -> bool:
    p = urlparse(url)
    return p.path in ("", "/") and not p.params and not p.query


def _known_urls(sources: Sequence[Source]) -> tuple[set[str], set[str]]:
    """(자료 주소·본문에 적힌 주소들의 키, 본문에 '도메인만' 적힌 호스트들)."""
    full: set[str] = set()
    hosts: set[str] = set()
    for s in sources:
        n = _normalize(s.url)
        if n:
            full.add(_key(n))
        for token in _SPLIT.split(s.text):
            run = _ASCII_RUN.match(token)        # '홈택스(www.hometax.go.kr)에서' → 'www.hometax.go.kr'
            if not run or "." not in run.group(0):
                continue
            candidate = run.group(0)
            after_scheme = candidate.split("://", 1)[-1]
            if not re.search(r"[/?#;\\@]", after_scheme):
                candidate = candidate.rstrip(".,")    # 도메인만 적힌 경우에만 문장 끝 마침표·쉼표를 뗀다
            n = _normalize(candidate)
            if not n:
                continue                          # 덩어리 전체가 주소 형식이 아니면 버린다
            full.add(_key(n))
            if _is_home(n) and not re.search(r"[/?#;]", candidate.split("://", 1)[-1].rstrip("/")):
                hosts.add((urlparse(n).hostname or "").removeprefix("www."))
    return full, hosts


def check_action_links(links: Sequence[ActionLink], sources: Sequence[Source]) -> tuple[list[CheckedLink], list[str]]:
    """(통과한 링크, 뺀 이유 목록). 같은 주소는 한 번만, 최대 4개."""
    full, hosts = _known_urls(sources)
    ok: list[CheckedLink] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for link in links:
        url = _normalize(link.url)
        if url is None:
            rejected.append(f"주소 형식 오류: {link.url[:80]}")
            continue
        if not is_official(url):
            rejected.append(f"공식 사이트가 아님: {url[:80]}")
            continue
        key = _key(url)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if key not in full and not (_is_home(url) and host in hosts):
            rejected.append(f"자료에서 확인되지 않은 주소: {url[:80]}")
            continue
        if key in seen:
            continue
        if len(ok) >= MAX_LINKS:
            rejected.append(f"링크가 {MAX_LINKS}개를 넘어 뺌: {url[:80]}")
            continue
        seen.add(key)
        ok.append(CheckedLink(link.label.strip()[:40] or "바로가기", url, link.purpose,
                              (link.note or "").strip()[:80] or None))
    return ok, rejected
