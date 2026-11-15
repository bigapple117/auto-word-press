"""하루 1편 발행 안전장치(한국 시간 기준, 2026-11-15 사용자 결정).

- 저녁 7시: 오늘 발행한 글이 없으면 알림(자동 발행 예정 글의 승인 요청 메시지에 답장 → 인용을 누르면 [발행] 버튼으로 이동)
- 밤 10시: 그때도 없으면 승인 대기 중 가장 오래된 글 1편을 자동 발행(버튼과 같은 handle_decision 사용)
- 승인 대기 글이 없으면 알림만 한다(사람이 한 번도 안 본 글을 새로 만들어 올리지 않음).
- '오늘 발행'은 워드프레스에서 직접 확인한다(관리자 화면에서 손으로 올린 글도 셈).
- 실행기 안에서 5분마다 확인. 실행기가 재시작돼도 그날 자정 전이면 놓친 단계를 이어서 한다.
  하루 상태는 data/daily_guard.json에 남겨 같은 알림·자동 발행이 두 번 일어나지 않게 한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src import db
from src.telegram_notifier import Decision, TelegramError

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
REMIND_AT = time(19, 0)          # 알림
AUTO_AT = time(22, 0)            # 자동 발행
CHECK_SECONDS = 300              # 실행기가 5분마다 확인
LOW_STOCK = 2                    # 승인 대기 글이 이 개수 이하이면 미리 채우라고 알림
MAX_TRIES = 3                    # 워드프레스가 분명히 거절하면 다음 대기 글로(최대 이만큼)


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(path)


def _hm(t: time) -> str:
    return f"{'오후' if t.hour >= 12 else '오전'} {t.hour - 12 if t.hour > 12 else t.hour}시"


class DailyGuard:
    def __init__(self, orch: "Orchestrator", state_path: Path) -> None:
        self.orch = orch
        self.state_path = state_path

    @property
    def tg(self):
        return self.orch.svc.telegram

    def published_today(self, now: datetime) -> bool:
        start = datetime.combine(now.astimezone(KST).date(), time(0), KST)
        return bool(self.orch.svc.wp.published_since(start))

    def run_if_due(self, now: datetime | None = None) -> str | None:
        """할 일이 없으면 None, 무언가 했으면 기록용 짧은 문장."""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(KST)
        if local.time() < REMIND_AT:
            return None
        today = local.date().isoformat()
        state = _load(self.state_path)
        if state.get("date") != today:
            state = {"date": today}
        auto_due = local.time() >= AUTO_AT
        if state.get("auto") or (not auto_due and state.get("reminded")):
            return None                                   # 오늘 할 일은 이미 끝남
        if self.published_today(now):                     # 워드프레스 확인 실패는 예외 → 5분 뒤 다시
            state.update(reminded=True, auto="already")
            _save(self.state_path, state)
            return "오늘 이미 발행한 글이 있음"
        if db.list_jobs(self.orch.conn, "publishing", limit=1):
            return None                                   # 누군가 막 [발행]을 누름 → 다음 확인에서 결과를 봄
        if auto_due:
            note, finished = self.auto_publish()
            state["reminded"] = True
            if finished:                                  # 결과가 불확실하면 기록하지 않고 5분 뒤 실제 상태로 다시 판단
                state["auto"] = note
        else:
            note = self.remind()
            state["reminded"] = True
        _save(self.state_path, state)
        return note

    def _stock_line(self, jobs: list[dict[str, Any]]) -> str:
        if len(jobs) > LOW_STOCK:
            return ""
        return f"\n승인 대기 글이 {len(jobs)}개 남았습니다. 주제를 미리 등록해 두세요."

    def remind(self) -> str:
        jobs = self.orch.awaiting_jobs()
        if not jobs:
            self.tg.send_text(f"[오늘의 발행] 오늘 아직 발행한 글이 없는데, 승인 대기 글도 없습니다.\n"
                              f"{_hm(AUTO_AT)}에 자동으로 올릴 글이 없으니 주제를 등록하거나 직접 발행해 주세요.")
            return "알림: 대기 글 없음"
        first = jobs[0]
        text = (f"[오늘의 발행] 오늘 아직 발행한 글이 없습니다.\n"
                f"{_hm(AUTO_AT)}까지 발행이 없으면 작업 {first['id']}번 '{first.get('title') or ''}'을(를) 자동 발행합니다.\n"
                f"다른 글을 올리려면 그 글의 [발행]을 눌러 주세요.\n{self.orch.waiting_line(jobs)}"
                f"{self._stock_line(jobs)}")
        message_id = (first["checkpoint"].get("approval") or {}).get("message_id")
        if message_id:
            try:
                self.tg.send_text(text + "\n↑ 위 인용을 누르면 이 글의 승인 요청으로 이동합니다.", reply_to=message_id)
                return f"알림: 작업 {first['id']}번 예정"
            except TelegramError as exc:          # 원래 메시지가 지워졌으면 답장 없이
                log.warning("오늘의 발행 알림 답장 실패(답장 없이 보냄): %s", exc)
        self.tg.send_text(text)
        return f"알림: 작업 {first['id']}번 예정"

    def auto_publish(self) -> tuple[str, bool]:
        """(기록용 문장, 오늘 자동 발행 단계를 끝냈는지). False면 5분 뒤 다시 판단한다."""
        jobs = self.orch.awaiting_jobs()
        if not jobs:
            self.orch._notify("[오늘의 발행] 오늘 발행한 글이 없지만 승인 대기 글이 없어 자동 발행하지 못했습니다.\n"
                              "주제를 등록해 주세요.")
            return "대기 글 없음", True
        for listed in jobs[:MAX_TRIES]:
            job = db.get_job(self.orch.conn, listed["id"])    # 그사이 [다음 글]로 요청 번호가 바뀌었을 수 있어 다시 읽음
            if not job or job["status"] != "awaiting_approval":
                return f"작업 {listed['id']}번 상태가 바뀜", False   # 누가 막 발행·취소함 → 다음 확인에서 다시 판단
            approval = job["checkpoint"].get("approval") or {}
            if not approval.get("nonce"):
                log.warning("작업 %s번: 승인 요청 번호가 없어 자동 발행에서 건너뜀", job["id"])
                continue
            note = self.orch.handle_decision(Decision(job["id"], "publish", approval["nonce"],
                                                      "auto", approval.get("message_id")))
            if note == "발행했습니다":
                left = self.orch.awaiting_jobs()
                self.orch._notify(f"[오늘의 발행] 오늘 발행한 글이 없어 작업 {job['id']}번 "
                                  f"'{job.get('title') or ''}'을(를) 자동 발행했습니다.\n"
                                  f"{self.orch.waiting_line(left)}{self._stock_line(left)}")
                return f"자동 발행: 작업 {job['id']}번", True
            if note != "발행에 실패했습니다":           # 결과 불확실·이미 처리 중 등 → 다음 글로 넘어가지 않음
                return f"작업 {job['id']}번: {note}", False
            log.warning("작업 %s번 자동 발행 거절됨, 다음 글 시도", job["id"])   # 실패 안내는 handle_decision이 보냄
        self.orch._notify("[오늘의 발행] 자동 발행에 실패했습니다. 위 안내를 확인하고 직접 [발행]을 눌러 주세요.")
        return "자동 발행 실패", True
