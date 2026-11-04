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
from pathlib import Path
from typing import Any, Callable, Sequence

from src.ai_generator import ContentGenerator
from src.blog_html import SectionImage, render_blog_html
from src.config import ROOT, Settings
from src.content_schema import MasterContent
from src.remote_reader import GeminiPageReader
from src.source_fetcher import Source, fetch_sources
from src.source_finder import NaverSearch, find_official_urls
from src.stock_media import BLOG_IMAGE_SIZE, StockMediaPicker, normalize_blog_image
from src.telegram_notifier import TelegramNotifier
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
    render: Callable = render_short
    fetch: Callable = fetch_sources
    find_urls: Callable = find_official_urls
    screenshot: Callable | None = None

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
        )


def job_dir(settings: Settings, job_id: int) -> Path:
    d = settings.data_dir / "jobs" / str(int(job_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sources_from(data: Sequence[dict]) -> list[Source]:
    return [Source(s["id"], s["url"], s["title"], s.get("text", ""), s.get("via", "direct")) for s in data]


# ---------- 1. 자료 + 글 ----------

def make_content(svc: Services, topic: str, target_seconds: int) -> dict:
    urls = svc.find_urls(svc.search, topic)
    try:
        news = svc.search.news(topic, display=5)
    except Exception as exc:  # noqa: BLE001 - 뉴스는 참고용이라 실패해도 진행
        log.warning("뉴스 검색 실패: %s", exc)
        news = []
    sources, errors = svc.fetch(urls, max_sources=MAX_SOURCES, fallback=svc.reader)
    if not sources:
        raise PipelineError("읽을 수 있는 공식 자료를 찾지 못했습니다: " + " / ".join(errors[:3]))
    result = svc.generator.generate(topic, sources, target_seconds,
                                    news=[(n.published[5:16], n.title) for n in news])
    return {
        "content": json.loads(result.content.model_dump_json()),
        "warnings": list(result.warnings),
        "source_errors": errors,
        "model": result.model,
        "sources": [{"id": s.id, "url": s.url, "title": s.title, "via": s.via, "text": s.text} for s in sources],
        "news": [{"date": n.published[:16], "title": n.title, "url": n.url} for n in news],
    }


# ---------- 2. 쇼츠: 성우·자막, 배경, 합성 ----------

def make_tts(svc: Services, job_id: int, content: MasterContent, target_seconds: int) -> dict:
    out = job_dir(svc.settings, job_id) / "short"
    r = svc.synthesize([s.narration for s in content.shorts.scenes], out, svc.settings.tts_voice,
                       svc.settings.ffmpeg_bin, target_seconds=target_seconds)
    return {"audio": str(r.audio_path), "timeline": str(r.timeline_path), "duration": r.duration, "rate": r.rate}


def _pick_json(p) -> dict | None:
    if p is None:
        return None
    return {"file": str(p.path), "kind": p.kind, "id": p.media_id, "credit": p.credit, "page": p.page_url}


def pick_media(svc: Services, content: MasterContent, with_shorts: bool) -> dict:
    blog = svc.picker.pick_blog_images([s.image_query for s in content.sections])
    if not any(blog):
        # 사진 검색어가 하나도 없거나 못 찾으면 쇼츠 첫 장면 검색어로 대표 사진 하나는 마련한다
        first = content.shorts.scenes[0].video_query if content.shorts.scenes else ""
        extra = svc.picker.pick_blog_image(first) if first else None
        blog = [extra] + [None] * (len(content.sections) - 1) if extra else blog
    scenes = svc.picker.pick_scene_backgrounds([s.video_query for s in content.shorts.scenes]) if with_shorts else []
    return {"blog": [_pick_json(p) for p in blog], "scenes": [_pick_json(p) for p in scenes]}


def make_video(svc: Services, job_id: int, content: MasterContent, tts: dict, media: dict) -> dict:
    out = job_dir(svc.settings, job_id) / "short" / "short.mp4"
    backgrounds = [Path(m["file"]) if m else None for m in media["scenes"]]
    r = svc.render(Path(tts["timeline"]), Path(tts["audio"]), backgrounds, out, svc.settings.ffmpeg_bin,
                   svc.settings.ffprobe_bin, svc.settings.fonts_dir, title=content.shorts.title)
    return {"path": str(r.path), "duration": r.duration, "seconds_taken": r.seconds_taken}


# ---------- 3. 워드프레스 임시 글 ----------

def make_draft(svc: Services, job_id: int, content: MasterContent, sources: list[Source], media: dict,
               post_id: int | None) -> dict:
    """사진을 올리고 임시 글을 저장한다. 첫 사진은 대표 사진(본문에는 다시 넣지 않음)."""
    w, h = BLOG_IMAGE_SIZE
    images: dict[int, SectionImage] = {}
    featured = None
    for i, (section, pick) in enumerate(zip(content.sections, media["blog"])):
        if not pick:
            continue
        fitted = normalize_blog_image(Path(pick["file"]), svc.settings.data_dir / "media_cache" / "blog")
        up = svc.wp.upload_media(fitted, job_id, i, alt=section.heading, caption=pick["credit"])
        if featured is None:
            featured = up.id
            continue
        images[i] = SectionImage(up.url, section.heading, f"사진: {pick['credit']}", pick["page"],
                                 width=w, height=h, media_id=up.id)
    html = render_blog_html(content, sources, images)
    post = svc.wp.save_draft(job_id, content.title, html, slug=content.slug,
                             excerpt=content.meta_description, featured_media=featured, post_id=post_id)
    return {"post_id": post.id, "status": post.status, "link": post.link, "featured": featured}


def take_screenshot(post_id: int, out_dir: Path) -> list[Path]:
    """scripts/screenshot_post.py를 별도 프로세스로 실행(브라우저가 실행기 메모리를 잡아먹지 않게)."""
    cmd = ["nice", "-n", "15", sys.executable, str(ROOT / "scripts" / "screenshot_post.py"), str(post_id),
           "--out", str(out_dir)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise PipelineError(f"화면 캡처 실패: {result.stderr.strip()[-300:]}")
    return [out_dir / f"post{post_id}_mobile.png"]


def request_approval(svc: Services, job_id: int, content: MasterContent, warnings: Sequence[str],
                     post_id: int, video_path: Path | None) -> dict:
    shots: list[Path] = []
    if svc.screenshot:
        try:
            shots = svc.screenshot(post_id, job_dir(svc.settings, job_id) / "screens")
        except Exception as exc:  # noqa: BLE001 - 캡처가 안 돼도 승인 요청은 보낸다
            log.warning("화면 캡처 실패(승인 요청은 계속): %s", exc)
            warnings = [f"화면 캡처 실패: {exc}"] + list(warnings)
    edit_url = f"{svc.settings.wp_base_url}/wp-admin/post.php?post={int(post_id)}&action=edit"
    req = svc.telegram.send_approval_request(job_id, content.title, content.meta_description, edit_url,
                                             warnings=list(warnings), video_path=video_path, screenshots=shots)
    return {"nonce": req.nonce, "message_id": req.message_id}
