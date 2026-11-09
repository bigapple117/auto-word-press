# REVIEW

## 2026-11-09 nginx: 실제 방문자 IP 복원, wp-login 요청 제한, xmlrpc 차단
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 설정 내용 stdin 전달)
- 1차: CHANGES_REQUESTED — (1) 원본 직접 접속 통제 필요 (2) 제한 값 설명 부정확, 실패 횟수 제한이 아님 (3) 방문자 IP 변화 영향 점검
- 반영: 주석 수정. 원본 직접 접속 제한은 사이트 전체 영향이라 별도 승인 작업으로 분리(이번 변경이 새로 여는 경로 없음). 관리자 1명·이 사이트의 wp-login만 세므로 값 유지 후 실측
- 2차: **PASS** — 운영 반영을 막을 결함 없음. 후속 권고: 원본 접속 제한, 분산·REST 공격 한계 안내
- 검증(Claude 실측): nginx -t, 주요 화면 200·/wp-admin/ 302, xmlrpc 403, wp-login 200×11 후 429, 로그에 실제 IP, REST 인증 조회 정상
- 미검증: 관리자 비밀번호로 실제 로그인
- 원문: data/design-review/codex-nginx-login-*.txt

## 2026-11-08 자동 발행 글 분류(카테고리) 자동 선택
- 작성자: Claude / 검토자: Codex (codex exec, read-only, diff stdin 전달 — codex-review.py는 bwrap 오류로 BLOCKED)
- 1차: CHANGES_REQUESTED — (1) 기존 임시 글에서 선택 실패 시 분류를 안 보내 원래 분류가 유지되는데 경고는 '기본 분류로 저장됨'으로 잘못 안내 (2) 기존 글 갱신 실패·옛 draft 체크포인트 재개·저장된 경고 재개 테스트 없음
- 2차: **PASS** — 안내 문구 '분류를 바꾸지 않고 저장(새 글은 기본 분류)', 테스트 3개 추가 확인
- 검증: Claude 쪽 pytest 336 통과, 실제 Gemini로 기존 글 13개 분류 미리 시험 13/13 일치(저장 안 함)
- 원문: data/design-review/codex-category*.txt

## 2026-11-06 주제 자동 추천 3단계: 주 1회 실행·개편 예정 표시·연말 연도 제거
- 작성자: Claude / 검토자: Codex (codex exec, read-only, diff stdin 전달)
- 1차: CHANGES_REQUESTED — (1) 등록된 후보 재클릭 시 버튼 수정 생략 → 첫 수정 실패 시 영구 미반영 (2) '개편된다' 등 다음 해와 무관한 말만으로 다음 해 개편 판정
- 2차: **PASS** — 재클릭 때도 수정 호출(같은 글자면 텔레그램 쪽에서 건너뜀), 다음 해 표현 + 개편·변경 말이 함께 있어야 판정
- 검증: Claude 쪽 pytest 321 통과
- 원문: data/design-review/codex-topic-step3*.txt

## 2026-11-06 주제 자동 추천 2단계: 후보 장부·텔레그램 [n번 등록]/[새 후보 더 받기]
- 작성자: Claude / 검토자: Codex (codex exec, read-only, diff stdin 전달 — 셸은 bwrap 오류로 막혀 diff만으로 판단)
- 계획 검토(1단계 전): Codex·Gemini — 후보 DB 보관, 버튼 64바이트, mark_done이 버튼 전체 삭제, 트렌드 기준어, 제도명 기준 중복 등 반영
- 1차: CHANGES_REQUESTED — (1) 완성된 HTML을 글자 수로 잘라 태그가 깨짐 (2) [더 받기] 횟수 확인과 회차 생성 분리로 한도 초과 가능 (3) 전송 직후 중단 시 같은 주 재전송·같은 주제 중복 등록
- 2차: CHANGES_REQUESTED — (1) 전송 후 기록 실패를 failed로 처리해 재전송 (2) 작업 생성 후 후보 기록 전 중단 시 다른 회차 같은 주제로 중복 작업
- 3차: CHANGES_REQUESTED — 후보 0개 메시지 전송 후 중단 시 running→failed로 재전송
- 4차: **PASS** — 전송 직전 sending 기록, 멈춘 sending은 sent, running만 failed. 주간 고유 색인은 failed만 제외
- 검증: Claude 쪽 pytest 310 통과(test_topic_service 20개)
- 원문: data/design-review/codex-topic-*.txt, gemini-topic-plan.txt

