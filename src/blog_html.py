"""MasterContent를 워드프레스에 올릴 HTML로 바꾼다.

AI가 쓴 글자는 모두 이스케이프한다(태그·스크립트가 섞여도 글자로만 보임).
이미지는 stock_media 단계가 끝난 뒤 소제목 번호별로 넘겨받아 넣는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Mapping, Sequence
from urllib.parse import urlparse

from src.content_schema import Block, MasterContent, Paragraph, Table
from src.link_check import CheckedLink, check_action_links
from src.source_fetcher import Source


@dataclass(frozen=True)
class SectionImage:
    url: str
    alt: str
    credit: str = ""        # 예: "Photo by 홍길동 on Pexels"
    credit_url: str = ""
    width: int = 0
    height: int = 0
    media_id: int = 0       # 워드프레스 미디어 번호(있으면 반응형 크기 자동 적용)


def _safe_url(url: str) -> str | None:
    """http/https 주소만 통과시킨다(javascript: 등 차단)."""
    url = (url or "").strip()
    return url if urlparse(url).scheme in ("http", "https") else None


def _p(text: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<p{attr}>{escape(text)}</p>"


def _spans(p: Paragraph) -> str:
    """문단 조각을 이어 붙인다. strong은 불리언 값만 보고 코드가 <strong>을 만든다."""
    return "".join(f"<strong>{escape(sp.text)}</strong>" if sp.strong is True else escape(sp.text)
                   for sp in p.spans)


def _paragraph(p: Paragraph | None, cls: str = "") -> str:
    if p is None or not p.spans:
        return ""
    attr = f' class="{cls}"' if cls else ""
    return f"<p{attr}>{_spans(p)}</p>"


def _table(t: Table, index: int) -> str:
    """표 제목은 위에, 열·행 머리글 표시, 좁은 화면에서는 표만 가로로 밀어 보게 감싼다."""
    width = len(t.headers)
    first_col_short = all(len(r[0]) <= 10 for r in t.rows if r)
    classes = ["jc-table"]
    if width >= 4:
        classes.append("jc-table--wide")
    if first_col_short:
        classes.append("jc-table--key")
    head = "".join(f'<th scope="col">{escape(h)}</th>' for h in t.headers)
    labels = [escape(h, quote=True) for h in t.headers]
    body = "".join(
        "<tr>" + "".join(
            f'<th scope="row" data-label="{labels[j]}">{escape(c)}</th>' if j == 0
            else f'<td data-label="{labels[j] if j < len(labels) else ""}">{escape(c)}</td>'
            for j, c in enumerate(row)) + "</tr>"
        for row in t.rows
    )
    cap_id = f"jc-table-{index}"
    return (
        f'<div class="jc-scroll" role="region" aria-labelledby="{cap_id}" tabindex="0">'
        f'<table class="{" ".join(classes)}"><caption id="{cap_id}">{escape(t.caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _checklist(items: Sequence[str]) -> str:
    lis = "".join(f"<li>{escape(i)}</li>" for i in items)
    return f'<ul class="jc-checklist">{lis}</ul>'


def _note(b: Block) -> str:
    kind = "caution" if b.note_kind == "caution" else "tip"
    label = "주의" if kind == "caution" else "확인 요령"
    inner = _spans(b.note) if b.note else ""
    return f'<p class="jc-note jc-note--{kind}"><span class="jc-note__label">{label}</span> {inner}</p>'


_PURPOSE_LABEL = {"apply": "신청", "check": "조회", "info": "안내"}


def _links_box(links: Sequence[CheckedLink]) -> str:
    """도입부 아래 '신청·조회 바로가기' 상자. 주소는 검증을 통과한 공식 주소만 온다."""
    if not links:
        return ""
    items = []
    for link in links:
        note = f'<span class="jc-link__note">{escape(link.note)}</span>' if link.note else ""
        items.append(
            f'<li><a class="jc-link jc-link--{escape(link.purpose)}" href="{escape(link.url, quote=True)}" '
            f'target="_blank" rel="noopener"><span class="jc-link__tag">{_PURPOSE_LABEL.get(link.purpose, "안내")}</span>'
            f'<span class="jc-link__label">{escape(link.label)}</span>{note}</a></li>')
    return f'<nav class="jc-links" aria-label="신청·조회 바로가기"><p class="jc-links__title">신청·조회 바로가기</p><ul>{"".join(items)}</ul></nav>'


def _block(b: Block, table_index: int) -> str:
    if b.type == "paragraph":
        return _paragraph(b.paragraph)
    if b.type == "table" and b.table:
        return _table(b.table, table_index)
    if b.type == "checklist" and b.items:
        return _checklist(b.items)
    if b.type == "note" and b.note:
        return _note(b)
    return ""


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
    size = f' width="{int(img.width)}" height="{int(img.height)}"' if img.width and img.height else ""
    cls = f' class="wp-image-{int(img.media_id)}"' if img.media_id else ""
    return (
        f'<figure class="wp-block-image size-large"><img src="{escape(src)}" alt="{escape(img.alt)}"'
        f'{size}{cls} loading="lazy" decoding="async">{credit}</figure>'
    )


ABOUT_PATH = "/about/"


def _editorial_box(checked_on: str | None) -> str:
    """글 끝 '편집 기준' 상자(신뢰 정보). 지어낸 경험 대신 확인 방법을 투명하게 밝힌다."""
    when = f" 자료 확인일: {escape(checked_on)}." if checked_on else ""
    return (
        '<aside class="jc-editorial" aria-label="편집 기준"><p class="jc-editorial__title">잘체크 편집 기준</p>'
        "<p>이 글은 정부·공공기관의 공식 안내 자료를 직접 확인해 정리했습니다." + when +
        " 제도는 바뀔 수 있고 개인 상황에 따라 결과가 다를 수 있으니, 신청 전에 담당 기관에서 최종 확인하세요. "
        f'<a href="{ABOUT_PATH}">잘체크 소개와 편집 원칙 보기</a></p></aside>'
    )


def render_blog_html(
    content: MasterContent,
    sources: Sequence[Source],
    images: Mapping[int, SectionImage] | None = None,
    checked_on: str | None = None,
) -> str:
    """본문 HTML(제목 제외)을 돌려준다. images 키는 소제목 번호(0부터).

    전체를 <div class="jc-article">로 감싸 사이트 공통 CSS(jc-*)가 이 글에만 적용되게 한다.
    """
    images = images or {}
    out: list[str] = ['<div class="jc-article">']
    out += [_paragraph(p) for p in content.intro]
    out.append(f'<p class="jc-reference">기준: {escape(content.reference_note)}</p>')
    if content.eligibility.strip():
        out.append(f'<p class="jc-reference">대상: {escape(content.eligibility.strip())}</p>')
    links, _rejected = check_action_links(content.action_links, sources)
    if links:
        out.append(_links_box(links))

    table_index = 0
    for i, s in enumerate(content.sections):
        out.append(f"<h2>{escape(s.heading)}</h2>")
        if s.takeaway:
            out.append(_p(s.takeaway, "jc-summary"))
        if i in images:
            out.append(_image(images[i]))
        for b in s.blocks:
            if b.type == "table":
                table_index += 1
            html = _block(b, table_index)
            if html:
                out.append(html)

    if content.faq:
        out.append("<h2>자주 묻는 질문</h2>")
        for f in content.faq:
            out.append(f'<h3 class="jc-faq-q">{escape(f.question)}</h3>')
            out.append(_p(f.answer))

    out.append(_paragraph(content.closing, "jc-closing"))

    used = {sid for s in content.sections for sid in s.source_ids}
    used |= {sid for f in content.faq for sid in f.source_ids}
    cited = [s for s in sources if s.id in used and _safe_url(s.url)]
    if cited:
        items = "".join(
            f'<li><a href="{escape(s.url)}" target="_blank" rel="noopener">{escape(s.title)}</a></li>'
            for s in cited
        )
        out.append(f'<h2>참고 자료</h2><ul class="jc-sources">{items}</ul>')
    out.append(_editorial_box(checked_on))
    out.append("</div>")
    return "\n".join(out)
