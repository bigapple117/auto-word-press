"""텔레그램으로 발행 승인 요청을 보내고, 버튼(발행·보류·취소) 응답을 받는다.

- 새 포트를 열지 않는다: 웹훅 대신 getUpdates로 주기적으로 묻는다(long polling).
- 설정한 대화방(TELEGRAM_CHAT_ID)에서, 그 대화방 사용자가 누른 버튼만 받아들인다.
- 버튼 값에는 작업 번호와 비밀 서명(HMAC)을 넣어, 다른 곳에서 만든 가짜 버튼을 거른다.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import requests

from src.config import Settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
ACTIONS = {"pub": "publish", "hold": "hold", "cancel": "cancel"}
MAX_VIDEO_BYTES = 49_000_000        # 봇 API 업로드 한도 50MB
MAX_TEXT = 3800                     # 메시지 4096자 한도 여유


class TelegramError(RuntimeError):
    """텔레그램 요청 실패."""


@dataclass(frozen=True)
class Decision:
    job_id: int
    action: str            # publish / hold / cancel
    callback_id: str
    message_id: int | None


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None,
                 timeout: float = 30) -> None:
        if not token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 필요합니다")
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

    def callback_data(self, job_id: int, action_code: str) -> str:
        payload = f"{action_code}:{int(job_id)}"
        return f"{payload}:{self._sign(payload)}"          # 64바이트 한도 안

    def parse_callback(self, data: str) -> tuple[int, str] | None:
        try:
            code, job, sig = data.split(":")
            job_id = int(job)
        except ValueError:
            return None
        if code not in ACTIONS or not hmac.compare_digest(sig, self._sign(f"{code}:{job_id}")):
            return None
        return job_id, ACTIONS[code]

    # ----- 보내기 -----
    def send_approval_request(
        self, job_id: int, title: str, summary: str, preview_url: str,
        warnings: Sequence[str] = (), video_path: Path | None = None,
    ) -> int:
        """승인 요청 메시지(버튼 포함)를 보내고 메시지 번호를 돌려준다. 영상이 있으면 먼저 보낸다."""
        if video_path and video_path.exists():
            self.send_video(video_path, caption=f"[작업 {job_id}] 쇼츠 미리보기")
        lines = [f"<b>[작업 {int(job_id)}] 발행 승인 요청</b>", "", f"<b>{html.escape(title)}</b>",
                 html.escape(summary), "", f'<a href="{html.escape(preview_url)}">미리보기 열기</a> (관리자 로그인 필요)']
        if warnings:
            lines += ["", "<b>확인할 것</b>"] + [f"- {html.escape(w)}" for w in warnings[:8]]
        text = "\n".join(lines)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "…"
        keyboard = {"inline_keyboard": [[
            {"text": "발행", "callback_data": self.callback_data(job_id, "pub")},
            {"text": "보류", "callback_data": self.callback_data(job_id, "hold")},
            {"text": "취소", "callback_data": self.callback_data(job_id, "cancel")},
        ]]}
        msg = self._call("sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                              "disable_web_page_preview": True, "reply_markup": keyboard})
        return int(msg["message_id"])

    def send_text(self, text: str) -> int:
        msg = self._call("sendMessage", json={"chat_id": self.chat_id, "text": text[:4000]})
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
    def poll_decisions(self, offset: int | None, wait_seconds: int = 25) -> tuple[list[Decision], int | None]:
        """버튼 응답을 가져온다. (결정 목록, 다음 offset). 우리 대화방·우리 서명이 아닌 것은 버린다."""
        params: dict[str, Any] = {"timeout": wait_seconds, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        updates = self._call("getUpdates", timeout=wait_seconds + 15, json=params)
        decisions: list[Decision] = []
        next_offset = offset
        for u in updates:
            next_offset = int(u["update_id"]) + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            msg = cq.get("message") or {}
            chat_ok = str((msg.get("chat") or {}).get("id")) == self.chat_id
            user_ok = str((cq.get("from") or {}).get("id")) == self.chat_id
            parsed = self.parse_callback(cq.get("data", ""))
            if not (chat_ok and user_ok and parsed):
                log.warning("허용되지 않은 버튼 응답을 무시했습니다(update %s)", u.get("update_id"))
                self.answer(cq.get("id", ""), "처리할 수 없는 요청입니다")
                continue
            decisions.append(Decision(parsed[0], parsed[1], cq["id"], msg.get("message_id")))
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
