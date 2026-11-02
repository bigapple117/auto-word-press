# Codex review — auto-word-press

- Requested at (UTC): 2026-11-02T00:34:30.625250+00:00
- Model requested: `default`
- HEAD at request: `28ba167b39941a6a7e34983f03f13f88e7019caa`
- HANDOFF SHA-256: `e083cc65d23410bd6ab25d5028a629b155859b6e216ac3bc3c2c63323f7c6dd9`
- Invocation completed: true (not an approval verdict)
- HANDOFF unchanged during review: true

## Actual reviewer response

**판정: BLOCKED**

실행 환경 오류로 필수 파일과 Git 변경 내용을 읽지 못해 검토를 완료하지 못했습니다. 이는 코드 결함 판정이나 승인이 아닙니다.

- **차단 근거:** 읽기 명령이 모두 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` 오류로 실행에 실패했습니다.
- **시도한 확인:** `git status --short`, HEAD 확인, 작업 트리·스테이징 변경 통계, 프로젝트 `AGENTS.md`·`PROJECT.md`·`HANDOFF.md`, 공통 규칙 문서 읽기.
- **미검증:** 실제 HEAD와 지정 HEAD의 일치 여부, `git diff`·`git diff --cached` 본문, 기존 변경 제외 범위, Gemini 구현 및 관련 코드, 테스트와 동작 재현. 파일 내용을 확보하지 못해 파일:줄 단위 지적도 할 수 없습니다.
- **수행하지 않은 작업:** 파일 쓰기, 코드 수정, Git 변경, 설치, 서비스 재시작, 배포.

권장 조치는 샌드박스 실행 오류를 해결한 뒤 동일한 미커밋 변경을 다시 검토하는 것입니다. 제공된 HANDOFF 본문은 읽었지만, 디스크의 최신 내용과 비교할 수 없었습니다.

---

# 2026-11-02 src/config.py · src/db.py 검토 기록 (작성: Claude, 검토: Codex)

위 자동 검토는 Codex 격리 실행(bwrap)이 이 서버에서 실패해 **BLOCKED**. 같은 변경을 `codex-consult.py`에 diff·전체 코드를 직접 넘기는 방식으로 다시 검토함 (기준 HEAD `28ba167` + 미커밋 변경).

1차: **CHANGES_REQUESTED**
- src/db.py record_post: WP 초안 생성 후 기록 전 종료 시 재실행이 중복 초안 생성 → wp_client 단계 요구사항으로 db.py 머리 주석에 기록(글에 작업 번호 표시, 생성 전 조회). 이번 범위 밖, 미해결
- src/db.py 승인/취소 경쟁 → `publishing` 상태 + `begin_publishing()` 조건부 선점, 발행 중 취소 불가, `mark_published`는 publishing에서만 → 반영
- 실행 중 종료 작업 복구 경로 없음 → `recover_interrupted()` 추가 → 반영

2차: **CHANGES_REQUESTED**
- 기존 DB의 CHECK 제약이 갱신되지 않음 → 운영 DB 미생성 확인, `PRAGMA user_version`(SCHEMA_VERSION=1) 도입, 버전 불일치 DB는 즉시 거부 → 반영

3차: **PASS** (남은 실제 결함 없음)
- 검증: pytest 35개 통과(Claude 실행). Codex는 정적 검토만, 테스트 직접 실행 안 함
- 미검증: 실제 워드프레스·실행기와의 통합(아직 미구현)

---

# 2026-11-02 자료 수집·글 생성기 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달 방식)

대상: src/source_fetcher.py, src/content_schema.py, templates/prompt_template.py, src/ai_generator.py, src/blog_html.py

1차 **CHANGES_REQUESTED** → 반영
- DNS 재조회로 SSRF 우회 → urllib3로 검증한 IP에 직접 접속(Host·SNI·인증서는 원래 호스트), 비공인 IP 하나라도 있으면 거절, 80/443만, 계정 포함 URL 거절, 환경 프록시 미사용
- 3xx 본문이 크기 제한 없이 읽힘 → 리다이렉트 본문 읽지 않음
- 전체 시간 제한 없음 → DNS 타임아웃 + 전체 deadline(40초) + 감시 타이머
- 문자셋 없는 페이지 예외 → 받은 바이트로 charset_normalizer 추정
- URL 하나 오류로 전체 중단 → 모든 실패를 SourceError로, fetch_sources는 계속
- 사실 검사 부족 → FAQ 빈 출처 거절, 기준 문장 검사 포함, 금액·% 출처 대조(못 찾으면 경고: 표기 차이 오탐 때문에 차단 대신 사람 승인 단계에서 확인)
- 이미지·출처 링크 javascript: → http(s)만 허용
2~4차 **CHANGES_REQUESTED** → 반영: 느린 전송 시 deadline 초과(감시 타이머로 소켓 shutdown, 헤더 대기 포함, Connection: close 포함), 깨진 HTML 파서 예외 변환, 끊겨서 정상 종료처럼 보이는 잘린 본문·헤더 거부, 타이머 만료 뒤 맺어진 연결 즉시 차단(_Watch 락)
5차 **CHANGES_REQUESTED(경미)** → Claude 판단으로 알려진 한계로 수용: HTTPS에서 TCP 연결 뒤 TLS 협상에 connect 제한이 다시 적용돼 최악의 경우 전체 제한을 CONNECT_TIMEOUT(10초)만큼 초과 가능. 무한 대기 아님, 코드 주석에 기록. 함께 지적된 timeout 하한(0.1초) 제거는 반영
- 검증: pytest 86개 통과(실제 소켓 느린 전송 시험 3종 포함), 실제 국세청 페이지 수집, 실제 Gemini 생성 2회 성공(숫자 오탐 0)
- 미검증: 다양한 사이트(자바스크립트로 그리는 페이지는 수집 불가로 거절됨), 워드프레스 업로드(미구현)
