"""워드프레스 REST API로 사진·영상 업로드, 임시 글 저장, 발행을 한다.

중복 방지: 글 본문에 보이지 않는 작업 표시(<!-- jalcheck-job:jcjob-<번호> -->)를 넣고,
새 글을 만들기 전에 그 표시로 먼저 찾는다. 이미 있으면 새로 만들지 않고 그 글을 고친다.
(글을 만든 직후 장부에 기록하기 전에 프로그램이 멈춰도 다음 실행이 같은 글을 찾는다.)
미디어도 파일 이름에 작업 표시를 넣어 같은 방식으로 다시 올리지 않는다.
"""
from __future__ import annotations

import fcntl
import logging
import mimetypes
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from src.config import ROOT, Settings

log = logging.getLogger(__name__)

MARKER_PREFIX = "jcjob-"
_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")
LOCK_DIR = ROOT / "data" / "locks"
MAX_SEARCH_PAGES = 20


class WPError(RuntimeError):
    """워드프레스 요청 실패."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class WPPost:
    id: int
    status: str
    link: str


@dataclass(frozen=True)
class WPMedia:
    id: int
    url: str


def job_marker(job_id: int) -> str:
    return f"{MARKER_PREFIX}{int(job_id)}"


def marker_comment(job_id: int) -> str:
    return f"<!-- jalcheck-job:{job_marker(job_id)} -->"


@contextmanager
def job_lock(job_id: int, lock_dir: Path = LOCK_DIR) -> Iterator[None]:
    """같은 작업의 글 저장·발행은 한 번에 하나만(같은 서버 안의 동시 실행 방지)."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"wp-job-{int(job_id)}.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class WordPressClient:
    def __init__(self, base_url: str, username: str, app_password: str,
                 session: requests.Session | None = None, timeout: float = 60,
                 lock_dir: Path = LOCK_DIR) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WPError("워드프레스 주소는 https여야 합니다(비밀번호가 암호화 없이 오가지 않게)")
        self.api = base_url.rstrip("/") + "/wp-json/wp/v2"
        self.http = session or requests.Session()
        self.auth = (username, app_password.replace(" ", ""))
        self.timeout = timeout
        self.lock_dir = lock_dir

    @classmethod
    def from_settings(cls, settings: Settings) -> "WordPressClient":
        settings.require("wp_base_url", "wp_username", "wp_app_password")
        return cls(settings.wp_base_url, settings.wp_username, settings.wp_app_password)

    # ----- 공통 요청 -----
    def _request(self, method: str, path: str, **kwargs) -> Any:
        return self._request_full(method, path, **kwargs)[0]

    def _get_all(self, path: str, params: dict) -> list[dict[str, Any]]:
        """검색 결과의 모든 페이지를 모은다(첫 페이지 밖의 기존 글을 놓치지 않게)."""
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_SEARCH_PAGES + 1):
            data, headers = self._request_full("GET", path, params={**params, "per_page": 100, "page": page})
            items += data
            try:
                total_pages = int(headers.get("X-WP-TotalPages", "1"))
            except ValueError:
                total_pages = 1
            if page >= total_pages:
                return items
        raise WPError(f"검색 결과가 너무 많습니다({path}). 직접 확인이 필요합니다")

    def _request_full(self, method: str, path: str, **kwargs) -> tuple[Any, Any]:
        try:
            resp = self.http.request(method, self.api + path, auth=self.auth, timeout=self.timeout,
                                     allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise WPError(f"워드프레스 접속 실패: {exc.__class__.__name__}") from None
        if resp.status_code in (301, 302, 307, 308):
            raise WPError(f"워드프레스가 다른 주소로 보냈습니다({resp.status_code}). WP_BASE_URL을 확인하세요",
                          resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            message = data.get("message") if isinstance(data, dict) else ""
            if resp.status_code == 401:
                message = "인증 실패: WP_USERNAME / WP_APP_PASSWORD를 확인하세요"
            raise WPError(f"워드프레스 오류 {resp.status_code} {code or ''}: {message}"[:300],
                          resp.status_code, code)
        if data is None:
            raise WPError("워드프레스 응답이 JSON이 아닙니다", resp.status_code)
        return data, getattr(resp, "headers", {}) or {}

    def whoami(self) -> dict[str, Any]:
        """인증 확인용. 이름과 권한을 돌려준다."""
        me = self._request("GET", "/users/me", params={"context": "edit"})
        return {"id": me.get("id"), "name": me.get("name"), "roles": me.get("roles", []),
                "can_publish": bool(me.get("capabilities", {}).get("publish_posts"))}

    # ----- 글 -----
    def find_post_by_job(self, job_id: int) -> WPPost | None:
        """작업 표시가 정확히 든 글(임시·발행·대기·비공개·예약)을 찾는다. 휴지통 글은 무시한다."""
        posts = self._get_all("/posts", {
            "search": job_marker(job_id), "status": "draft,pending,publish,private,future",
            "context": "edit", "_fields": "id,status,link,content"})
        exact = [p for p in posts if marker_comment(job_id) in (p.get("content", {}).get("raw") or "")]
        if len(exact) > 1:
            raise WPError(f"작업 {job_id}번 표시가 든 글이 {len(exact)}개입니다. 직접 정리가 필요합니다")
        if exact:
            p = exact[0]
            return WPPost(p["id"], p["status"], p.get("link", ""))
        return None

    def _post_for_job(self, job_id: int, post_id: int | None) -> WPPost | None:
        """장부에 적힌 글 번호가 있으면 그 글이 이 작업의 글인지 확인한다. 없어졌으면 표시로 다시 찾는다."""
        if post_id:
            try:
                p = self._request("GET", f"/posts/{int(post_id)}",
                                  params={"context": "edit", "_fields": "id,status,link,content"})
            except WPError as exc:
                if exc.status != 404:
                    raise
                p = None
            if p and p.get("status") != "trash":
                if marker_comment(job_id) not in (p.get("content", {}).get("raw") or ""):
                    raise WPError(f"글 {post_id}번은 작업 {job_id}번의 글이 아닙니다. 덮어쓰지 않습니다")
                return WPPost(p["id"], p["status"], p.get("link", ""))
        return self.find_post_by_job(job_id)

    def save_draft(self, job_id: int, title: str, html: str, slug: str = "", excerpt: str = "",
                   featured_media: int | None = None, post_id: int | None = None) -> WPPost:
        """임시 글로 저장한다. 이 작업의 기존 글이 있으면 그 글을 고친다.

        - 이미 발행된 글은 고치지 않는다.
        - 기존 글을 고칠 때는 상태 값을 보내지 않는다(그사이 사람이 발행했어도 임시 글로 되돌리지 않게).
        """
        with job_lock(job_id, self.lock_dir):
            existing = self._post_for_job(job_id, post_id)
            if existing and existing.status in ("publish", "future", "private"):
                label = {"publish": "발행", "future": "예약 발행", "private": "비공개 발행"}[existing.status]
                raise WPError(f"작업 {job_id}번 글은 이미 {label}됐습니다(글 {existing.id}번). 고치지 않습니다")
            body: dict[str, Any] = {
                "title": title, "content": f"{marker_comment(job_id)}\n{html}", "excerpt": excerpt,
            }
            if slug:
                body["slug"] = slug
            if featured_media:
                body["featured_media"] = featured_media
            if existing:
                data = self._request("POST", f"/posts/{existing.id}", json=body)
            else:
                data = self._request("POST", "/posts", json={**body, "status": "draft"})
            if data.get("status") == "publish":
                log.warning("작업 %s번 글이 저장 도중 발행 상태가 됐습니다(사람이 발행한 것으로 보임)", job_id)
            return WPPost(data["id"], data["status"], data.get("link", ""))

    def get_post(self, post_id: int) -> WPPost | None:
        try:
            p = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit",
                                                                       "_fields": "id,status,link"})
        except WPError as exc:
            if exc.status == 404:
                return None
            raise
        if p.get("status") == "trash":
            return None
        return WPPost(p["id"], p["status"], p.get("link", ""))

    def publish(self, job_id: int, post_id: int) -> WPPost:
        """이 작업의 임시 글을 발행한다. 이미 발행됐으면 그대로 돌려준다(두 번 눌러도 안전)."""
        with job_lock(job_id, self.lock_dir):
            current = self._post_for_job(job_id, post_id)
            if current is None or current.id != int(post_id):
                raise WPError(f"작업 {job_id}번의 글 {post_id}번이 없습니다(삭제됐거나 휴지통)")
            if current.status == "publish":
                return current
            data = self._request("POST", f"/posts/{int(post_id)}", json={"status": "publish"})
            if data.get("status") != "publish":
                raise WPError(f"발행 요청 뒤 상태가 {data.get('status')}입니다")
            return WPPost(data["id"], data["status"], data.get("link", ""))

    # ----- 미디어 -----
    def find_media(self, filename_stem: str) -> WPMedia | None:
        """파일 이름이 정확히 이 이름(워드프레스가 붙이는 '-1' 같은 꼬리 허용)인 미디어를 찾는다."""
        pattern = re.compile(rf"^{re.escape(filename_stem)}(-\d+)?$")
        items = self._get_all("/media", {"search": filename_stem, "_fields": "id,source_url"})
        matches = [m for m in items if pattern.match(Path(urlparse(m.get("source_url", "")).path).stem)]
        if matches:
            m = min(matches, key=lambda x: x["id"])     # 가장 먼저 올린 것
            return WPMedia(m["id"], m["source_url"])
        return None

    def upload_media(self, path: Path, job_id: int, index: int, alt: str = "",
                     caption: str = "") -> WPMedia:
        """작업 번호·순서가 든 파일 이름으로 올린다. 같은 이름이 이미 있으면 다시 올리지 않는다."""
        stem = f"{job_marker(job_id)}-{int(index)}"
        with job_lock(job_id, self.lock_dir):
            found = self.find_media(stem)
            if found:
                return found
            return self._upload(path, stem, alt, caption)

    def _upload(self, path: Path, stem: str, alt: str, caption: str) -> WPMedia:
        ext = path.suffix.lower()
        mime = mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"
        if not mime.startswith(("image/", "video/")):
            raise WPError(f"올릴 수 없는 파일 형식: {ext}")
        name = _SAFE_NAME.sub("-", f"{stem}{ext}")
        with path.open("rb") as f:
            data = self._request("POST", "/media", data=f, headers={
                "Content-Type": mime, "Content-Disposition": f'attachment; filename="{name}"'})
        media = WPMedia(data["id"], data.get("source_url", ""))
        if alt or caption:
            self._request("POST", f"/media/{media.id}", json={"alt_text": alt, "caption": caption})
        return media
