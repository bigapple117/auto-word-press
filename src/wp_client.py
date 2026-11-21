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
from typing import Any, Callable, Iterator
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


_MARKER_RE = re.compile(r"<!-- jalcheck-job:[A-Za-z0-9_-]+ -->")


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

    def ensure_tags(self, names: list[str]) -> list[int]:
        """이름이 정확히 같은 태그를 찾고, 없으면 만든다. 태그 번호 목록."""
        ids: list[int] = []
        for name in dict.fromkeys(n.strip()[:40] for n in names if n and n.strip()):
            found = [t for t in self._get_all("/tags", {"search": name, "_fields": "id,name"}) if t["name"] == name]
            if found:
                ids.append(found[0]["id"])
                continue
            try:
                ids.append(self._request("POST", "/tags", json={"name": name})["id"])
            except WPError as exc:
                if exc.code == "term_exists":     # 동시에 만들어진 경우
                    found = [t for t in self._get_all("/tags", {"search": name, "_fields": "id,name"}) if t["name"] == name]
                    ids += [found[0]["id"]] if found else []
                else:
                    raise
        return ids

    def list_categories(self) -> list[dict[str, Any]]:
        """사이트의 분류 전부(글이 없는 분류 포함): id, slug, name, description."""
        return self._get_all("/categories", {"_fields": "id,slug,name,description", "hide_empty": "false"})

    def save_draft(self, job_id: int, title: str, html: str, slug: str = "", excerpt: str = "",
                   featured_media: int | None = None, post_id: int | None = None,
                   tags: list[int] | None = None, categories: list[int] | None = None) -> WPPost:
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
            if tags:
                body["tags"] = tags
            if categories:
                body["categories"] = categories
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

    def list_published_posts(self) -> list[dict[str, Any]]:
        """발행된 글 전부: id, link, title(글자), date_gmt(발행), modified_gmt."""
        items = self._get_all("/posts", {"status": "publish", "context": "edit",
                                         "_fields": "id,link,title,date_gmt,modified_gmt"})
        return [{"id": p["id"], "link": p["link"], "title": (p.get("title") or {}).get("raw", ""),
                 "date_gmt": p["date_gmt"], "modified_gmt": p["modified_gmt"]} for p in items]

    def get_post_title(self, post_id: int) -> str:
        p = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit", "_fields": "title"})
        return (p.get("title") or {}).get("raw", "")

    def _get_or_none(self, post_id: int, fields: str) -> dict[str, Any] | None:
        try:
            p = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit", "_fields": fields})
        except WPError as exc:
            if exc.status == 404:
                return None
            raise
        return p

    def refresh_applied(self, job_id: int, target_id: int) -> WPPost | None:
        """갱신 작업 job_id의 내용이 이미 원래 글에 들어갔으면 그 글, 아니면 None."""
        t = self._get_or_none(target_id, "id,status,link,content")
        if t and t.get("status") == "publish" and marker_comment(job_id) in (t.get("content", {}).get("raw") or ""):
            return WPPost(t["id"], t["status"], t.get("link", ""))
        return None

    def refresh_marked(self, job_id: int, target_id: int) -> bool:
        """원래 글에 이 갱신 작업의 표시가 있는지(공개 여부와 무관). 재작업 차단용."""
        t = self._get_or_none(target_id, "id,content")
        return bool(t) and marker_comment(job_id) in (t.get("content", {}).get("raw") or "")

    def apply_refresh(self, job_id: int, draft_id: int, target_id: int) -> WPPost:
        """갱신본 임시 글(draft_id)의 내용을 발행된 원래 글(target_id)에 덮어쓰고 임시 글은 휴지통으로.

        주소(slug)·작성일·상태는 보내지 않으므로 그대로다. 이미 반영됐으면 임시 글만 정리하고 돌려준다
        (두 번 눌러도, 중간에 끊겨 다시 눌러도 안전)."""
        with job_lock(job_id, self.lock_dir):
            marker = marker_comment(job_id)
            target = self._get_or_none(target_id, "id,status,link,content")
            if not target or target.get("status") != "publish":
                raise WPError(f"원래 글 {target_id}번이 발행 상태가 아닙니다. 갱신하지 않습니다", 409)
            draft = self._get_or_none(draft_id, "id,status,title,content,excerpt,featured_media,tags,categories")
            draft_live = bool(draft and draft.get("status") != "trash")
            if marker not in (target.get("content", {}).get("raw") or ""):
                if not draft_live or marker not in (draft.get("content", {}).get("raw") or ""):
                    raise WPError(f"작업 {job_id}번의 갱신본 임시 글 {draft_id}번이 없습니다(삭제됐거나 휴지통)", 409)
                if draft.get("status") != "draft":
                    raise WPError(f"갱신본 글 {draft_id}번이 임시 글이 아닙니다({draft.get('status')}). 직접 확인이 필요합니다", 409)
                # 원래 글을 처음 쓴 작업의 표시도 남긴다(그 작업으로 하던 대표 사진 교체 등이 계속 되게)
                old = [m for m in dict.fromkeys(_MARKER_RE.findall(target.get("content", {}).get("raw") or ""))
                       if m not in draft["content"]["raw"]]
                body: dict[str, Any] = {
                    "title": draft["title"]["raw"], "content": "\n".join([*old, draft["content"]["raw"]]),
                    "excerpt": draft["excerpt"]["raw"], "tags": draft.get("tags") or [],
                }
                if draft.get("featured_media"):
                    body["featured_media"] = draft["featured_media"]
                if draft.get("categories"):
                    body["categories"] = draft["categories"]
                target = self._request("POST", f"/posts/{int(target_id)}", json=body)
                if target.get("status") != "publish":
                    raise WPError(f"갱신 뒤 원래 글 상태가 {target.get('status')}입니다")
            if draft_live:
                try:   # force 없음 = 휴지통(되살릴 수 있음). 원래 글은 이미 바뀌었으니 실패해도 갱신은 완료로 본다
                    self._request("DELETE", f"/posts/{int(draft_id)}")
                except WPError as exc:
                    log.warning("작업 %s번: 갱신은 반영했지만 임시 글 %s번 휴지통 이동 실패(직접 정리): %s",
                                job_id, draft_id, exc)
            return WPPost(target["id"], target["status"], target.get("link", ""))

    def get_post_content(self, post_id: int) -> str:
        """글 본문 HTML(관리자 화면에서 직접 고친 내용 포함)."""
        p = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit", "_fields": "content"})
        return p["content"]["raw"]

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

    def delete_job_media(self, job_id: int) -> list[int]:
        """이 작업이 올린 미디어(파일 이름 jcjob-<번호>-<순서>)만 영구 삭제한다. 다시 쓰기 전에 사용."""
        pattern = re.compile(rf"^{re.escape(job_marker(job_id))}-\d+(-\d+)?$")
        with job_lock(job_id, self.lock_dir):
            items = self._get_all("/media", {"search": f"{job_marker(job_id)}-", "_fields": "id,source_url"})
            ids = [m["id"] for m in items if pattern.match(Path(urlparse(m.get("source_url", "")).path).stem)]
            for media_id in ids:
                self._request("DELETE", f"/media/{media_id}", params={"force": "true"})
        return ids

    def upload_media(self, path: Path, job_id: int, index: int, alt: str = "",
                     caption: str = "") -> WPMedia:
        """작업 번호·순서가 든 파일 이름으로 올린다. 같은 이름이 이미 있으면 다시 올리지 않는다."""
        stem = f"{job_marker(job_id)}-{int(index)}"
        with job_lock(job_id, self.lock_dir):
            found = self.find_media(stem)
            if found:
                return found
            return self._upload(path, stem, alt, caption)

    def _media_in_use(self, media_id: int, stem: str, except_post: int, skip_own_body: bool = False) -> bool:
        """이 첨부를 다른 글의 대표 사진으로 쓰거나, 어느 글·페이지 본문에든 이 파일 이름이 있으면 True.
        skip_own_body: except_post 글의 본문은 보지 않는다(그 본문의 사진도 함께 바꿀 때)."""
        for kind in ("posts", "pages"):
            items = self._get_all(f"/{kind}", {"status": "any", "context": "edit",
                                               "_fields": "id,featured_media,content"})
            for it in items:
                own = kind == "posts" and it.get("id") == except_post
                if not own and int(it.get("featured_media") or 0) == int(media_id):
                    return True
                if own and skip_own_body:
                    continue
                if stem in ((it.get("content") or {}).get("raw") or ""):
                    return True
        return False

    def replace_featured(self, job_id: int, post_id: int, index: int, path: Path, old_media_id: int,
                         alt: str = "", caption: str = "",
                         on_switched: Callable[[WPMedia], None] | None = None,
                         body_figure: Callable[[WPMedia], str] | None = None) -> tuple[WPMedia, bool]:
        """글의 대표 사진을 새 사진으로 바꾼다(상태는 건드리지 않음). (새 사진, 옛 사진 삭제 여부)

        body_figure가 없으면 대표 사진만 바꾼다(본문은 그대로). 있으면 대표 사진이 본문에도 들어 있는 글(11-17 이후)로
        보고, 본문의 옛 사진 묶음(<figure>) 딱 하나를 body_figure(새 사진)로 바꿔 대표 사진과 같은 요청으로 저장한다.
        본문에서 옛 사진을 정확히 한 곳에서 찾지 못하면 아무것도 바꾸지 않는다.

        새 사진을 같은 이름(jcjob-<번호>-<순서>)으로 올려 대표 사진으로 걸고 → on_switched(장부 기록) →
        옛 사진을 지운다. 화면에 대표 사진이 빈 순간이 없고, 나중에 화면만 다시 만들어도 이름으로 새 사진을 찾는다.
        - 같은 이름의 첨부가 이미 2개 이상이면(예전 실패의 흔적) 아무것도 바꾸지 않고 멈춘다.
        - 옛 사진을 다른 글이 쓰거나 어느 본문에든 들어 있으면 지우지 않고 남긴다(이때 이름이 겹치므로 알림)."""
        stem = f"{job_marker(job_id)}-{int(index)}"
        name_re = re.compile(rf"^{re.escape(stem)}(-\d+)?$")
        with job_lock(job_id, self.lock_dir):
            post = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit",
                                                                          "_fields": "id,featured_media,content"})
            if marker_comment(job_id) not in (post.get("content") or {}).get("raw", ""):
                raise WPError(f"글 {post_id}번은 작업 {job_id}번 글이 아닙니다")
            if int(post.get("featured_media") or 0) != int(old_media_id):
                raise WPError(f"글 {post_id}번의 대표 사진이 예상({old_media_id})과 다릅니다"
                              f"({post.get('featured_media')}). 사람이 바꾼 것일 수 있어 건드리지 않습니다")
            same_name = [m for m in self._get_all("/media", {"search": stem, "_fields": "id,source_url"})
                         if name_re.match(Path(urlparse(m.get("source_url", "")).path).stem)]
            if [m["id"] for m in same_name] != [int(old_media_id)]:
                raise WPError(f"'{stem}' 이름의 첨부가 {[m['id'] for m in same_name]}입니다(옛 대표 사진 하나여야 함). "
                              "예전 교체가 중간에 멈춘 흔적일 수 있어 바꾸지 않습니다. 직접 확인이 필요합니다")
            raw = (post.get("content") or {}).get("raw", "")
            fig = _old_figure(raw, stem, int(old_media_id)) if body_figure else None
            in_use = self._media_in_use(old_media_id, stem, except_post=int(post_id),
                                        skip_own_body=body_figure is not None)
            new = self._upload(path, stem, alt, caption)
            left = f"새로 올린 첨부 {new.id}는 남아 있고 옛 사진 {old_media_id}도 그대로입니다. 직접 확인이 필요합니다"
            update: dict[str, Any] = {"featured_media": new.id}
            if fig:
                figure = body_figure(new)
                if not new.url or new.url not in figure or f"wp-image-{new.id}" not in figure:
                    raise WPError(f"글 {post_id}번: 새 본문 사진 묶음이 비었거나 새 사진 주소가 없습니다. {left}")
                update["content"] = raw[:fig.start()] + figure + raw[fig.end():]
            try:
                self._request("POST", f"/posts/{int(post_id)}", json=update)
                check = self._request("GET", f"/posts/{int(post_id)}", params={"context": "edit",
                                                                               "_fields": "featured_media,content"})
            except WPError as exc:
                raise WPError(f"글 {post_id}번 저장 중 오류({exc}). {left}") from exc
            after = (check.get("content") or {}).get("raw", "")
            if int(check.get("featured_media") or 0) != new.id or (fig and (
                    new.url not in after or fig.group(0) in after
                    or re.search(rf"wp-image-{int(old_media_id)}(?!\d)", after))):
                raise WPError(f"글 {post_id}번 사진을 {new.id}로 바꾸지 못했습니다(대표 {check.get('featured_media')}). {left}")
            if on_switched:
                try:
                    on_switched(new)
                except Exception as exc:
                    raise WPError(f"글 {post_id}번은 새 사진 {new.id}로 바뀌었지만 장부 기록 실패({exc}). "
                                  f"옛 사진 {old_media_id}는 지우지 않았습니다. 장부를 직접 맞춰야 합니다") from exc
            if in_use:
                log.warning("옛 대표 사진 %s는 다른 곳에서 쓰여 지우지 않았습니다", old_media_id)
                return new, False
            try:   # 이미 바뀌고 장부도 맞췄으니 삭제 실패는 경고만(같은 이름이 남아 다음 교체는 멈춤)
                self._request("DELETE", f"/media/{int(old_media_id)}", params={"force": "true"})
            except WPError as exc:
                log.warning("글 %s번은 새 사진 %s로 바꿨지만 옛 사진 %s 삭제 실패(직접 지워야 함): %s",
                            post_id, new.id, old_media_id, exc)
                return new, False
        return new, True

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


