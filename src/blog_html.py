"""MasterContent를 워드프레스에 올릴 HTML로 바꾼다.

AI가 쓴 글자는 모두 이스케이프한다(태그·스크립트가 섞여도 글자로만 보임).
이미지는 stock_media 단계가 끝난 뒤 소제목 번호별로 넘겨받아 넣는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Mapping, Sequence
from urllib.parse import urlparse

from src.content_schema import MasterContent, Table
from src.source_fetcher import Source


@dataclass(frozen=True)
class SectionImage:
    url: str
    alt: str
    credit: str = ""        # 예: "Photo by 홍길동 on Pexels"
    credit_url: str = ""


def _safe_url(url: str) -> str | None:
    """http/https 주소만 통과시킨다(javascript: 등 차단)."""
    url = (url or "").strip()
    return url if urlparse(url).scheme in ("http", "https") else None


def _p(text: str) -> str:
    return f"<p>{escape(text)}</p>"


def _table(t: Table) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in t.headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in t.rows
    )
    return (
        '<figure class="wp-block-table"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        f"<figcaption>{escape(t.caption)}</figcaption></figure>"
    )


def _checklist(items: Sequence[str]) -> str:
    lis = "".join(f"<li>{escape(i)}</li>" for i in items)
    return f'<ul class="jc-checklist">{lis}</ul>'


def _image(img: SectionImage) -> str:
    src = _safe_url(img.url)
    if src is None:
        return ""
    credit = ""
    if img.credit:
        text = escape(img.credit)
        link = _safe_url(img.credit_url)
        if link:
            text = f'<a href="{escape(link)}" rel="nofollow noopener" target="_blank">{text}</a>'
        credit = f"<figcaption>{text}</figcaption>"
    return (
        f'<figure class="wp-block-image"><img src="{escape(src)}" alt="{escape(img.alt)}" '
        f'loading="lazy" decoding="async">{credit}</figure>'
    )


def render_blog_html(
    content: MasterContent,
    sources: Sequence[Source],
    images: Mapping[int, SectionImage] | None = None,
) -> str:
    """본문 HTML(제목 제외)을 돌려준다. images 키는 소제목 번호(0부터)."""
    images = images or {}
    out: list[str] = [_p(t) for t in content.intro]
    out.append(f'<p class="jc-reference"><strong>기준</strong>: {escape(content.reference_note)}</p>')

    for i, s in enumerate(content.sections):
        out.append(f"<h2>{escape(s.heading)}</h2>")
        if i in images:
            out.append(_image(images[i]))
        out += [_p(t) for t in s.paragraphs]
        if s.table:
            out.append(_table(s.table))
        if s.checklist:
            out.append(_checklist(s.checklist))

    if content.faq:
        out.append("<h2>자주 묻는 질문</h2>")
        for f in content.faq:
            out.append(f"<h3>{escape(f.question)}</h3>")
            out.append(_p(f.answer))

    out.append(_p(content.closing))

    used = {sid for s in content.sections for sid in s.source_ids}
    used |= {sid for f in content.faq for sid in f.source_ids}
    cited = [s for s in sources if s.id in used and _safe_url(s.url)]
    if cited:
        items = "".join(
            f'<li><a href="{escape(s.url)}" target="_blank" rel="noopener">{escape(s.title)}</a></li>'
            for s in cited
        )
        out.append(f'<h2>참고 자료</h2><ul class="jc-sources">{items}</ul>')
    return "\n".join(out)
