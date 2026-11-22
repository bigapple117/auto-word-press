"""작업 한 건을 단계별로 처리하는 부품 모음.

오케스트레이터가 장부(db)의 상태를 바꾸고, 여기 함수들이 실제 일을 한다.
각 단계 결과는 JSON으로 돌려주며 장부 체크포인트에 저장된다(다시 실행하면 끝난 단계는 건너뜀).
외부 서비스는 Services에 모아 두어 시험할 때 가짜로 바꿀 수 있게 했다.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable, Collection, Sequence

from src.ai_generator import ContentGenerator
from src.auto_refresh import FIGURE_WARNING, check_warnings, split_figures
from src.blog_html import SectionImage, render_blog_html
from src.config import ROOT, Settings
from src.law_source import LawClient, find_law_sources
from src.recent_change import find_recent_changes
from src.link_check import is_home
from src.content_schema import MasterContent, slug_without_year
from src.image_judge import GeminiImageJudge
from src.remote_reader import GeminiPageReader
from src.source_fetcher import Source, fetch_sources
from src.source_finder import NaverSearch, find_official_urls
from src.stock_media import BLOG_IMAGE_SIZE, StockMediaPicker, normalize_blog_image
from src.telegram_notifier import TelegramNotifier
from src.gemini_tts import synthesize_gemini
from src.tts_engine import synthesize_scenes
from src.video_renderer import render_short
from src.wp_client import WordPressClient

log = logging.getLogger(__name__)

MAX_SOURCES = 4


class PipelineError(RuntimeError):
    """작업을 계속할 수 없는 문제(사람이 보고 고쳐야 함)."""


@dataclass
class Services:
    settings: Settings
    search: Any
    reader: Any
    generator: Any
    picker: Any
    wp: Any
    telegram: Any
    synthesize: Callable = synthesize_scenes
    synthesize_gemini: Callable = synthesize_gemini
    render: Callable = render_short
    fetch: Callable = fetch_sources
    find_urls: Callable = find_official_urls
    screenshot: Callable | None = None
    laws: Any = None                 # 국가법령정보 API(LAW_OC가 없으면 법령 조회를 건너뜀)
    find_laws: Callable = find_law_sources
    find_changes: Callable | None = find_recent_changes   # 최근 변경(확정된 개정) 기사 찾기. None이면 건너뜀
    judge: Callable | None = None    # 대표 사진을 AI가 보고 고름(없거나 실패하면 검색 순서 첫 후보)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Services":
        return cls(
            settings=settings,
            search=NaverSearch.from_settings(settings),
            reader=GeminiPageReader.from_settings(settings),
            generator=ContentGenerator.from_settings(settings),
            picker=StockMediaPicker.from_settings(settings),
            wp=WordPressClient.from_settings(settings),
            telegram=TelegramNotifier.from_settings(settings),
            screenshot=take_screenshot,
            laws=LawClient(settings.law_oc) if settings.law_oc else None,
            judge=GeminiImageJudge.from_settings(settings),
        )


def job_dir(settings: Settings, job_id: int) -> Path:
    d = settings.data_dir / "jobs" / str(int(job_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sources_from(data: Sequence[dict]) -> list[Source]:
    return [Source(s["id"], s["url"], s["title"], s.get("text", ""), s.get("via", "direct")) for s in data]


# ---------- 1. 자료 + 글 ----------

VERDICT_WORDS = {"wrong": "틀림", "unsure": "확인 불가"}


def _short(text: str, n: int = 60) -> str:
    """승인 알림이 길어지지 않게 AI 근거를 한 줄로 줄인다."""
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def verify_figures(generator: Any, warnings: Sequence[str], sources: Sequence[Source],
                   article: str) -> tuple[list[str], list[str]]:
    """'자료에서 못 찾은 숫자' 경고를 1순위 모델이 자료 원문과 대조(auto_refresh.check_warnings 재사용).
    자료에 단위 없이 적힌 표 숫자(예: 만 원 단위 표의 '3.5')처럼 프로그램이 못 맞춘 숫자를 사람 확인에서 덜어 내려는 것.
    맞다고(ok) 판정된 숫자만 경고에서 빼 확인 기록(fixes)으로 옮기고, 틀림·확인 불가는 근거와 함께 경고에 남긴다.
    대조를 못 하면(오류·자료 과다·예비 모델) 원래 경고 그대로. 돌려주는 값: (경고, 확인 기록)."""
    figures = split_figures(warnings)
    if not figures or getattr(generator, "structured", None) is None:
        return list(warnings), []
    try:
        checks = check_warnings(generator, figures, [{"url": s.url, "text": s.text} for s in sources], article)
    except Exception as exc:  # noqa: BLE001 - 대조는 덧붙이는 검사라 실패하면 사람이 확인하면 된다
        log.warning("숫자 경고 AI 대조 실패: %s: %s", exc.__class__.__name__, exc)
        return list(warnings), []
    ok = [c for c in checks if c.verdict == "ok"]
    bad = [c for c in checks if c.verdict != "ok"]
    kept = [w for w in warnings if not str(w).startswith(FIGURE_WARNING)]
    if bad:   # 알림은 경고 8줄까지만 보이므로 판정 근거는 한 줄로 모은다
        kept.append(f"{FIGURE_WARNING}(확인 필요): " + ", ".join(figures[c.id] for c in bad))
        kept.append("AI 대조: " + " / ".join(f"{figures[c.id]} {VERDICT_WORDS.get(c.verdict, c.verdict)}"
                                             f"({_short(c.why)})" for c in bad))
    checked = (["숫자 자료 대조 확인(AI): " + " / ".join(f"{figures[c.id]} ({_short(c.why)})" for c in ok)] if ok else [])
    return kept, checked


def make_content(svc: Services, topic: str, target_seconds: int, checks: Sequence[str] = (),
                 must_read: Sequence[str] = ()) -> dict:
    """must_read: 꼭 읽을 주소(근거 페이지가 바뀌어 자동 등록된 갱신이면 그 페이지). 검색 결과보다 앞에 둔다."""
    # 사이트 첫 화면(예: hometax.go.kr/)은 메뉴뿐이라 근거 자료에서 뺀다.
    # 바로가기 링크로는 다른 자료 본문에 그 도메인이 적혀 있을 때만 남는다(link_check)
    first = [u for u in dict.fromkeys(must_read) if u and not is_home(u)]
    urls = first + [u for u in svc.find_urls(svc.search, topic) if not is_home(u) and u not in first]
    try:
        news = svc.search.news(topic, display=5)
    except Exception as exc:  # noqa: BLE001 - 뉴스는 참고용이라 실패해도 진행
        log.warning("뉴스 검색 실패: %s", exc)
        news = []
    sources, errors = svc.fetch(urls, max_sources=MAX_SOURCES, fallback=svc.reader) if urls else ([], [])
    if not sources:
        # 법령 조문만으로는 신청 방법·기간·누리집을 쓸 수 없으므로 공식 안내 자료가 꼭 있어야 한다
        raise PipelineError("읽을 수 있는 공식 자료를 찾지 못했습니다: " + " / ".join(errors[:3]))
    if svc.laws is not None:
        laws, law_errors = svc.find_laws(svc.laws, svc.generator.choose_law_articles, topic, len(sources) + 1)
        sources, errors = list(sources) + laws, list(errors) + law_errors
    # 최근 변경: 안내 페이지가 늦게 고쳐지는 경우를 대비해 확정된 개정만 기사 인용 그대로 자료에 더한다
    changes = None
    read_changes = getattr(svc.generator, "read_changes", None)
    if svc.find_changes is not None and read_changes is not None:
        try:
            changes = svc.find_changes(svc.search, svc.fetch, read_changes, topic,
                                       len(sources) + 1, skip_urls=[s.url for s in sources])
            sources, errors = list(sources) + changes.sources, list(errors) + changes.errors
        except Exception as exc:  # noqa: BLE001 - 덧붙이는 조사라 실패해도 글은 계속 쓴다
            log.warning("최근 변경 찾기 실패: %r", exc)
            errors = list(errors) + [f"최근 변경 찾기 실패: {exc.__class__.__name__}"]
    result = svc.generator.generate(topic, sources, target_seconds,
                                    news=[(n.published[5:16], n.title) for n in news], checks=checks)
    # 발행 전 자동 검토: 자료와 다시 대조해 고치고, 사람에게는 끝내 판단 못 한 것만 넘긴다
    result = svc.generator.review(topic, sources, target_seconds, result)
    # 사실 확인: 문장마다 자료와 대조, 끝내 근거를 못 찾은 글은 아침 자동 발행에서 뺀다
    result, fact = svc.generator.fact_check(topic, sources, target_seconds, result)
    warnings = list(result.warnings)
    fallback = result.model != svc.settings.gemini_model
    if fallback:
        warnings.insert(0, f"예비 모델({result.model}) 작성 — {svc.settings.gemini_model}이(가) 한도 초과·혼잡이라 대신 썼습니다. 품질 확인 필요")
    checked: list[str] = []
    if not fallback:   # 예비 모델로 썼다면 1순위 모델이 한도라 대조도 못 하므로 부르지 않는다
        before = split_figures(warnings)
        warnings, checked = verify_figures(svc.generator, warnings, sources, result.content.model_dump_json())
        # AI가 맞다고 본 숫자도 남겨, 사람 없이 반영하는 갱신 자동 반영에서 한 번 더 대조하게 한다
        cleared = [f for f in before if f not in split_figures(warnings)]
    else:
        cleared = []
    return {
        "content": json.loads(result.content.model_dump_json()),
        "warnings": warnings,
        "fixes": (changes.notes if changes else []) + checked + list(result.fixes),   # 알림은 앞 8줄만 보이므로 먼저
        "fact_check": fact.as_dict(),
        "cleared_figures": cleared,
        "recent_changes": changes.as_dict() if changes else {},
        "fallback_model": fallback,
        "source_errors": errors,
        "model": result.model,
        "sources": [{"id": s.id, "url": s.url, "title": s.title, "via": s.via, "text": s.text} for s in sources],
        "news": [{"date": n.published[:16], "title": n.title, "url": n.url} for n in news],
    }


# ---------- 2. 쇼츠: 성우·자막, 배경, 합성 ----------

def make_tts(svc: Services, job_id: int, content: MasterContent, target_seconds: int) -> dict:
    """성우 음성·자막 시간. Gemini 음성(기본)이 안 되면 edge-tts로 대체해 멈추지 않는다."""
    out = job_dir(svc.settings, job_id) / "short"
    narrations = [s.narration for s in content.shorts.scenes]
    st = svc.settings
    if st.tts_engine == "gemini":
        try:
            r = svc.synthesize_gemini(narrations, out, st.gemini_api_key, st.gemini_tts_model,
                                      st.gemini_tts_voice, st.ffmpeg_bin)
            return {"audio": str(r.audio_path), "timeline": str(r.timeline_path), "duration": r.duration,
                    "rate": r.rate, "engine": "gemini"}
        except Exception as exc:  # noqa: BLE001 - 시간 초과·파일 오류 등 무엇이든 기본 음성으로 대체해 멈추지 않는다
            log.warning("Gemini 음성을 못 써서 기본 음성으로 대체: %s: %s", exc.__class__.__name__, exc)
    r = svc.synthesize(narrations, out, st.tts_voice, st.ffmpeg_bin, target_seconds=target_seconds)
    return {"audio": str(r.audio_path), "timeline": str(r.timeline_path), "duration": r.duration,
            "rate": r.rate, "engine": "edge"}


def _pick_json(p) -> dict | None:
    if p is None:
        return None
    return {"file": str(p.path), "kind": p.kind, "id": p.media_id, "credit": p.credit, "page": p.page_url}


def pick_media(svc: Services, content: MasterContent, with_shorts: bool,
               avoid: Collection[int] = ()) -> dict:
    """avoid: 다른 글에서 이미 쓴 블로그 사진 번호(가능하면 피함)."""
    queries = [s.image_query for s in content.sections]
    scene_queries = [s.video_query for s in content.shorts.scenes]
    if svc.judge is not None:
        blog = svc.picker.pick_blog_images(queries, avoid=avoid, judge=svc.judge, topic=content.title,
                                           extra_queries=scene_queries)
    else:
        blog = svc.picker.pick_blog_images(queries, avoid=avoid)
    if not any(blog):
        # 사진 검색어가 하나도 없거나 못 찾으면 쇼츠 장면 검색어로 대표 사진 하나는 마련한다(AI 판정 → 예전 방식).
        # 사진 검색어가 있었다면 장면 검색어 판정도 pick_blog_images에서 이미 했으므로 다시 판정하지 않는다.
        first = next((q for q in scene_queries if q and q.strip()), "")
        had_queries = any(q and q.strip() for q in queries)
        extra = svc.picker.judged_featured(scene_queries, set(), avoid, svc.judge, content.title) \
            if svc.judge is not None and not had_queries else None
        extra = extra or (svc.picker.pick_blog_image(first, avoid=avoid) if first else None)
        blog = [extra] + [None] * (len(content.sections) - 1) if extra else blog
    scenes = svc.picker.pick_scene_backgrounds([s.video_query for s in content.shorts.scenes]) if with_shorts else []
    return {"blog": [_pick_json(p) for p in blog], "scenes": [_pick_json(p) for p in scenes]}


def make_video(svc: Services, job_id: int, content: MasterContent, tts: dict, media: dict) -> dict:
    out = job_dir(svc.settings, job_id) / "short" / "short.mp4"
    backgrounds = [Path(m["file"]) if m else None for m in media["scenes"]]
    r = svc.render(Path(tts["timeline"]), Path(tts["audio"]), backgrounds, out, svc.settings.ffmpeg_bin,
                   svc.settings.ffprobe_bin, svc.settings.fonts_dir, title=content.shorts.title,
                   cards=[sc.card for sc in content.shorts.scenes], hook=content.shorts.hook,
                   topic=content.focus_keyword, music=svc.settings.music_file)
    return {"path": str(r.path), "duration": r.duration, "seconds_taken": r.seconds_taken}


# ---------- 3. 워드프레스 임시 글 ----------

def make_draft(svc: Services, job_id: int, content: MasterContent, sources: list[Source], media: dict,
               post_id: int | None) -> dict:
    """사진을 올리고 임시 글을 저장한다. 첫 사진은 대표 사진으로도 쓴다.

    글 화면 맨 위에는 대표 사진을 보여 주지 않으므로(single.html) 첫 사진도 본문 제 소제목 아래에 넣는다.
    """
    w, h = BLOG_IMAGE_SIZE
    images: dict[int, SectionImage] = {}
    featured = None
    for i, (section, pick) in enumerate(zip(content.sections, media["blog"])):
        if not pick:
            continue
        fitted = normalize_blog_image(Path(pick["file"]), svc.settings.data_dir / "media_cache" / "blog")
        alt = (section.image_alt or section.heading).strip()
        up = svc.wp.upload_media(fitted, job_id, i, alt=alt, caption=pick["credit"])
        if featured is None:
            featured = up.id
        images[i] = SectionImage(up.url, alt, f"사진: {pick['credit']}", pick["page"],
                                 width=w, height=h, media_id=up.id)
    checked_on = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y년 %-m월 %-d일")
    html = render_blog_html(content, sources, images, checked_on=checked_on)
    tag_ids = svc.wp.ensure_tags([content.focus_keyword, *content.tags][:5])
    category, category_warning = choose_category(svc, content)
    post = svc.wp.save_draft(job_id, content.title, html, slug=slug_without_year(content.slug),
                             excerpt=content.meta_description, featured_media=featured, post_id=post_id,
                             tags=tag_ids, categories=[category["id"]] if category else None)
    return {"post_id": post.id, "status": post.status, "link": post.link, "featured": featured,
            "category": category["name"] if category else "", "category_warning": category_warning}


def choose_category(svc: Services, content: MasterContent) -> tuple[dict | None, str]:
    """(고른 분류 또는 None, 경고). 고르지 못하면 분류를 보내지 않는다 — 새 글은 워드프레스 기본 분류,
    이미 있던 임시 글은 원래 분류 그대로라 승인 때 확인하게 알린다."""
    try:
        cats = svc.wp.list_categories()
        options = [(c["slug"], c["name"], c.get("description") or "") for c in cats]
        slug = svc.generator.choose_category(content.title, content.meta_description,
                                             [content.focus_keyword, *content.tags], options)
        return next(c for c in cats if c["slug"] == slug), ""
    except Exception as exc:  # noqa: BLE001 - 분류를 못 골라도 글 저장은 계속한다
        log.warning("분류 자동 선택 실패(기본 분류로 저장): %s", exc)
        return None, f"분류 자동 선택 실패 — 분류를 바꾸지 않고 저장(새 글은 기본 분류), 발행 전 워드프레스에서 분류 확인 ({exc})"


def take_screenshot(post_id: int, out_dir: Path) -> list[Path]:
    """scripts/screenshot_post.py를 별도 프로세스로 실행(브라우저가 실행기 메모리를 잡아먹지 않게)."""
    cmd = ["nice", "-n", "15", sys.executable, str(ROOT / "scripts" / "screenshot_post.py"), str(post_id),
           "--out", str(out_dir)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise PipelineError(f"화면 캡처 실패: {result.stderr.strip()[-300:]}")
    return [out_dir / f"post{post_id}_mobile.png"]


def request_approval(svc: Services, job_id: int, content: MasterContent, warnings: Sequence[str],
                     post_id: int, video_path: Path | None, category: str = "",
                     screenshots: Sequence[Path] | None = None, file_ids: dict | None = None,
                     fixes: Sequence[str] = ()) -> dict:
    """승인 요청을 보낸다. screenshots를 주면 새로 찍지 않고 그 파일을 쓴다(다시 보내기를 빠르게).
    file_ids: 전에 올린 파일의 텔레그램 번호(있으면 다시 올리지 않음). 돌려주는 값에 새 번호까지 담는다."""
    shots: list[Path] = list(screenshots or [])
    if screenshots is None and svc.screenshot:
        try:
            shots = svc.screenshot(post_id, job_dir(svc.settings, job_id) / "screens")
        except Exception as exc:  # noqa: BLE001 - 캡처가 안 돼도 승인 요청은 보낸다
            log.warning("화면 캡처 실패(승인 요청은 계속): %s", exc)
            warnings = [f"화면 캡처 실패: {exc}"] + list(warnings)
    edit_url = f"{svc.settings.wp_base_url}/wp-admin/post.php?post={int(post_id)}&action=edit"
    req = svc.telegram.send_approval_request(job_id, content.title, content.meta_description, edit_url,
                                             warnings=list(warnings), video_path=video_path, screenshots=shots,
                                             category=category, file_ids=file_ids, fixes=list(fixes))
    return {"nonce": req.nonce, "message_id": req.message_id, "file_ids": dict(req.file_ids or {})}
