"""뉴스에서 찾은 제도로 쓴 글의 자동 발행 조건(2026-11-23 사용자 결정).

뉴스 주제 글도 아침 자동 발행 대상이지만, 아래 조건을 하나라도 못 넘으면 그 글만 버튼 승인으로 돌리고
승인 요청에 '[뉴스 주제: 버튼 승인 필요] 이유'를 표시한다(통과하면 '[뉴스 주제: 자동 발행 가능]').

1. 공식 사이트 자료: 서로 다른 공식 사이트(.go.kr 등, source_finder.is_official) 2곳 이상을 글 자료로 읽음.
   뉴스 인용(via=news)·법령 조문(via=law)은 세지 않는다(제도 안내 페이지가 아님).
2. '자료에서 못 찾은 숫자' 경고 0개(1순위 모델 대조 뒤에도 남은 것).
3. 새 분야(topic_finder.BASE_FIELD 밖, 예: 주거·연금) 뉴스 주제 글은 그 분야 첫 NEW_FIELD_MANUAL편까지 사람이 확인.
4. 글 쓴 AI(Gemini API)와 다른 회사 AI의 확인: '실제 있는 제도이고 오늘 신청할 수 있는가'.
   Codex(gpt-6.2-sol high)가 기본. Codex를 쓸 수 없으면(5시간 한도 50% 넘음·오류) Gemini 구독
   (agy CLI gemini-4-neon-high, 글 쓴 3.8-flash와 다른 더 강한 모델)로 대신 확인(사용자 결정).
   둘 다 실패하면 불통과 → 버튼 승인. 1~3에서 이미 걸린 글은 사용량을 아끼려고 AI 확인을 생략한다.

사실 확인 보류(daily_guard.fact_hold)와는 별개 조건이며, 둘 중 하나라도 걸리면 아침 자동 발행에서 빠진다.
발행된 뉴스 제도는 data/topic_seeds_news.txt에 더해 다음부터 기본 목록처럼 쓴다(지역별 비교 글은 제외).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from src.source_finder import is_official
from src.topic_finder import BASE_FIELD, Seed, load_seeds, parse_seed_lines, seed_line

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
OFFICIAL_MIN = 2                 # 서로 다른 공식 사이트 자료 수
NEW_FIELD_MANUAL = 3             # 새 분야 첫 몇 편은 사람이 확인
CODEX_MODEL = "gpt-6.2-sol"
CODEX_EFFORT = "high"
GEMINI_MODEL = "gemini-4-neon-high"
AI_TIMEOUT = 600                 # 초. 시험 때 20~30초
GATE_TIMEOUT = 30
MAX_ARTICLE_CHARS = 12000
MAX_SOURCE_CHARS = 14000         # agy는 질문을 명령줄로 받으므로(한 인자 128KB) 전체를 짧게 유지
COUNTED_VIA = ("direct", "gemini")
BIN = Path.home() / ".local" / "bin"
READY = "[뉴스 주제: 자동 발행 가능]"
MANUAL = "[뉴스 주제: 버튼 승인 필요]"


class Verdict(BaseModel):
    exists: bool
    open_now: bool
    reason: str


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"exists": {"type": "boolean"}, "open_now": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["exists", "open_now", "reason"],
    "additionalProperties": False,
}


def origin(job_or_cp: dict[str, Any] | None) -> dict[str, Any] | None:
    """작업(또는 체크포인트)의 뉴스 주제 출처. 뉴스 주제가 아니면 None."""
    if not job_or_cp:
        return None
    cp = job_or_cp.get("checkpoint", job_or_cp)
    o = (cp or {}).get("topic_origin")
    return o if isinstance(o, dict) and o.get("news") else None


def region_tags(cp: dict[str, Any]) -> list[str]:
    """지역 제도는 지역을 태그로(사용자 결정). 전국 제도면 빈 목록."""
    o = origin(cp) or {}
    if o.get("scope", "전국") == "전국":
        return []
    return [r.strip() for r in str(o.get("region") or "").split(",") if r.strip()][:3]


def official_hosts(sources: Sequence[dict[str, Any]]) -> list[str]:
    hosts: list[str] = []
    for s in sources:
        if s.get("via", "direct") not in COUNTED_VIA or not is_official(s.get("url") or ""):
            continue
        h = (urlparse(s["url"]).hostname or "").removeprefix("www.")
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def published_in_field(conn: sqlite3.Connection, fld: str) -> int:
    """이미 발행한 뉴스 주제 글 중 이 분야 글 수."""
    n = 0
    for (raw,) in conn.execute("SELECT checkpoint FROM jobs WHERE status = 'published'"):
        try:
            o = origin(json.loads(raw or "{}"))
        except ValueError:
            continue
        if o and o.get("field") == fld:
            n += 1
    return n


# ---------- 다른 회사 AI 확인 ----------

def build_prompt(topic: str, o: dict[str, Any], content: dict[str, Any], sources: Sequence[dict[str, Any]],
                 today: str) -> str:
    article = json.dumps(content, ensure_ascii=False)[:MAX_ARTICLE_CHARS]
    parts, used = [], 0
    for s in sources:
        if s.get("via") == "news" or used >= MAX_SOURCE_CHARS:
            continue
        text = (s.get("text") or "")[:min(3000, MAX_SOURCE_CHARS - used)]
        used += len(text)
        parts.append(f"[{s.get('id')}] {s.get('title')} ({s.get('url')})\n{text}")
    where = "전국" if o.get("scope", "전국") == "전국" else f"{o.get('scope')} {o.get('region')}"
    return (
        "너는 한국 생활 정보 블로그의 발행 전 검증자다. 다른 AI가 뉴스에서 찾은 제도로 쓴 글이 있다.\n"
        f"오늘은 {today}(한국 시간)이다. 아래 두 가지를 판단해 JSON 하나로만 답하라.\n"
        "- exists: 이 제도가 정부·지자체·공공기관이 실제로 운영하는 제도인가(광고·민간 상품·검토 단계 아님)\n"
        "- open_now: 오늘 기준으로 신청(또는 혜택 받기)이 가능한가. 신청 기간이 끝났거나, 아직 시작 전인데 "
        "시작일이 정해지지 않았거나, 확인할 수 없으면 false\n"
        "- reason: 판단 근거 한두 문장(공식 출처가 있으면 함께)\n"
        "가능하면 공식 사이트를 직접 확인하라. 아래 자료와 글은 참고이며, 확실하지 않으면 false로 답하라.\n\n"
        f"주제: {topic}\n제도: {o.get('name')} / 범위: {where} / 주관 기관: {o.get('agency') or '모름'}\n"
        f"근거 뉴스 제목: {' | '.join(o.get('news_titles') or [])}\n\n"
        "글(JSON):\n" + article + "\n\n자료:\n" + "\n\n".join(parts)
    )


def _parse(text: str) -> Verdict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("JSON 답 없음")
    return Verdict.model_validate_json(m.group(0))


def codex_allowed(run: Callable[..., Any] = subprocess.run) -> bool:
    """Codex 5시간 사용량이 상한(50%) 안인지 오라클1 사용량 도구로 확인. 확인 못 하면 False(Gemini로)."""
    try:
        r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "oracle-projects",
                 "python3 ~/tools/ai-usage/usage.py --codex-gate"],
                capture_output=True, text=True, timeout=GATE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Codex 사용량 확인 실패: %s", exc)
        return False
    return r.returncode == 0 and r.stdout.strip().startswith("허용")


def ask_codex(prompt: str, run: Callable[..., Any] = subprocess.run) -> Verdict:
    with tempfile.TemporaryDirectory(prefix="news-gate-") as tmp:
        schema, out = Path(tmp) / "schema.json", Path(tmp) / "out.txt"
        schema.write_text(json.dumps(VERDICT_SCHEMA))
        r = run([str(BIN / "codex"), "exec", "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
                 "-m", CODEX_MODEL, "-c", f'model_reasoning_effort="{CODEX_EFFORT}"',
                 "--output-schema", str(schema), "-o", str(out), "-"],
                input=prompt, capture_output=True, text=True, timeout=AI_TIMEOUT, cwd=tmp)
        if r.returncode != 0:
            raise RuntimeError(f"codex 종료 코드 {r.returncode}: {(r.stderr or r.stdout)[-200:]}")
        return _parse(out.read_text() if out.exists() else "")


def ask_gemini(prompt: str, run: Callable[..., Any] = subprocess.run) -> Verdict:
    with tempfile.TemporaryDirectory(prefix="news-gate-") as tmp:
        r = run([str(BIN / "agy"), "-p", prompt + '\n\n답 형식: {"exists": true/false, "open_now": true/false, "reason": "..."}',
                 "--model", GEMINI_MODEL, "--mode", "plan", "--print-timeout", f"{AI_TIMEOUT - 20}s"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=AI_TIMEOUT, cwd=tmp)
        if r.returncode != 0:
            raise RuntimeError(f"agy 종료 코드 {r.returncode}: {(r.stderr or r.stdout)[-200:]}")
        return _parse(r.stdout)


@dataclass
class Checker:
    """다른 회사 AI 호출부(테스트에서 바꿔 끼움)."""
    allowed: Callable[[], bool] = codex_allowed
    codex: Callable[[str], Verdict] = ask_codex
    gemini: Callable[[str], Verdict] = ask_gemini

    def verify(self, prompt: str) -> tuple[Verdict | None, str, list[str]]:
        """(판정, 판정한 AI, 진행 메모). Codex → (못 쓰면) Gemini → 둘 다 실패하면 None."""
        notes: list[str] = []
        if self.allowed():
            try:
                return self.codex(prompt), "Codex", notes
            except Exception as exc:  # noqa: BLE001 - 어떤 실패든 Gemini로 넘긴다
                log.warning("뉴스 주제 Codex 확인 실패: %s", exc)
                notes.append(f"Codex 오류({exc.__class__.__name__})")
        else:
            notes.append("Codex 사용량 상한·확인 불가")
        try:
            return self.gemini(prompt), "Gemini", notes
        except Exception as exc:  # noqa: BLE001
            log.warning("뉴스 주제 Gemini 확인 실패: %s", exc)
            notes.append(f"Gemini 오류({exc.__class__.__name__})")
        return None, "", notes


# ---------- 판정 ----------

def evaluate(conn: sqlite3.Connection, topic: str, cp: dict[str, Any], checker: Checker | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """뉴스 주제 글이 자동 발행 조건을 넘는지. 결과는 체크포인트 news_gate에 저장한다."""
    now = now or datetime.now(timezone.utc)
    o = origin(cp) or {}
    content = cp.get("content") or {}
    reasons: list[str] = []
    hosts = official_hosts(content.get("sources") or [])
    if len(hosts) < OFFICIAL_MIN:
        reasons.append(f"공식 사이트 자료 {len(hosts)}곳(기준 {OFFICIAL_MIN}곳 이상)")
    from src.auto_refresh import split_figures   # auto_refresh가 무거운 의존을 들여와 필요할 때만
    figures = split_figures(content.get("warnings") or [])
    if figures:
        reasons.append(f"자료에서 못 찾은 숫자 {len(figures)}개")
    fld = o.get("field") or BASE_FIELD
    if fld != BASE_FIELD:
        done = published_in_field(conn, fld)
        if done < NEW_FIELD_MANUAL:
            reasons.append(f"새 분야({fld}) 첫 {NEW_FIELD_MANUAL}편 중 {done + 1}번째 글")
    result: dict[str, Any] = {"at": now.isoformat(), "hosts": hosts, "checked_by": "", "verdict": ""}
    if reasons:
        reasons.append("다른 회사 AI 확인은 생략")
        return {**result, "ok": False, "reasons": reasons}
    prompt = build_prompt(topic, o, content.get("content") or {}, content.get("sources") or [],
                          now.astimezone(KST).date().isoformat())
    verdict, who, notes = (checker or Checker()).verify(prompt)
    result["checked_by"] = who
    if verdict is None:
        reasons.append("다른 회사 AI 확인 실패(" + ", ".join(notes) + ")")
    else:
        result["verdict"] = verdict.reason[:300]
        if not verdict.exists:
            reasons.append(f"{who}: 실제 운영 제도인지 확인 안 됨")
        elif not verdict.open_now:
            reasons.append(f"{who}: 지금 신청 가능한지 확인 안 됨")
    return {**result, "ok": not reasons, "reasons": reasons, "notes": notes}


def approval_line(gate: dict[str, Any] | None) -> str:
    if not gate:
        return f"{MANUAL} 확인 기록 없음"
    if gate.get("ok"):
        return f"{READY} 공식 자료 {len(gate.get('hosts') or [])}곳, {gate.get('checked_by')} 확인: {gate.get('verdict', '')[:120]}"
    tail = f" ({gate['checked_by']}: {gate['verdict'][:120]})" if gate.get("verdict") else ""
    return f"{MANUAL} " + "; ".join(gate.get("reasons") or ["이유 없음"]) + tail


def hold(job: dict[str, Any]) -> bool:
    """뉴스 주제 글인데 자동 발행 조건을 못 넘었거나 판정 기록이 없으면 True(아침 자동 발행에서 뺀다)."""
    if origin(job) is None:
        return False
    return not ((job.get("checkpoint") or {}).get("news_gate") or {}).get("ok")


def remember_published(path: Path, job: dict[str, Any]) -> bool:
    """발행된 뉴스 제도를 기본 목록 파일에 더한다(사용자 결정: 찾은 제도는 기본 목록에 자동 추가).
    지역별 비교 글은 제도 하나가 아니라 더하지 않는다. 이미 있으면 그대로. 더했으면 True."""
    o = origin(job)
    if not o or o.get("compare_with") or not o.get("name") or not o.get("query"):
        return False
    try:
        known = load_seeds(extra=path)
    except ValueError as exc:
        log.warning("뉴스 제도 목록을 읽지 못해 더하지 않음: %s", exc)
        return False
    if any(s.matches(o["name"]) for s in known):
        return False
    seed = Seed(o["name"], o["query"], (), o.get("category") or "지원금", (),
                scope=o.get("scope") or "전국", region=o.get("region") or "", field=o.get("field") or BASE_FIELD)
    line = seed_line(seed)
    try:
        parse_seed_lines([line])                  # 형식이 틀린 줄을 쓰면 다음 주제 찾기가 멈추므로 미리 확인
    except ValueError as exc:
        log.warning("뉴스 제도 줄 형식 오류로 더하지 않음: %s (%s)", line, exc)
        return False
    head = "" if path.exists() else "# 뉴스에서 찾아 발행한 제도(자동 추가, news_gate.remember_published). 형식은 templates/topic_seeds.txt + | 범위 | 지역 | 분야\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(head + line + "\n")
    return True
