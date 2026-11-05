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

---

# 2026-11-02 해외 차단 사이트 대신 읽기(Gemini url_context) 검토 기록 (작성: Claude, 검토: Codex)

대상: src/remote_reader.py(신규), src/source_fetcher.py(BlockedUrlError·RelayUnavailableError·fallback), src/ai_generator.py(경고)

1차 **CHANGES_REQUESTED** → 반영
- DNS 검증 미완료 주소가 Google로 넘어감 → reader가 호출 전 _parse + _resolve_public(공인 IP만)
- 다른 URL 성공으로 통과 → retrieved_url을 요청 페이지와 대조, SDK enum SUCCESS만
- 잘린 응답 채택 → finish_reason STOP만, max_output_tokens 8192
- 대신 읽기 호출 폭주 → max_fallback_attempts=3, 400/401/403/429면 남은 주소 대신 읽기 중단
- 수용한 한계: Google 측 리다이렉트는 통제 불가(우리 내부망은 Google에서 도달 불가), AI 옮겨 적기 환각은 via=gemini 경고 + sources.json 원문 대조로 완화

2차 **PASS** (남은 실제 결함 없음)
- 검증: pytest 130개(Claude 실행), 실제 kinfa.or.kr 대신 읽기 성공·핵심 문장 원문 표기 일치, 127.0.0.1 거절. Codex는 정적 검토만

---

# 2026-11-02 Pixabay 배경 수집·영상 합성 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: src/stock_media.py(신규), src/video_renderer.py, scripts/dry_run_short.py

1차 **CHANGES_REQUESTED** → 반영
- 렌더 잠금이 폴더별이라 동시 실행 가능, ASS 작성·검증·교체가 잠금 밖 → 공용 잠금 `data/.render.lock`, 전 과정 잠금 안, 실패 시 임시 출력 삭제
- 다운로드 임시 파일명 충돌 → mkstemp 고유 임시 파일 후 replace
- 다운로드 전체 시간 미보장 → 감시 타이머로 소켓 shutdown(Pixabay CDN 고정이라 최선 노력)
- 디스크 무한 증가 → 보관함 3GB 상한·LRU 삭제(사용 중 파일 보호), 여유 3GB 미만이면 다운로드 거절
- 필터 경로 작은따옴표 → 위험 문자 포함 경로 거절, ':'만 이스케이프
- API 키가 예외 체인에 남을 수 있음 → `from None`, 응답 본문 출력 제거
- 깨진 미디어 재사용 → ffprobe 검증, 깨진 파일 삭제 후 다음 후보
2차 **CHANGES_REQUESTED** → 반영: 깨진 사진 다음 후보 시도, 블로그 사진 경로(pick_blog_images)에도 보관함 정리
3차 **PASS**
- 검증: pytest 162개(Claude 실행), 실제 Pixabay 영상으로 32초 쇼츠 재렌더(87초), 검은 띠 영상 자동 교체 확인
- 미검증: 블로그 사진 → 워드프레스 업로드 연결(wp_client 미구현)

---

# 2026-11-02 워드프레스 발행 클라이언트 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: src/wp_client.py(신규), scripts/dry_run_publish.py

1차 **CHANGES_REQUESTED** → 반영
- 조회 후 수정 사이 사람이 발행하면 임시 글로 되돌릴 수 있음 → 기존 글 수정 시 status 미전송, 발행·예약·비공개 글은 수정 거절
- 동시 실행 중복 생성 → 작업별 파일 잠금(`data/locks/wp-job-<id>.lock`)으로 조회~생성·발행·미디어 업로드 묶음
- 미디어 `jcjob-42-1`이 `-10`과 혼동 → 파일명 정확 비교(워드프레스 꼬리 `-N`만 허용)
- 검색 첫 20개만 확인 → X-WP-TotalPages로 전 페이지 확인
- 장부의 post_id 무검증 → 본문 마커 검증, 불일치 거절, 404·휴지통이면 마커로 다시 찾기. publish도 작업 소유 확인
- 재실행 시 사진·출처 불일치 → 최초 선정 사진·출처 저장 후 재사용
2차 **CHANGES_REQUESTED** → 반영: dry run의 사진 기록 파일 읽기→선정→저장을 폴더별 잠금·원자적 교체로 보호
3차 **PASS**
- 수용한 한계: 관리자가 웹에서 발행하는 순간과 자동 저장이 겹치는 극히 짧은 구간은 서버 측 원자 처리(플러그인) 없이는 막을 수 없음. 상태 미전송으로 발행 취소는 막고, 내용이 덮일 수 있는 위험만 남음. 휴지통 글을 복원하면 같은 작업 글이 둘이 될 수 있음(찾기에서 2개면 중단)
- 검증: pytest 182개(Claude 실행), 실제 워드프레스에서 같은 작업 4회 실행 → 임시 글 1개(13번)·사진 6장 유지, 발행 안 함

