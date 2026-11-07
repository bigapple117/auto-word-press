"""Pixabay에서 장면별 배경 영상·사진을 찾아 받는다.

- 쇼츠는 세로 화면이라 세로 영상 우선, 없으면 큰 가로 영상, 그다음 세로 사진, 그래도 없으면 None(그라데이션).
- Pixabay 규칙: 검색 결과는 24시간 저장해 재사용(요청 줄이기), 파일은 우리 서버에 받아서 씀(직접 링크 금지).
- 다운로드는 Pixabay 주소(https)만, 크기·시간 제한.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Sequence
from urllib.parse import urlparse

import requests

from src.config import Settings

log = logging.getLogger(__name__)

API_VIDEOS = "https://pixabay.com/api/videos/"
API_IMAGES = "https://pixabay.com/api/"
ALLOWED_DOWNLOAD_HOSTS = frozenset({"cdn.pixabay.com", "pixabay.com"})
CACHE_SECONDS = 24 * 3600
MAX_VIDEO_BYTES = 60_000_000
MAX_IMAGE_BYTES = 20_000_000
DOWNLOAD_TIMEOUT = 120          # 파일 하나 전체 시간(초)
MIN_VIDEO_SECONDS = 4
MIN_PORTRAIT_HEIGHT = 1280      # 세로 영상 최소 높이
MIN_LANDSCAPE_HEIGHT = 1080     # 가로 영상은 잘라 쓰므로 더 커야 함
MEDIA_CACHE_MAX_BYTES = 3_000_000_000   # 받은 미디어 보관함 최대 3GB(오래 안 쓴 것부터 지움)
MIN_FREE_BYTES = 3_000_000_000          # 디스크 여유가 이보다 적으면 받지 않음


class MediaError(RuntimeError):
    """검색·다운로드 실패."""


@dataclass(frozen=True)
class MediaPick:
    path: Path
    kind: str           # video / image
    provider: str       # pixabay
    media_id: int
    page_url: str       # 출처 페이지(크레딧용)
    author: str
    query: str

    @property
    def credit(self) -> str:
        return f"{self.author} / Pixabay"


def _safe_download_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_DOWNLOAD_HOSTS:
        raise MediaError(f"허용되지 않은 다운로드 주소: {url[:80]}")
    return url


class PixabayClient:
    def __init__(self, api_key: str, cache_dir: Path, session: requests.Session | None = None,
                 timeout: float = 20, clock=time.time) -> None:
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.http = session or requests.Session()
        self.timeout = timeout
        self.clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> "PixabayClient":
        settings.require("pixabay_api_key")
        return cls(settings.pixabay_api_key, settings.data_dir / "cache" / "pixabay")

    # ----- 검색(24시간 저장) -----
    def _cache_file(self, endpoint: str, params: dict) -> Path:
        key = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False)
        return self.cache_dir / "search" / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")

    def _search(self, endpoint: str, params: dict) -> list[dict[str, Any]]:
        cache = self._cache_file(endpoint, params)
        if cache.exists():
            try:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if self.clock() - saved["saved_at"] < CACHE_SECONDS:
                    return saved["hits"]
            except (ValueError, KeyError):
                pass
        try:
            resp = self.http.get(endpoint, params={"key": self.api_key, "safesearch": "true", **params},
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            # from None: 원래 예외에 API 키가 든 요청 주소가 담길 수 있어 연결을 끊는다
            raise MediaError(f"Pixabay 접속 실패: {exc.__class__.__name__}") from None
        if resp.status_code == 429:
            raise MediaError("Pixabay 호출 한도 초과(1분 100회)")
        if resp.status_code != 200:
            raise MediaError(f"Pixabay 오류 {resp.status_code}")
        try:
            hits = resp.json().get("hits", [])
        except ValueError:
            raise MediaError("Pixabay 응답이 JSON이 아닙니다") from None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"saved_at": self.clock(), "hits": hits}, ensure_ascii=False),
                         encoding="utf-8")
        return hits

    def search_videos(self, query: str, per_page: int = 20) -> list[dict[str, Any]]:
        return self._search(API_VIDEOS, {"q": query[:100], "per_page": per_page})

    def search_images(self, query: str, orientation: str = "vertical", per_page: int = 20) -> list[dict[str, Any]]:
        return self._search(API_IMAGES, {"q": query[:100], "per_page": per_page,
                                         "image_type": "photo", "orientation": orientation})

    # ----- 다운로드 -----
    def download(self, url: str, dest: Path, max_bytes: int) -> Path:
        """이미 받은 파일이면 그대로 쓴다(사용 시각 갱신). 실행마다 고유한 임시 파일로 받아 완료되면 교체한다."""
        if dest.exists() and dest.stat().st_size > 0:
            os.utime(dest)
            return dest
        _safe_download_url(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(dest.parent).free < MIN_FREE_BYTES:
            raise MediaError("디스크 여유 공간이 부족해 받지 않습니다")
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
        tmp = Path(tmp_name)
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT
        try:
            with os.fdopen(fd, "wb") as f, \
                    self.http.get(url, stream=True, timeout=self.timeout, allow_redirects=False) as resp:
                # 감시 타이머: 전체 시간이 다 되면 연결을 끊어 막힌 읽기를 깨운다
                watchdog = threading.Timer(DOWNLOAD_TIMEOUT, _cut_response, args=(resp,))
                watchdog.daemon = True
                watchdog.start()
                try:
                    if resp.status_code != 200:
                        raise MediaError(f"다운로드 응답 코드 {resp.status_code}")
                    size = 0
                    for chunk in resp.iter_content(262144):
                        size += len(chunk)
                        if size > max_bytes:
                            raise MediaError("파일이 너무 큽니다")
                        if time.monotonic() > deadline:
                            raise MediaError("다운로드 시간 초과")
                        f.write(chunk)
                finally:
                    watchdog.cancel()
            if time.monotonic() > deadline:
                raise MediaError("다운로드 시간 초과")
            if size == 0:
                raise MediaError("빈 파일입니다")
            tmp.replace(dest)
            return dest
        except (requests.RequestException, OSError) as exc:
            if time.monotonic() > deadline:
                raise MediaError("다운로드 시간 초과") from None
            raise MediaError(f"다운로드 실패: {exc.__class__.__name__}") from None
        finally:
            tmp.unlink(missing_ok=True)


def _cut_response(resp) -> None:
    raw = getattr(resp, "raw", None)
    sock = getattr(getattr(raw, "connection", None), "sock", None)
    if sock is None:
        sock = getattr(getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def media_is_valid(ffprobe: Path, path: Path, kind: str) -> bool:
    """받은 파일이 정상인지(영상·사진 스트림이 있고, 영상은 길이가 있는지) 확인한다."""
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        info = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    if result.returncode != 0:
        return False
    video = [st for st in info.get("streams", []) if st.get("codec_type") == "video" and st.get("width")]
    if not video:
        return False
    if kind == "video":
        try:
            return float(info.get("format", {}).get("duration", 0)) >= 1
        except (TypeError, ValueError):
            return False
    return True


def prune_cache(media_dir: Path, max_bytes: int = MEDIA_CACHE_MAX_BYTES, keep: set[Path] | None = None) -> int:
    """보관함이 상한을 넘으면 오래 안 쓴 파일부터 지운다. 지운 바이트 수를 돌려준다."""
    if not media_dir.exists():
        return 0
    keep = {p.resolve() for p in (keep or set())}
    files = [p for p in media_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
    total = sum(p.stat().st_size for p in files)
    freed = 0
    for p in sorted(files, key=lambda f: f.stat().st_mtime):
        if total <= max_bytes:
            break
        if p.resolve() in keep:
            continue
        size = p.stat().st_size
        p.unlink(missing_ok=True)
        total -= size
        freed += size
    return freed


_STOP = {"a", "an", "the", "and", "of", "on", "in", "with", "for", "to", "at"}


def by_relevance(hits: Sequence[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """태그에 검색어 단어가 많이 든 결과를 앞으로(Pixabay 기본 순서는 인기순이라 엉뚱한 것이 먼저 올 수 있음)."""
    words = {w for w in re.findall(r"[a-z]+", (query or "").lower()) if w not in _STOP}
    if not words:
        return list(hits)

    def score(h: dict[str, Any]) -> int:
        # Pixabay 태그는 앞쪽일수록 사진의 주인공 → 앞 3개 태그에 맞으면 3점, 나머지 태그는 1점
        tag_list = [t.strip().lower() for t in str(h.get("tags", "")).split(",") if t.strip()]
        head = set(re.findall(r"[a-z]+", " ".join(tag_list[:3])))
        rest = set(re.findall(r"[a-z]+", " ".join(tag_list[3:])))
        total = 0
        for w in words:
            forms = {w, w.rstrip("s")}
            total += 3 if forms & head else (1 if forms & rest else 0)
        return total

    scored = [(score(h), i, h) for i, h in enumerate(hits)]
    matched = [x for x in scored if x[0] > 0]
    return [h for _s, _i, h in sorted(matched, key=lambda x: (-x[0], x[1]))]   # 태그가 하나도 안 맞으면 쓰지 않음


def choose_video(hits: Sequence[dict[str, Any]], exclude: set[int]) -> tuple[dict, str] | None:
    """(결과, 화질 이름). 세로 영상 우선, 없으면 큰 가로 영상."""
    portrait, landscape = [], []
    for h in hits:
        if h.get("id") in exclude or h.get("duration", 0) < MIN_VIDEO_SECONDS:
            continue
        for variant in ("medium", "large", "small"):
            v = (h.get("videos") or {}).get(variant) or {}
            w, ht, url = v.get("width", 0), v.get("height", 0), v.get("url", "")
            if not url or v.get("size", 0) > MAX_VIDEO_BYTES:
                continue
            if ht > w and ht >= MIN_PORTRAIT_HEIGHT:
                portrait.append((h, variant))
                break
            if w >= ht and ht >= MIN_LANDSCAPE_HEIGHT:
                landscape.append((h, variant))
                break
    return (portrait or landscape or [None])[0]


# Pixabay 사진 번호는 올린 순서대로 붙는다. 작가가 한 번에 올린 연작(각도만 조금 다른 거의 같은 사진)은
# 번호가 바로 이웃하므로, 이미 쓴 사진과 번호 차이가 이 안이면 같은 사진으로 보고 피한다.
SERIES_ID_GAP = 20


def _same_series(media_id: int, series: Collection[int]) -> bool:
    return any(abs(media_id - x) <= SERIES_ID_GAP for x in series)


def choose_image(hits: Sequence[dict[str, Any]], exclude: set[int], min_height: int = 1600,
                 series: Collection[int] = ()) -> dict | None:
    """exclude는 그 번호만, series는 그 번호와 같은 연작(번호가 이웃한 사진)까지 피한다."""
    for h in hits:
        if (h.get("id") not in exclude and not _same_series(h.get("id", 0), series)
                and h.get("imageHeight", 0) >= min_height and h.get("largeImageURL")):
            return h
    return None


LETTERBOX_MIN_RATIO = 0.97     # 실제 화면이 이보다 작으면(검은 띠) 쓰지 않음
MAX_VIDEO_TRIES = 3


def has_letterbox(ffmpeg: Path, path: Path, width: int, height: int) -> bool:
    """영상 안에 검은 띠가 들어 있는지 앞부분 2초로 검사한다. 검사 실패는 띠 없음으로 본다."""
    try:
        result = subprocess.run(
            ["nice", "-n", "15", str(ffmpeg), "-hide_banner", "-ss", "1", "-t", "2", "-i", str(path),
             "-vf", "cropdetect=24:16:0", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    found = re.findall(r"crop=(\d+):(\d+):\d+:\d+", result.stderr)
    if not found or not width or not height:
        return False
    w, h = map(int, found[-1])
    return w < width * LETTERBOX_MIN_RATIO or h < height * LETTERBOX_MIN_RATIO


BLOG_IMAGE_SIZE = (1200, 675)    # 블로그 본문 사진: 16:9로 통일


def normalize_blog_image(src: Path, dest_dir: Path, size: tuple[int, int] = BLOG_IMAGE_SIZE) -> Path:
    """가운데를 16:9로 잘라 1200x675 JPEG로 저장한다(비율 통일·용량 절감). 이미 있으면 재사용."""
    from PIL import Image, ImageOps

    dest = dest_dir / f"{src.stem}_{size[0]}x{size[1]}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        fitted = ImageOps.fit(im, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        fd, tmp_name = tempfile.mkstemp(dir=dest_dir, prefix=dest.name + ".", suffix=".part")
        os.close(fd)
        try:
            fitted.save(tmp_name, "JPEG", quality=82, optimize=True, progressive=True)
            Path(tmp_name).replace(dest)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
    return dest


def _simpler(query: str) -> str | None:
    words = query.split()
    return " ".join(words[:2]) if len(words) > 2 else None


class StockMediaPicker:
    def __init__(self, client: PixabayClient, media_dir: Path, ffmpeg: Path | None = None) -> None:
        self.client = client
        self.media_dir = media_dir
        self.ffmpeg = ffmpeg            # 있으면 깨진 파일·검은 띠 영상을 걸러냄
        self.ffprobe = ffmpeg.with_name("ffprobe") if ffmpeg else None

    def _usable(self, path: Path, kind: str) -> bool:
        """깨진 파일은 보관함에서 지우고 쓰지 않는다."""
        if self.ffprobe and not media_is_valid(self.ffprobe, path, kind):
            log.warning("깨진 파일이라 지우고 건너뜀: %s", path.name)
            path.unlink(missing_ok=True)
            return False
        return True

    @classmethod
    def from_settings(cls, settings: Settings) -> "StockMediaPicker":
        return cls(PixabayClient.from_settings(settings), settings.data_dir / "media_cache" / "pixabay",
                   settings.ffmpeg_bin)

    def _video(self, query: str, used: set[int]) -> MediaPick | None:
        hits = self.client.search_videos(query)
        skip = set(used)
        for _ in range(MAX_VIDEO_TRIES):
            found = choose_video(by_relevance(hits, query), skip)
            if not found:
                return None
            hit, variant = found
            info = hit["videos"][variant]
            path = self.client.download(info["url"], self.media_dir / f"v{hit['id']}_{variant}.mp4",
                                        MAX_VIDEO_BYTES)
            if not self._usable(path, "video"):
                skip.add(hit["id"])
                continue
            if self.ffmpeg and has_letterbox(self.ffmpeg, path, info.get("width", 0), info.get("height", 0)):
                log.info("검은 띠가 있는 영상이라 건너뜀: %s", hit["id"])
                skip.add(hit["id"])
                continue
            return MediaPick(path, "video", "pixabay", hit["id"], hit.get("pageURL", ""),
                             hit.get("user", ""), query)
        return None

    def _image(self, query: str, used: set[int], orientation: str, series: bool = False) -> MediaPick | None:
        """series=True면 이미 쓴 사진의 연작도 피한다(used가 모두 사진 번호일 때만: 영상 번호는 따로 매겨짐)."""
        hits = self.client.search_images(query, orientation)
        skip = set(used)
        near = set(used) if series else set()
        for _ in range(MAX_VIDEO_TRIES):
            hit = choose_image(by_relevance(hits, query), skip, min_height=1600 if orientation == "vertical" else 900,
                               series=near)
            if not hit:
                return None
            ext = Path(urlparse(hit["largeImageURL"]).path).suffix.lower() or ".jpg"
            path = self.client.download(hit["largeImageURL"], self.media_dir / f"i{hit['id']}{ext}",
                                        MAX_IMAGE_BYTES)
            if not self._usable(path, "image"):
                skip.add(hit["id"])
                continue
            return MediaPick(path, "image", "pixabay", hit["id"], hit.get("pageURL", ""),
                             hit.get("user", ""), query)
        return None

    def pick_scene_backgrounds(self, queries: Sequence[str]) -> list[MediaPick | None]:
        """장면별 배경. 한 쇼츠 안에서 같은 영상·사진을 다시 쓰지 않는다. 실패한 장면은 None."""
        used: set[int] = set()
        picks: list[MediaPick | None] = []
        for query in queries:
            pick = None
            for q in filter(None, (query, _simpler(query))):
                try:
                    pick = self._video(q, used) or self._image(q, used, "vertical")
                except MediaError as exc:
                    log.warning("배경 찾기 실패(%s): %s", q, exc)
                    pick = None
                if pick:
                    break
            if pick:
                used.add(pick.media_id)
            picks.append(pick)
        prune_cache(self.media_dir, keep={p.path for p in picks if p})
        return picks

    def pick_blog_images(self, queries: Sequence[str | None],
                         avoid: Collection[int] = ()) -> list[MediaPick | None]:
        """블로그 소제목별 가로 사진(겹치지 않게). 검색어가 비어 있는 소제목은 None.
        avoid: 다른 글에서 이미 쓴 사진 번호. 목록 화면에서 대표 사진이 겹쳐 보이지 않게 먼저 피한다.
        끝나면 보관함 상한을 맞춘다(이번에 쓴 사진은 보호)."""
        used: set[int] = set()
        picks = [self.pick_blog_image(q, used, avoid) if q and q.strip() else None for q in queries]
        prune_cache(self.media_dir, keep={p.path for p in picks if p})
        return picks

    def pick_blog_image(self, query: str, used: set[int] | None = None,
                        avoid: Collection[int] = ()) -> MediaPick | None:
        """블로그 본문용 가로 사진 하나(보관함 정리는 pick_blog_images에서).
        다른 글에서 안 쓴 사진을 먼저 찾고, 검색어에 맞는 새 사진이 없을 때만 다른 글의 사진을 다시 쓴다
        (같은 글 안에서는 어떤 경우에도 겹치지 않음)."""
        used = used if used is not None else set()
        queries = list(filter(None, (query, _simpler(query))))
        rounds = [used | set(avoid), used] if avoid else [used]
        for skip in rounds:
            for q in queries:
                try:
                    pick = self._image(q, skip, "horizontal", series=True)
                except MediaError as exc:
                    log.warning("블로그 사진 찾기 실패(%s): %s", q, exc)
                    continue
                if pick:
                    if skip is used and _same_series(pick.media_id, avoid):
                        log.info("다른 글에서 안 쓴 사진이 없어 이미 쓴 사진을 다시 씀(%s): %s", q, pick.media_id)
                    used.add(pick.media_id)
                    return pick
        return None
