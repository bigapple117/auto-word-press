"""텔레그램으로 발행 승인 요청을 보내고, 버튼(발행·보류·취소) 응답을 받는다.

- 새 포트를 열지 않는다: 웹훅 대신 getUpdates로 주기적으로 묻는다(long polling).
- 설정한 대화방(TELEGRAM_CHAT_ID)에서, 그 대화방 사용자가 누른 버튼만 받아들인다.
- 버튼 값에는 작업 번호·요청 번호(nonce)·비밀 서명(HMAC)을 넣어, 가짜 버튼을 거른다.
  요청 번호는 승인 요청마다 새로 만들므로, 호출하는 쪽(오케스트레이터)은 장부에 저장한 '현재 요청 번호'와
  같은 버튼만 처리해야 한다(예전 메시지의 버튼 무시).
- poll_decisions가 돌려준 다음 offset은, 받은 결정을 장부에 기록한 뒤에 넘겨야 한다
  (텔레그램은 더 큰 offset으로 물으면 이전 기록을 지운다).
- 개인 대화방 전용(그룹 대화방 번호는 거절).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import requests

from src.config import Settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
ACTIONS = {"p": "publish", "h": "hold", "c": "cancel"}
_CALLBACK = re.compile(r"^([phc]):(\d{1,12}):([0-9a-f]{8}):([0-9a-f]{16})$")
# 주제 후보 버튼: t=등록(후보 번호), m=새 후보 더 받기(회차 번호)
_TOPIC_CALLBACK = re.compile(r"^([tm]):(\d{1,12}):([0-9a-f]{16})$")
TOPIC_ACTIONS = {"t": "register", "m": "more"}
TITLE_MAX, SUMMARY_MAX, WARNING_MAX, WARNINGS_MAX = 200, 500, 300, 8
TOPIC_MESSAGE_MAX = 3900           # 텔레그램 한 메시지 4096자 한도보다 여유 있게
MAX_VIDEO_BYTES = 49_000_000        # 봇 API 업로드 한도 50MB


class TelegramError(RuntimeError):
    """텔레그램 요청 실패."""


@dataclass(frozen=True)
class Decision:
    job_id: int
    action: str            # publish / hold / cancel
    nonce: str             # 승인 요청 번호(장부의 현재 요청 번호와 비교)
    callback_id: str
    message_id: int | None


@dataclass(frozen=True)
class TopicAction:
    action: str            # register(후보 등록) / more(새 후보 더 받기)
    ref_id: int            # register: 후보 번호, more: 회차 번호
    callback_id: str
    message_id: int | None
    keyboard: tuple = ()   # 누른 메시지의 버튼 배치(등록한 버튼 하나만 바꾸려고)


@dataclass(frozen=True)
class TopicLine:
    """후보 메시지 한 줄."""
    cand_id: int
    kind: str              # new / new_year / update
    topic: str
    category: str
    interest: float | None
    reasons: Sequence[str] = ()
    warnings: Sequence[str] = ()
    hosts: Sequence[str] = ()
    news: Sequence[str] = ()
    post_link: str = ""    # 고치기 후보의 원래 글


@dataclass(frozen=True)
class ApprovalRequest:
    message_id: int
    nonce: str


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None,
                 timeout: float = 30) -> None:
        if not token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 필요합니다")
        if not re.fullmatch(r"[1-9][0-9]{0,19}", str(chat_id)):
            raise TelegramError("TELEGRAM_CHAT_ID는 개인 대화방 번호(양수)여야 합니다. 그룹 대화방은 지원하지 않습니다")
        self.base = f"{API}/bot{token}"
        self.chat_id = str(chat_id)
        self.http = session or requests.Session()
        self.timeout = timeout
        # 버튼 서명용 열쇠: 토큰에서 만든다(토큰을 아는 우리만 만들 수 있음)
        self._key = hashlib.sha256(b"jalcheck-callback:" + token.encode()).digest()

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramNotifier":
        settings.require("telegram_bot_token", "telegram_chat_id")
        return cls(settings.telegram_bot_token, settings.telegram_chat_id)

    # ----- 공통 -----
    def _call(self, method: str, timeout: float | None = None, **kwargs) -> Any:
        try:
            resp = self.http.post(f"{self.base}/{method}", timeout=timeout or self.timeout, **kwargs)
        except requests.RequestException as exc:
            # from None: 원래 예외의 주소에 봇 토큰이 들어 있다
            raise TelegramError(f"텔레그램 접속 실패: {exc.__class__.__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"텔레그램 응답 오류 {resp.status_code}") from None
        if not data.get("ok"):
            raise TelegramError(f"텔레그램 오류 {resp.status_code}: {str(data.get('description', ''))[:200]}")
        return data.get("result")

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]

    def callback_data(self, job_id: int, action_code: str, nonce: str) -> str:
        payload = f"{action_code}:{int(job_id)}:{nonce}"
        return f"{payload}:{self._sign(payload)}"          # 최대 약 40바이트(한도 64)

    def topic_callback(self, code: str, ref_id: int) -> str:
        payload = f"{code}:{int(ref_id)}"
        return f"{payload}:{self._sign(payload)}"         # 최대 약 31바이트

    def parse_topic_callback(self, data: str) -> tuple[str, int] | None:
        m = _TOPIC_CALLBACK.fullmatch(data or "")
        if not m:
            return None
        code, ref, sig = m.groups()
        if not hmac.compare_digest(sig, self._sign(f"{code}:{ref}")):
            return None
        return TOPIC_ACTIONS[code], int(ref)

    def parse_callback(self, data: str) -> tuple[int, str, str] | None:
        """(작업 번호, 행동, 요청 번호). 형식이 틀리거나 서명이 안 맞으면 None."""
        m = _CALLBACK.fullmatch(data or "")
        if not m:
            return None
        code, job, nonce, sig = m.groups()
        if not hmac.compare_digest(sig, self._sign(f"{code}:{job}:{nonce}")):
            return None
        return int(job), ACTIONS[code], nonce

    # ----- 보내기 -----
    def send_approval_request(
        self, job_id: int, title: str, summary: str, edit_url: str,
        warnings: Sequence[str] = (), video_path: Path | None = None,
        screenshots: Sequence[Path] = (),
    ) -> ApprovalRequest:
        """승인 요청 메시지(버튼 포함)를 보내고 메시지 번호를 돌려준다.

        임시 글은 로그인해야 보이므로 글 화면 캡처를 파일로 먼저 보내 텔레그램 안에서 확인하게 한다.
        """
        for shot in screenshots:
            if shot.exists():
                self.send_document(shot, caption=f"[작업 {job_id}] 글 화면 ({shot.stem.rsplit('_', 1)[-1]})")
        if video_path and video_path.exists():
            self.send_video(video_path, caption=f"[작업 {job_id}] 쇼츠 미리보기")
        def clip(text: str, limit: int) -> str:     # 서식을 만들기 전에 원문을 자른다(태그가 잘리지 않게)
            return text if len(text) <= limit else text[:limit] + "…"

        nonce = secrets.token_hex(4)
        lines = [f"<b>[작업 {int(job_id)}] 발행 승인 요청</b>", "", f"<b>{html.escape(clip(title, TITLE_MAX))}</b>",
                 html.escape(clip(summary, SUMMARY_MAX)), "",
                 f'<a href="{html.escape(edit_url, quote=True)}">워드프레스에서 열기</a> (로그인 화면이 나오면 로그인)']
        if warnings:
            lines += ["", "<b>확인할 것</b>"] + [f"- {html.escape(clip(w, WARNING_MAX))}"
                                               for w in list(warnings)[:WARNINGS_MAX]]
        text = "\n".join(lines)
        keyboard = {"inline_keyboard": [[
            {"text": "발행", "callback_data": self.callback_data(job_id, "p", nonce)},
            {"text": "보류", "callback_data": self.callback_data(job_id, "h", nonce)},
            {"text": "취소", "callback_data": self.callback_data(job_id, "c", nonce)},
        ]]}
        msg = self._call("sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                              "disable_web_page_preview": True, "reply_markup": keyboard})
        return ApprovalRequest(int(msg["message_id"]), nonce)

    def send_topic_candidates(self, header: str, lines: Sequence[TopicLine], notes: Sequence[str],
                              batch_id: int, more_left: int) -> int:
        """주제 후보 메시지 하나 + 번호 등록 버튼(2열) + [새 후보 더 받기]."""
        kind_ko = {"new": "새 주제", "new_year": "새 연도 글", "update": "기존 글 고치기"}

        def esc(t: str, n: int = 200) -> str:
            return html.escape(t if len(t) <= n else t[:n] + "…")
        blocks: list[tuple[list[str], dict | None]] = []
        for i, ln in enumerate(lines, 1):
            interest = "-" if ln.interest is None else f"{ln.interest:,.0f}"
            b = [f"<b>{i}. {esc(ln.topic, 80)}</b>",
                 f"   {kind_ko.get(ln.kind, ln.kind)} · {esc(ln.category)} · 관심도 {interest}"]
            if ln.reasons:
                b.append(f"   이유: {esc(', '.join(ln.reasons), 150)}")
            if ln.hosts:
                b.append(f"   공식 자료: {esc(', '.join(ln.hosts[:3]), 120)}")
            for n in list(ln.news)[:2]:
                b.append(f"   뉴스: {esc(n, 70)}")
            if ln.post_link:
                b.append(f'   <a href="{html.escape(ln.post_link, quote=True)}">원래 글 보기</a>')
            for w in list(ln.warnings)[:2]:
                b.append(f"   ! {esc(w, 120)}")
            # 고치기는 새 작업이 아니라 확인용(버튼 없음)
            button = (None if ln.kind == "update" else
                      {"text": f"{i}번 등록", "callback_data": self.topic_callback("t", ln.cand_id)})
            blocks.append((b, button))
        tail = []
        if not lines:
            tail.append("이번에는 기준에 맞는 새 후보가 없습니다.")
        if notes:
            tail += [""] + [f"- {esc(n, 150)}" for n in notes]
        tail += ["", "관심도: 자녀장려금 검색량을 100으로 본 비교값(검색 횟수 아님)",
                 "고른 번호를 누르면 글쓰기 대기열에 들어갑니다(발행은 따로 승인)."]

        def build(n: int) -> str:
            body = [f"<b>{esc(header)}</b>", ""] + [x for b, _ in blocks[:n] for x in b]
            if n < len(blocks):
                body.append(f"(메시지 길이 때문에 {len(blocks) - n}개는 생략)")
            return "\n".join(body + tail)

        n = len(blocks)
        text = build(n)
        while n > 0 and len(text) > TOPIC_MESSAGE_MAX:   # 서식이 깨지지 않게 후보 단위로 뺀다
            n -= 1
            text = build(n)
        buttons = [btn for _b, btn in blocks[:n] if btn]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        rows.append([{"text": f"새 후보 더 받기 (오늘 {more_left}번 남음)",
                      "callback_data": self.topic_callback("m", batch_id)}])
        msg = self._call("sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                              "disable_web_page_preview": True,
                                              "reply_markup": {"inline_keyboard": rows}})
        return int(msg["message_id"])

    def mark_topic_registered(self, message_id: int | None, keyboard: Sequence, cand_id: int, label: str) -> None:
        """등록한 후보 버튼 하나만 이름을 바꾼다(나머지 버튼은 그대로 눌러 쓸 수 있게)."""
        if message_id is None or not keyboard:
            return
        target = self.topic_callback("t", cand_id)
        rows = [[dict(b, text=label) if b.get("callback_data") == target else dict(b) for b in row]
                for row in keyboard]
        try:
            self._call("editMessageReplyMarkup", json={"chat_id": self.chat_id, "message_id": message_id,
                                                       "reply_markup": {"inline_keyboard": rows}})
        except TelegramError as exc:
            log.warning("후보 버튼 바꾸기 실패: %s", exc)

    def send_text(self, text: str) -> int:
        msg = self._call("sendMessage", json={"chat_id": self.chat_id, "text": text[:4000]})
        return int(msg["message_id"])

    def send_document(self, path: Path, caption: str = "") -> int:
        """파일 그대로 보낸다(긴 화면 캡처가 줄어들지 않게)."""
        if path.stat().st_size > MAX_VIDEO_BYTES:
            return self.send_text(f"{caption}\n(파일이 50MB를 넘어 보내지 못했습니다)")
        with path.open("rb") as f:
            msg = self._call("sendDocument", timeout=120, data={"chat_id": self.chat_id, "caption": caption[:1000]},
                             files={"document": (path.name, f, "image/png")})
        return int(msg["message_id"])

    def send_video(self, path: Path, caption: str = "") -> int:
        if path.stat().st_size > MAX_VIDEO_BYTES:
            return self.send_text(f"{caption}\n(영상이 50MB를 넘어 보내지 못했습니다)")
        with path.open("rb") as f:
            msg = self._call("sendVideo", timeout=180, data={"chat_id": self.chat_id, "caption": caption[:1000],
                                                            "supports_streaming": "true"},
                             files={"video": (path.name, f, "video/mp4")})
        return int(msg["message_id"])

    # ----- 받기 -----
    def poll_decisions(self, offset: int | None,
                       wait_seconds: int = 25) -> tuple[list[Decision | TopicAction], int | None]:
        """버튼 응답을 가져온다. (결정 목록, 다음 offset). 우리 대화방·우리 서명이 아닌 것은 버린다."""
        params: dict[str, Any] = {"timeout": wait_seconds, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        updates = self._call("getUpdates", timeout=wait_seconds + 15, json=params)
        decisions: list[Decision | TopicAction] = []
        next_offset = offset
        for u in updates:
            next_offset = int(u["update_id"]) + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            msg = cq.get("message") or {}
            chat_ok = str((msg.get("chat") or {}).get("id")) == self.chat_id
            user_ok = str((cq.get("from") or {}).get("id")) == self.chat_id
            if chat_ok and user_ok:
                topic = self.parse_topic_callback(cq.get("data", ""))
                if topic:
                    kb = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
                    decisions.append(TopicAction(topic[0], topic[1], cq.get("id", ""), msg.get("message_id"),
                                                 tuple(tuple(r) for r in kb)))
                    continue
            parsed = self.parse_callback(cq.get("data", "")) if (chat_ok and user_ok) else None
            if not parsed:
                log.warning("허용되지 않은 버튼 응답을 무시했습니다(update %s)", u.get("update_id"))
                self.answer(cq.get("id", ""), "처리할 수 없는 요청입니다")
                continue
            decisions.append(Decision(parsed[0], parsed[1], parsed[2], cq["id"], msg.get("message_id")))
        return decisions, next_offset

    def answer(self, callback_id: str, text: str) -> None:
        """버튼을 누른 사람 화면에 짧은 안내를 띄운다(실패해도 무시)."""
        if not callback_id:
            return
        try:
            self._call("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text[:190]})
        except TelegramError as exc:
            log.warning("버튼 응답 안내 실패: %s", exc)

    def mark_done(self, message_id: int | None, note: str) -> None:
        """처리한 승인 메시지의 버튼을 없애고 결과를 덧붙인다."""
        if message_id is None:
            return
        try:
            self._call("editMessageReplyMarkup", json={"chat_id": self.chat_id, "message_id": message_id,
                                                       "reply_markup": {"inline_keyboard": []}})
            self._call("sendMessage", json={"chat_id": self.chat_id, "text": note[:4000],
                                            "reply_to_message_id": message_id})
        except TelegramError as exc:
            log.warning("승인 메시지 정리 실패: %s", exc)


def find_chat_id(token: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """봇에게 메시지를 보낸 대화방 목록(처음 설정할 때 대화방 번호 찾기용)."""
    http = session or requests.Session()
    try:
        data = http.post(f"{API}/bot{token}/getUpdates", json={"timeout": 0}, timeout=30).json()
    except (requests.RequestException, ValueError):
        raise TelegramError("텔레그램 접속 실패") from None
    if not data.get("ok"):
        raise TelegramError(f"텔레그램 오류: {str(data.get('description', ''))[:200]}")
    chats = {}
    for u in data.get("result", []):
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("id") is not None:
            chats[c["id"]] = {"id": c["id"], "type": c.get("type"), "name": c.get("first_name") or c.get("title")}
    return list(chats.values())