---

# 2026-11-02 "사람이 쓴 블로그처럼" 개편 검토 기록 (작성: Claude, 자문: Gemini·Codex, 검토: Codex)

자문(사용자 요청으로 3자 협의): Gemini — 사람 글 vs AI 글 특징·검색엔진 감점 패턴·지시문 규칙 10개 조사 / Codex — blocks 스키마, 표·본문 CSS, AI 티 자동 검사 설계. 공통 결론: 문체 지시만으로는 부족, 표·본문 중복 제거와 고정 틀 해체가 핵심.

검토 대상: src/content_schema.py, src/blog_html.py, src/ai_generator.py(check_style), scripts/apply_site_css.py
1차 **CHANGES_REQUESTED** → 반영
- apply_site_css가 읽은 뒤 덮어쓸 때 관리자 동시 변경을 덮을 수 있음 → settings 미전송, POST 직전 재조회해 바뀌었으면 중단
- 표 반복 검사가 숫자 부분 일치(100 vs 1,000)로 오탐 → 단위 포함 숫자 토큰 정확 비교, 행 이름과 값이 같은 문장에 있어야 일치
- 화면에 안 나오는 필드의 강조까지 집계 → 블록 type에 해당하는 필드만
- 표 열 5개 통과 → 2~4개
- HTML 렌더러: 직접적인 XSS 경로 없음(모든 AI 텍스트·URL 이스케이프, 클래스·id는 코드 생성)
2차 **CHANGES_REQUESTED(경미)** → 반영: 숫자 토큰 부호 포함
- 검증: pytest 195개(Claude 실행), 실제 생성 글을 임시 글 26번으로 업로드, 사이트 CSS 적용·페이지 반영 확인
- 미검증: 실제 화면 캡처(서버에 브라우저 시스템 라이브러리 없음 → 사용자 화면 확인 필요)

---

# 2026-11-03 텔레그램 승인 알림 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: src/telegram_notifier.py
1차 **CHANGES_REQUESTED** → 반영: 비ASCII 서명으로 폴링 중단(대화방·사용자 확인 후 정규식 검사·비교), 예전 요청 버튼 재사용(요청마다 nonce를 서명에 포함, Decision.nonce — 현재 요청 대조·원자적 소비는 오케스트레이터 책임으로 명시), 긴 제목 HTML 절단(원문 단계 길이 제한), 그룹 대화방(개인 대화방 전용으로 제한), offset은 장부 기록 후 넘기도록 명시
2차 **CHANGES_REQUESTED(경미)** → 반영: 정규식 fullmatch(끝 줄바꿈 우회 차단), chat_id 0·선행 0 거절
- 검증: pytest 206개(Claude 실행), 실제 봇으로 승인 요청(글 캡처·쇼츠·버튼) 전송, 버튼 8회 수신(보류·취소·발행, 서명·대화방 확인 통과, 시험이라 발행 안 함)
- 미검증: 오케스트레이터와의 nonce 대조·발행 연결(미구현)

---

# 2026-11-03 신청·조회 바로가기 링크 검증 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: src/link_check.py(신규), src/blog_html.py `_links_box`
1차 **CHANGES_REQUESTED** → 반영: 본문 주소 부분 추출로 다른 호스트 생성(%77ww…) → 덩어리 전체 검사, 전체 경로만 있어도 첫 화면 허용 → 도메인만 적힌 덩어리만 인정, 끝 이중 슬래시·세미콜론 매개변수 무시 → 경로·params 그대로 비교, '?'로 시작하는 쿼리 누락 → 포함
2차 **CHANGES_REQUESTED** → 반영: 역슬래시에서 잘려 정상 도메인 인정 → 덩어리째 거절, 정규화에서 끝 구두점 제거로 다른 주소 인정 → 제거 안 함(도메인만 적힌 경우의 끝 '.', ','만 추출 단계에서 제거)
3차 **CHANGES_REQUESTED(경미)** → 반영: |·^·백틱에서 잘림 → 보이는 ASCII 전체를 덩어리로
- 렌더링 XSS: href·class 탈출 경로 없음(1차 확인)
- 검증: pytest 219개(Claude 실행), 실제 생성 글에서 홈택스·국세청 링크 통과(지어낸 주소 0), 임시 글 31번 화면 캡처로 바로가기 상자 확인

