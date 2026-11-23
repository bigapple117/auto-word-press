"""새 글의 '자료에서 못 찾은 숫자' 경고를 AI가 자료와 대조해 덜어 내기(pipeline.verify_figures)."""
import json

from src.pipeline import verify_figures
from src.source_fetcher import Source

WARN = "자료에서 못 찾은 숫자(확인 필요): 3.5만, 7만, 1,500만 원"
SOURCES = [Source("S1", "https://gov.example", "표", "우대지원지역 5 9 4.5 8 3.5 7 (단위: 만 원)")]


class FakeGen:
    models = ["main"]

    def __init__(self, checks, model="main", boom=None):
        self.checks, self.model, self.boom, self.prompts = checks, model, boom, []

    def structured(self, prompt, schema, system, main_only=False):
        self.prompts.append(prompt)
        if self.boom:
            raise self.boom
        return json.dumps({"checks": self.checks}, ensure_ascii=False), self.model


def c(i, verdict, why="근거"):
    return {"id": i, "verdict": verdict, "why": why}


def test_all_ok_moves_warning_to_fixes():
    gen = FakeGen([c(0, "ok", "표 3.5"), c(1, "ok", "표 7"), c(2, "ok", "한도 1,500")])
    warnings, fixes = verify_figures(gen, ["다른 경고", WARN], SOURCES, "글")
    assert warnings == ["다른 경고"]
    assert fixes == ["숫자 자료 대조 확인(AI): 3.5만 (표 3.5) / 7만 (표 7) / 1,500만 원 (한도 1,500)"]
    assert "3.5 7" in gen.prompts[0] and "1. 7만" in gen.prompts[0]


def test_wrong_and_unsure_stay_with_reason():
    gen = FakeGen([c(0, "ok"), c(1, "wrong", "자료는 8"), c(2, "unsure", "자료에 없음")])
    warnings, fixes = verify_figures(gen, [WARN], SOURCES, "글")
    assert warnings == ["자료에서 못 찾은 숫자(확인 필요): 7만, 1,500만 원",
                        "AI 대조: 7만 틀림(자료는 8) / 1,500만 원 확인 불가(자료에 없음)"]
    assert fixes == ["숫자 자료 대조 확인(AI): 3.5만 (근거)"]


def test_failures_keep_original_warning():
    cases = [FakeGen([], boom=RuntimeError("x")),          # 모델 오류
             FakeGen([c(0, "ok"), c(1, "ok"), c(2, "ok")], model="spare"),   # 예비 모델 판정은 안 믿음
             FakeGen([c(0, "ok")])]                         # 판정 수가 숫자 수와 다름
    for gen in cases:
        assert verify_figures(gen, ["다른 경고", WARN], SOURCES, "글") == (["다른 경고", WARN], [])


def test_no_figure_warning_makes_no_call():
    gen = FakeGen([], boom=AssertionError("부르면 안 됨"))
    assert verify_figures(gen, ["다른 경고"], SOURCES, "글") == (["다른 경고"], [])
    assert verify_figures(object(), [WARN], SOURCES, "글") == ([WARN], [])   # structured 없는 생성기


def test_auto_refresh_rechecks_cleared_figures():
    from src import auto_refresh
    cp = {"content": {"warnings": ["자료에서 못 찾은 숫자(확인 필요): 7만"], "cleared_figures": ["3.5만", "7만"]}}
    assert auto_refresh.figure_list(cp) == ["7만", "3.5만"]
