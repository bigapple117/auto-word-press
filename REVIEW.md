# REVIEW

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