def _old_figure(raw: str, stem: str, media_id: int) -> "re.Match[str]":
    """본문에서 이 이름(jcjob-<번호>-<순서>) 사진이 든 <figure> 하나를 찾는다. 정확히 한 곳이 아니면 WPError."""
    name = re.escape(stem) + r"(?:-\d+)?\.[A-Za-z]+"
    if len(re.findall(re.escape(stem) + r"(?![\d])", raw)) != 1:
        raise WPError(f"본문에 '{stem}' 사진이 한 곳이 아닙니다. 사람이 고쳤을 수 있어 건드리지 않습니다")
    figs = list(re.finditer(rf'<figure\b[^>]*>\s*<img\b[^>]*\bsrc="[^"]*/{name}"[^>]*>.*?</figure>',
                            raw, flags=re.S))
    if len(figs) != 1 or not re.search(rf"wp-image-{int(media_id)}(?!\d)", figs[0].group(0)) \
            or figs[0].group(0).count("<figure") != 1 or "<!-- wp:" in figs[0].group(0) \
            or re.search(rf'wp:image\s*\{{[^}}]*"id":\s*{int(media_id)}(?!\d)', raw):
        raise WPError(f"본문에서 '{stem}' 사진 묶음(대표 사진 {media_id}번)을 찾지 못했습니다. 건드리지 않습니다")
    return figs[0]
