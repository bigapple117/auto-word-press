"""매일 아침 1편 자동 발행(한국 시간 기준, 2026-11-15 사용자 결정).

- 매일 오전 8시: 그날 이미 발행한 글이 있어도 승인 대기 중 가장 오래된 글 1편을 발행한다.
- 발행은 텔레그램 [발행] 버튼과 같은 handle_decision을 쓴다 → 알림도 직접 눌렀을 때와 똑같다
  (승인 요청 메시지가 '발행했습니다 + 주소'로 바뀌고, 유튜브 업로드 안내 또는 다음 글 버튼).
- 사실 확인에서 근거를 못 찾은 문장이 남은 글(fact_check.hold)은 건너뛴다(사람이 [발행]을 누르면 올라감).
- 승인 대기 글이 없으면 알림만 한다(사람이 한 번도 안 본 글을 새로 만들어 올리지 않음).
- 실행기 안에서 1분마다 확인. 실행기가 8시에 꺼져 있었으면 그날 켜지는 즉시 발행한다.
  하루 상태는 data/daily_guard.json에 남겨 같은 날 두 번 자동 발행하지 않게 한다.
- 발행 결과가 불확실하면(응답 끊김) 그 작업 번호를 기억해 두고, 실행기가 실제 상태를 맞춘 뒤
  올라갔으면 끝, 안 올라갔으면 같은 글을 다시 시도한다(다른 글을 하나 더 올리지 않게).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src import db
from src.telegram_notifier import Decision

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
PUBLISH_AT = time(8, 0)          # 매일 이 시각 자동 발행
CHECK_SECONDS = 60               # 실행기가 1분마다 확인(8시 전에는 시각만 보고 끝)
MAX_TRIES = 3                    # 한 번 확인할 때 워드프레스가 분명히 거절하면 다음 대기 글로(최대 이만큼)
MAX_ATTEMPTS = 3                 # 하루 자동 발행 시도 횟수 상한(장애 때 알림 반복 방지)


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(path)


def fact_hold(job: dict[str, Any]) -> bool:
    """사실 확인에서 근거를 못 찾은 문장이 남은 글(또는 확인을 못 한 글)이면 True.
    사실 확인이 생기기 전에 만든 글은 기록이 없으므로 예전처럼 자동 발행 대상이다."""
    content = ((job.get("checkpoint") or {}).get("content") or {})
    return bool((content.get("fact_check") or {}).get("hold"))


class DailyGuard:
    def __init__(self, orch: "Orchestrator", state_path: Path) -> None:
        self.orch = orch
        self.state_path = state_path

    def run_if_due(self, now: datetime | None = None) -> str | None:
        """할 일이 없으면 None, 무언가 했으면 기록용 짧은 문장."""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(KST)
        if local.time() < PUBLISH_AT:
            return None
        today = local.date().isoformat()
        state = _load(self.state_path)
        if state.get("date") != today:
            state = {"date": today}
        if state.get("done"):
            return None                                   # 오늘 자동 발행은 끝남
        trying = state.get("trying")
        if trying:                                        # 지난번 결과를 모르는 글(응답 끊김·같은 순간 직접 발행)
            job = db.get_job(self.orch.conn, trying) or {}
            status = job.get("status")
            if status == "publishing":
                return None                               # 실행기가 실제 상태를 맞출 때까지 기다림
            state.pop("trying")
            if status == "published":                     # 직접 눌러 올린 경우도 오늘 아침 1편으로 인정
                state["done"] = f"발행됨: 작업 {trying}번"
                _save(self.state_path, state)
                return state["done"]
        if state.get("attempts", 0) >= MAX_ATTEMPTS:      # 워드프레스 장애가 계속되면 알림을 반복하지 않고 멈춤
            state["done"] = "자동 발행 실패(시도 횟수 초과)"
            _save(self.state_path, state)
            self.orch._notify("[아침 자동 발행] 오늘 아침 글을 여러 번 올리려 했지만 실패했습니다. "
                              "위 안내를 확인하고 직접 [발행]을 눌러 주세요.")
            return state["done"]
        state["attempts"] = state.get("attempts", 0) + 1
        rejected = state.setdefault("rejected", [])
        note, finished, watch = self.publish_oldest(rejected)
        if finished:
            state["done"] = note
        elif watch:
            state["trying"] = watch
        _save(self.state_path, state)
        return note

    def _only_held(self, held: list[int]) -> tuple[str, bool, None]:
        self.orch._notify("[아침 자동 발행] 승인 대기 글이 모두 사실 확인에서 근거를 못 찾은 문장이 있는 글이라 "
                          f"자동 발행하지 않았습니다(작업 {', '.join(map(str, held))}번).\n"
                          "승인 요청의 '사실 확인' 항목을 보고 직접 [발행]을 눌러 주세요.")
        return "사실 확인 보류 글만 있음", True, None

    def publish_oldest(self, rejected: list[int]) -> tuple[str, bool, int | None]:
        """(기록용 문장, 오늘 일을 끝냈는지, 결과를 지켜볼 작업 번호). 거절된 글 번호는 rejected에 더한다."""
        waiting = [j for j in self.orch.awaiting_jobs() if j["id"] not in rejected]
        held = [j["id"] for j in waiting if fact_hold(j)]
        jobs = [j for j in waiting if j["id"] not in held]
        if not jobs:
            if rejected:
                self.orch._notify("[아침 자동 발행] 자동 발행에 실패했습니다. 위 안내를 확인하고 직접 [발행]을 눌러 주세요.")
                return "자동 발행 실패", True, None
            if held:
                return self._only_held(held)
            self.orch._notify("[아침 자동 발행] 승인 대기 글이 없어 오늘 아침 글을 올리지 못했습니다.\n"
                              "주제를 등록해 주세요.")
            return "대기 글 없음", True, None
        tried = False
        for listed in jobs[:MAX_TRIES]:
            job = db.get_job(self.orch.conn, listed["id"])    # 그사이 [다음 글]로 요청 번호가 바뀌었을 수 있어 다시 읽음
            if job and fact_hold(job):                       # 그사이 다시 만들어져 보류가 됐을 수 있음
                held.append(job["id"])
                continue
            if not job or job["status"] != "awaiting_approval":
                return f"작업 {listed['id']}번 상태가 바뀜", False, listed["id"]
            approval = job["checkpoint"].get("approval") or {}
            if not approval.get("nonce"):
                log.warning("작업 %s번: 승인 요청 번호가 없어 자동 발행에서 건너뜀", job["id"])
                rejected.append(job["id"])
                continue
            tried = True
            note = self.orch.handle_decision(Decision(job["id"], "publish", approval["nonce"],
                                                      "auto", approval.get("message_id")))
            if note == "발행했습니다":                    # 알림은 handle_decision이 버튼과 똑같이 보냄
                return f"자동 발행: 작업 {job['id']}번", True, None
            if note != "발행에 실패했습니다":           # 결과 불확실·같은 순간 직접 발행 → 이 글의 결과를 지켜봄
                return f"작업 {job['id']}번: {note}", False, job["id"]
            log.warning("작업 %s번 자동 발행 거절됨, 다음 글 시도", job["id"])   # 실패 안내는 handle_decision이 보냄
            rejected.append(job["id"])
        if not tried and held and not rejected:
            return self._only_held(held)
        self.orch._notify("[아침 자동 발행] 자동 발행에 실패했습니다. 위 안내를 확인하고 직접 [발행]을 눌러 주세요.")
        return "자동 발행 실패", True, None