---

# 2026-11-04 전체 자동 실행기 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: src/pipeline.py, src/orchestrator.py, scripts/jobctl.py, src/db.py(begin_publishing nonce, cancel_if_current, stale_publishing)
1차 **CHANGES_REQUESTED** → 반영
- nonce 확인과 발행 선점이 원자적이지 않음 → db.begin_publishing(nonce)·cancel_if_current가 한 트랜잭션에서 현재 승인 요청 번호 확인+상태 변경
- once가 실행기 잠금 우회 → once도 .worker.lock
- 발행 후 장부 기록 실패 시 publishing 고착 → 수신 스레드가 reconcile_publishing(180초)으로 워드프레스 실제 상태와 맞춤
- 화면 안내 실패가 offset 진행을 막음 → answer 개별 예외 처리
- 작업 획득·실패 기록 오류가 실행기 전체 종료 → 메인 루프 예외 시 30초 뒤 재시도
2차 **CHANGES_REQUESTED** → 반영: 발행 응답만 끊긴 경우 승인 대기로 되돌려 '공개됐는데 취소' 가능 → WPError 4xx만 되돌리고 불확실하면 publishing 유지+재조정, 실패 기록 실패 작업은 다음 작업 전에 재기록
3차 **PASS**
- 검증: pytest 234개(오케스트레이터 15개: 전체 흐름·재발행·nonce·보류/취소·실패 재시도·발행 실패 복구·블로그만·재시작·원자 nonce·장부 실패 재조정·불확실 발행·answer 실패·실패 기록 재시도)
- 미검증: 실제 작업 1건 처리 중(Gemini 503 재시도), 상시 실행(systemd) 미적용

---

# 2026-11-04 SEO mu-plugin 검토 기록 (작성: Claude, 검토: Codex — codex-consult 코드 전달)

대상: deploy/wp/mu-plugins/jalcheck-seo.php(→ /var/www/html/wp-content/mu-plugins/)
1차 **CHANGES_REQUESTED** → 반영: 비공개·임시 글 미리보기·비밀번호 글에서 메타 출력(공개 글만으로 제한), 작성자 타입(단일 조직 명의 계정이라 Organization 유지, url은 작성자 페이지), 별도 글 목록에서 og:url 홈 출력(is_front_page && !is_paged), SEO 플러그인 감지 확대+비활성화 필터
- 확인: 속성 이스케이프·JSON-LD </script> 차단 정상, WP7 mb_* 대체 구현 있음, 기본 코어·TT5와 중복 출력 없음
2차 **PASS**
- 검증: php -l, 실제 첫 화면(WebSite)·글(Article) 출력 확인

---

# 2026-11-04 본문 분량 지시·고쳐 쓰기 분량 유지 검토 기록 (작성: Claude, 검토: Codex — diff stdin 전달)

대상: f928902, 5dba78b, 1896162, b411a0a (templates/prompt_template.py, src/ai_generator.py, src/content_schema.py, tests/test_content.py)
1차 **BLOCKED** — 확정 버그 지적 없음. 샌드박스 오류(bwrap loopback)로 Codex가 파일 열람·시험 실행을 못 함
- Codex 미검증 2건은 Claude가 직접 확인: section_paragraphs는 문단·주의·요약만 세서 지시문 범위와 같음(src/content_schema.py:119), JSON 깨짐 시 draft의 안내를 재사용하는 경로는 시험 추가(b411a0a)로 확인
- 모델 호출을 늘리는 코드 없음(Codex도 같은 의견)
- 검증: pytest 259개 통과. 미검증: 실제 Gemini 글 생성(API 키 만료로 보류)