## 2026-11-06 바로가기 링크: 정부 자료 2곳이 안내한 사업 누리집 첫 화면 인정(link_check)
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 변경 내용 stdin 전달)
- 참고: `codex-review.py --uncommitted`는 샌드박스 오류(bwrap: loopback: Failed RTM_NEWADDR)로 2회 BLOCKED → 붙여 넣기 방식으로 재요청
- 1차: CHANGES_REQUESTED — (1) 공식 자료 한 곳 본문 등장만으로 인정(광고·주의 예시 주소 통과 위험) (2) 리다이렉트로 외부 본문을 읽은 경우 (3) 단축 주소 (4) 조건별 분리 테스트 누락
- 2차: CHANGES_REQUESTED — (1) 공식 2곳에 같은 단축 주소가 있으면 통과 (2) 리다이렉트 테스트가 fetcher 실제 동작을 검증하지 않음
- 3차: **PASS** — 서로 다른 공식 사이트 2곳 이상에 도메인만 적힌 경우 첫 화면만 인정, 깊은·단축 주소 불가, 실제 fetch_source 가짜 네트워크 리다이렉트 테스트 (근거 src/link_check.py:93–112, 129, tests/test_content.py:434, source_fetcher.py:357·371)
- 검증: Codex 정적 검토. Claude 쪽 pytest 274 통과
- 원문: data/design-review/codex-officiallink-*.txt

## 2026-11-05 jalcheck-reading.php(목차·함께 보면 좋은 제도) + jc-article.css 추가 규칙
- 작성자: Claude / 검토자: Codex (codex exec, read-only, 파일 내용 stdin 전달)
- 참고: `codex-review.py --uncommitted`는 샌드박스 오류(bwrap: loopback: Failed RTM_NEWADDR)로 BLOCKED → 같은 범위를 내용 붙여 넣기 방식으로 재요청
- 1차: CHANGES_REQUESTED — (1) 발췌문 생성 중 처리 소모 (2) 기존 ID와 생성 ID 충돌 (3) 태그·카테고리만 바뀔 때 캐시 무효화 누락 (4) .jc-article 밖 h2 처리
- 2차: CHANGES_REQUESTED — (1) 앞부분·엔티티 ID 충돌 (2) deleted_term_relationships 누락 (3) .jc-article 뒤 h2 처리
- 3차: **PASS** — 3개 모두 해결, 새 운영 영향 문제 없음 (근거 jalcheck-reading.php:54–66, 57–62, 81–86, 117, 178–182)
- 검증: 정적 검토만(Codex). Claude 쪽 실제 검증: php -l 통과, 실제 글 본문으로 목차 생성 시험(6개 항목, 참고 자료 제외), ID 충돌·바깥 h2·짧은 글(소제목 2개) 시험 통과
- 원문: data/design-review/codex-reading*.txt

## 2026-11-07 대표 사진 중복 방지(e19e555, 11b2c16, c984d0c)
- 검토자: Codex(codex-consult.py, diff 직접 전달 — codex-review.py는 bwrap 샌드박스 오류로 BLOCKED)
- 1차: CHANGES_REQUESTED 성격 지적 5건 — 중간 실패 후 재실행 시 옛 첨부 재사용, redraft·실행기와 경쟁, 파일명만으로 삭제 안전성 불충분, 사진 선택 예약 경쟁, 실행 중 작업을 겹침 판단에서 제외
- 반영(11b2c16): 같은 이름 첨부가 옛 사진 하나가 아니면 중단, 대표 사진 변경 후 서버 재조회, 장부 기록 후 삭제, 다른 글 대표/본문에서 쓰면 삭제 안 함, 각 교체 직전 대기·진행 중 작업 있으면 중단·장부 재조회, 겹침 판단은 전체 작업
- 2차(자문 형식, 판정 생략): 유지보수 구간은 상태 조회만으로 보장 안 됨 / media·draft 두 번 저장 원자성. → draft.featured는 어디서도 안 읽어 저장 제거(한 번 저장). 유지보수 구간은 새 작업이 텔레그램 버튼으로만 생기고(주간 주제는 월요일) 실행 직전 대기·진행 0건 확인으로 운영 판단
- 미검증: 실제 WP에서 업로드 후 실패 주입(가짜 WP 테스트로만 확인)
