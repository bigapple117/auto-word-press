"""매일 아침 1편 자동 발행(한국 시간 기준, 2026-11-15 사용자 결정).

- 매일 오전 8시: 그날 이미 발행한 글이 있어도 승인 대기 중 가장 오래된 글 1편을 발행한다.
- 발행은 텔레그램 [발행] 버튼과 같은 handle_decision을 쓴다 → 알림도 직접 눌렀을 때와 똑같다
  (승인 요청 메시지가 '발행했습니다 + 주소'로 바뀌고, 유튜브 업로드 안내 또는 다음 글 버튼).
- 사실 확인에서 근거를 못 찾은 문장이 남은 글(fact_check.hold)과 뉴스 주제 추가 조건을 못 넘은 글(news_gate)은
  건너뛴다(사람이 [발행]을 누르면 올라감).
- 승인 대기 글이 없으면 알림만 한다(사람이 한 번도 안 본 글을 새로 만들어 올리지 않음).
- 따로(apply_refreshes): 근거 페이지 변경으로 자동 등록된 갱신 중 '확실한 정보만 바뀜' 판정을 통과한 글은
  승인 요청 다음 날 8시 이후 원래 글에 반영한다(2026-11-21 사용자 결정, auto_refresh 참고).
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

from src import auto_refresh, db, news_gate, refresh
from src.telegram_notifier import Decision

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
PUBLISH_AT = time(8, 0)          # 매일 이 시각 자동 발행
CHECK_SECONDS = 60               # 실행기가 1분마다 확인(8시 전에는 시각만 보고 끝)
MAX_TRIES = 3                    # 한 번 확인할 때 워드프레스가 분명히 거절하면 다음 대기 글로(최대 이만큼)
MAX_NEWS_RECHECKS = 2            # 아침에 다시 확인할 뉴스 주제 글 수(AI 확인 동안 버튼 처리가 멈추므로 제한)
AUTO_CALLBACK = "auto"           # 아침 자동 발행이 handle_decision에 넘기는 버튼 번호(순서 확인을 건너뜀)
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


def auto_hold(job: dict[str, Any]) -> bool:
    """아침 자동 발행에서 뺄 글: 사실 확인 보류 또는 뉴스 주제 추가 조건 불통과(news_gate)."""
    return fact_hold(job) or news_gate.hold(job)


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

    def apply_refreshes(self, now: datetime | None = None) -> str | None:
        """근거 변경으로 자동 등록된 갱신 중 '확실한 정보만 바뀜' 판정을 통과한 글을 오전 8시 이후 원래 글에 반영한다
        (auto_refresh.due: 승인 요청이 어제 이전이고 [보류]를 안 누른 글). 새 글 1편 자동 발행과는 별개.
        같은 날 같은 작업은 한 번만 시도한다(실패하면 버튼 [발행]으로)."""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(KST)
        if local.time() < PUBLISH_AT:
            return None
        today = local.date().isoformat()
        state = _load(self.state_path)
        if state.get("date") != today:
            state = {"date": today}
        tried = state.setdefault("refreshed", [])
        notes = []
        for listed in self.orch.awaiting_jobs():
            if listed["id"] in tried or not refresh.is_refresh(listed):
                continue
            job = db.get_job(self.orch.conn, listed["id"])
            if not job or not auto_refresh.due(job, now):
                continue
            approval = job["checkpoint"].get("approval") or {}
            if not approval.get("nonce"):
                continue
            tried.append(job["id"])
            _save(self.state_path, state)                 # 결과와 상관없이 오늘은 다시 시도하지 않음
            # 장부에도 '시도함'을 남겨 실패·건너뜀 뒤 다음 날 다시 자동 반영하지 않게 한다(그 뒤엔 버튼 [발행]으로만)
            db.save_checkpoint(self.orch.conn, job["id"], "auto_apply",
                               {**job["checkpoint"]["auto_apply"], "attempted": today})
            if not auto_refresh.still_same(self.orch.svc.wp, job):   # 판정 뒤 사람이 원래 글·임시 글을 고침
                self.orch._notify(f"[작업 {job['id']}] 확인한 뒤 원래 글이나 고친 글이 바뀌어 자동 반영하지 않았습니다. "
                                  "내용을 보고 직접 [발행]을 눌러 주세요.")
                notes.append(f"작업 {job['id']}번: 글이 바뀌어 건너뜀")
                continue
            note = self.orch.handle_decision(Decision(job["id"], "publish", approval["nonce"],
                                                      AUTO_CALLBACK, approval.get("message_id")))
            notes.append(f"작업 {job['id']}번: {note}")
        return "갱신 자동 반영 — " + ", ".join(notes) if notes else None

    def _only_held(self, held: list[int]) -> tuple[str, bool, None]:
        self.orch._notify("[아침 자동 발행] 승인 대기 글이 모두 사람 확인이 필요한 글(사실 확인에서 근거를 못 찾은 문장, "
                          f"또는 뉴스 주제 추가 조건 불통과)이라 자동 발행하지 않았습니다(작업 {', '.join(map(str, held))}번).\n"
                          "승인 요청의 '사실 확인'·'[뉴스 주제: 버튼 승인 필요]' 항목을 보고 직접 [발행]을 눌러 주세요.")
        return "사실 확인 보류 글만 있음", True, None

    def publish_oldest(self, rejected: list[int]) -> tuple[str, bool, int | None]:
        """(기록용 문장, 오늘 일을 끝냈는지, 결과를 지켜볼 작업 번호). 거절된 글 번호는 rejected에 더한다."""
        # 갱신 작업은 사람이 직접 승인할 때만 반영(아침 자동 발행에서 뺀다)
        waiting = [j for j in self.orch.awaiting_jobs() if j["id"] not in rejected and not refresh.is_refresh(j)]
        held = [j["id"] for j in waiting if fact_hold(j)]   # 뉴스 주제 조건은 차례가 온 글만 아래에서 다시 확인
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
        tried, tries, rechecks = False, 0, 0
        for listed in jobs:
            if tries >= MAX_TRIES:
                break
            job = db.get_job(self.orch.conn, listed["id"])    # 그사이 [다음 글]로 요청 번호가 바뀌었을 수 있어 다시 읽음
            if job and news_gate.origin(job) and news_gate.stale((job.get("checkpoint") or {}).get("news_gate")) \
                    and rechecks < MAX_NEWS_RECHECKS:        # 뉴스 주제 판정은 그날 것만 유효 → 차례가 온 글만 다시 확인
                rechecks += 1
                job = self.orch.recheck_news_gate(job)
            if job and auto_hold(job):                       # 사실 확인 보류·뉴스 주제 조건 불통과(그사이 다시 만들어졌을 수도)
                held.append(job["id"])
                continue
            if not job or job["status"] != "awaiting_approval":
                return f"작업 {listed['id']}번 상태가 바뀜", False, listed["id"]
            approval = job["checkpoint"].get("approval") or {}
            if not approval.get("nonce"):
                log.warning("작업 %s번: 승인 요청 번호가 없어 자동 발행에서 건너뜀", job["id"])
                rejected.append(job["id"])
                continue
            tried, tries = True, tries + 1
            note = self.orch.handle_decision(Decision(job["id"], "publish", approval["nonce"],
                                                      AUTO_CALLBACK, approval.get("message_id")))
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
